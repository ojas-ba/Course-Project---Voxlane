"""Single global Mongo-backed runtime configuration (V3).

There is exactly one config document (``_id="global"``) in the
``runtime_config`` collection. MongoDB is required: if it is unavailable the
app fails clearly at startup, and an outbound call is refused if the config
cannot be loaded. There is no fallback to ``.env`` for behavior config.

A process-local cache is kept so the synchronous ``build_config_snapshot``
(used on the inbound path) can build a snapshot without awaiting Mongo. The
cache is refreshed at startup and whenever the config is saved.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.config import Settings, V1_SAMPLE_RATE
from app.database import get_database
from app.models.runtime_config_models import GLOBAL_CONFIG_ID, RuntimeConfig

RUNTIME_CONFIG_COLLECTION = "runtime_config"

# Default opening line used ONLY for the inbound/default pipeline path.
# The outbound path always speaks a pre-generated LLM greeting and never
# falls back to this text.
DEFAULT_INBOUND_OPENING_TEXT = "Hello, you're connected to the assistant. How can I help you today?"

_cached_config: RuntimeConfig | None = None


def get_cached_runtime_config() -> RuntimeConfig | None:
    """Return the last-loaded runtime config, or None if not yet loaded."""

    return _cached_config


def _set_cache(config: RuntimeConfig) -> RuntimeConfig:
    global _cached_config
    _cached_config = config
    return config


def _parse_doc(doc: dict[str, Any]) -> RuntimeConfig:
    payload = {k: v for k, v in doc.items() if k != "_id"}
    return RuntimeConfig.model_validate(payload)


async def load_runtime_config(settings: Settings) -> RuntimeConfig:
    """Load the global runtime config from Mongo, seeding a default if absent.

    Raises whatever the Mongo driver raises if the database is unreachable;
    callers on the outbound path must treat that as "do not place the call".
    """

    db = get_database()
    doc = await db[RUNTIME_CONFIG_COLLECTION].find_one({"_id": GLOBAL_CONFIG_ID})
    if doc is None:
        config = RuntimeConfig.default(settings)
        await db[RUNTIME_CONFIG_COLLECTION].update_one(
            {"_id": GLOBAL_CONFIG_ID},
            {"$set": config.model_dump(mode="json")},
            upsert=True,
        )
        logger.info("Seeded default runtime_config document")
        return _set_cache(config)
    return _set_cache(_parse_doc(doc))


async def seed_runtime_config_if_missing(settings: Settings) -> RuntimeConfig:
    """Ensure a global runtime config exists; populate the cache. Startup use."""

    return await load_runtime_config(settings)


async def save_runtime_config(config: RuntimeConfig) -> RuntimeConfig:
    """Persist the global runtime config and refresh the cache."""

    db = get_database()
    await db[RUNTIME_CONFIG_COLLECTION].update_one(
        {"_id": GLOBAL_CONFIG_ID},
        {"$set": config.model_dump(mode="json")},
        upsert=True,
    )
    logger.info("Saved runtime_config document")
    return _set_cache(config)


def build_effective_snapshot(config: RuntimeConfig) -> dict[str, Any]:
    """Build the immutable per-call effective config snapshot.

    Includes the full structured ``runtime_config`` plus flattened legacy keys
    that the pipeline/greeting code reads directly.
    """

    return {
        "runtime_config": config.model_dump(mode="json"),
        "SYSTEM_PROMPT": config.prompt.system_prompt,
        "GREETING_PROMPT": config.prompt.greeting_prompt,
        "LLM_MODEL": config.llm.model,
        "LLM_TEMPERATURE": config.llm.temperature,
        "TTS_VOICE_ID": config.tts.voice_id,
        "TTS_MODEL": config.tts.model,
        "SAMPLE_RATE": V1_SAMPLE_RATE,
        "INITIAL_GREETING_FALLBACK_TEXT": DEFAULT_INBOUND_OPENING_TEXT,
        "snapshot_version": "v3",
    }
