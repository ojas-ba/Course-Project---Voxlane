from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import get_settings
from app.models.runtime_config_models import GLOBAL_CONFIG_ID, RuntimeConfig
from app.runtime import build_config_snapshot
from app.services import runtime_config
from app.services.runtime_config import (
    build_effective_snapshot,
    load_runtime_config,
    save_runtime_config,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    runtime_config._cached_config = None
    yield
    runtime_config._cached_config = None


def _fake_db(find_one_result):
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value=find_one_result)
    coll.update_one = AsyncMock(return_value=None)
    db = MagicMock()
    db.__getitem__.return_value = coll
    return db, coll


def test_default_config_shape():
    cfg = RuntimeConfig.default(get_settings())
    assert cfg.stt.model == "flux-general-multi"
    assert cfg.stt.language_hints == ["en", "hi"]
    assert cfg.llm.provider == "cerebras"
    assert cfg.llm.model == "gpt-oss-120b"
    assert cfg.tts.provider == "cartesia"
    assert cfg.memory.recent_turn_pairs == 6


def test_effective_snapshot_shape():
    cfg = RuntimeConfig.default(get_settings())
    snap = build_effective_snapshot(cfg)
    assert snap["snapshot_version"] == "v3"
    assert snap["SAMPLE_RATE"] == 16000
    assert snap["SYSTEM_PROMPT"] == cfg.prompt.system_prompt
    assert snap["GREETING_PROMPT"] == cfg.prompt.greeting_prompt
    assert snap["TTS_VOICE_ID"] == cfg.tts.voice_id
    assert snap["runtime_config"]["llm"]["model"] == cfg.llm.model


@pytest.mark.asyncio
async def test_load_seeds_default_when_missing():
    db, coll = _fake_db(find_one_result=None)
    with patch("app.services.runtime_config.get_database", return_value=db):
        cfg = await load_runtime_config(get_settings())
    assert cfg.stt.model == "flux-general-multi"
    coll.update_one.assert_awaited_once()
    args, kwargs = coll.update_one.call_args
    assert args[0] == {"_id": GLOBAL_CONFIG_ID}
    assert kwargs.get("upsert") is True
    assert runtime_config.get_cached_runtime_config() is not None


@pytest.mark.asyncio
async def test_load_returns_existing_doc():
    doc = RuntimeConfig.default(get_settings()).model_dump(mode="json")
    doc["_id"] = GLOBAL_CONFIG_ID
    doc["llm"]["model"] = "custom-model"
    db, coll = _fake_db(find_one_result=doc)
    with patch("app.services.runtime_config.get_database", return_value=db):
        cfg = await load_runtime_config(get_settings())
    assert cfg.llm.model == "custom-model"
    coll.update_one.assert_not_called()


@pytest.mark.asyncio
async def test_save_upserts_and_caches():
    cfg = RuntimeConfig.default(get_settings())
    cfg.tts.voice_id = "voice-y"
    db, coll = _fake_db(find_one_result=None)
    with patch("app.services.runtime_config.get_database", return_value=db):
        returned = await save_runtime_config(cfg)
    coll.update_one.assert_awaited_once()
    assert returned.tts.voice_id == "voice-y"
    assert runtime_config.get_cached_runtime_config().tts.voice_id == "voice-y"


def test_build_config_snapshot_falls_back_to_default_without_cache():
    runtime_config._cached_config = None
    snap = build_config_snapshot(get_settings())
    assert snap["snapshot_version"] == "v3"
    assert snap["runtime_config"]["stt"]["model"] == "flux-general-multi"
