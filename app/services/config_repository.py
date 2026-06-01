"""In-memory seeded behavior config repository for Phase 1."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.config import Settings, V1_SAMPLE_RATE
from app.models.config_models import (
    BotProfile,
    CallConfigSnapshot,
    FallbackPolicy,
    FlowDefinition,
    FlowNode,
    GreetingConfig,
    LLMConfig,
    STTConfig,
    SlotDefinition,
    TTSConfig,
)

SNAPSHOT_VERSION = "v2_phase1"
DEFAULT_BOT_PROFILE_ID = "default_demo_bot"
DEFAULT_STT_CONFIG_ID = "default_deepgram_flux"
DEFAULT_LLM_CONFIG_ID = "default_cerebras_llm"
DEFAULT_TTS_CONFIG_ID = "default_cartesia_tts"
DEFAULT_FLOW_ID = "default_demo_flow"


def _seed_timestamp() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _looks_like_spoken_greeting(text: str) -> bool:
    normalized = text.strip().lower()
    readiness_cues = ("good time", "available", "ready", "continue", "speak", "talk")
    return "." in normalized and ("?" in normalized or any(cue in normalized for cue in readiness_cues))


def _resolve_greeting_settings(settings: Settings) -> tuple[str, str]:
    legacy_greeting = settings.initial_greeting_prompt.strip()
    fallback_text = settings.initial_greeting_fallback_text.strip()
    instructions = settings.initial_greeting_instructions.strip()

    if not fallback_text:
        fallback_text = (
            legacy_greeting
            if _looks_like_spoken_greeting(legacy_greeting)
            else "Hello. I am calling for a short conversation. Is now a good time?"
        )
    if not instructions:
        instructions = (
            "Generate a brief personalized outbound opening and ask whether now is a good time."
            if _looks_like_spoken_greeting(legacy_greeting)
            else legacy_greeting
        )

    return instructions, fallback_text


def _build_default_components(
    settings: Settings,
) -> tuple[BotProfile, STTConfig, LLMConfig, TTSConfig, FlowDefinition]:
    now = _seed_timestamp()
    instructions, fallback_text = _resolve_greeting_settings(settings)
    greeting_config = GreetingConfig(
        mode="llm_generated",
        static_text=fallback_text,
        generation_prompt=settings.initial_greeting_prompt,
        generation_instructions=instructions,
        generation_timeout_secs=settings.outbound_greeting_prep_timeout_secs,
        fallback_text=fallback_text,
    )
    fallback_policy = FallbackPolicy()

    stt_config = STTConfig(id=DEFAULT_STT_CONFIG_ID)
    llm_config = LLMConfig(
        id=DEFAULT_LLM_CONFIG_ID,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
    )
    tts_config = TTSConfig(
        id=DEFAULT_TTS_CONFIG_ID,
        model=settings.tts_model,
        voice_id=settings.tts_voice_id,
    )

    slots = [
        SlotDefinition(
            id="name",
            label="Name",
            description="The caller or candidate name.",
            question="What is your name?",
        ),
        SlotDefinition(
            id="location",
            label="Location",
            description="The caller or candidate location.",
            question="Where are you located?",
        ),
        SlotDefinition(
            id="role",
            label="Role",
            description="The role or position relevant to this call.",
            question="What role are you interested in?",
        ),
    ]
    nodes = [
        FlowNode(
            id="start",
            type="readiness_check",
            assistant_text=fallback_text,
            next_node_id="collect_name",
        ),
        FlowNode(
            id="collect_name",
            type="slot_collection",
            slot_id="name",
            on_success_node_id="collect_location",
        ),
        FlowNode(
            id="collect_location",
            type="slot_collection",
            slot_id="location",
            on_success_node_id="collect_role",
        ),
        FlowNode(
            id="collect_role",
            type="slot_collection",
            slot_id="role",
            on_success_node_id="closing",
        ),
        FlowNode(
            id="closing",
            type="closing",
            assistant_text="Thank you. That is all I needed for now.",
        ),
    ]
    flow_definition = FlowDefinition(
        id=DEFAULT_FLOW_ID,
        name="Default Demo Flow",
        version="1.0",
        description="Default Phase 1 demo flow for future slot collection.",
        use_case_type="interview",
        slots=slots,
        nodes=nodes,
        start_node_id="start",
        created_at=now,
        updated_at=now,
    )

    bot_profile = BotProfile(
        id=DEFAULT_BOT_PROFILE_ID,
        name="Default Demo Bot",
        description="Seeded default bot profile for the local V2 prototype.",
        system_prompt=settings.system_prompt,
        flow_id=flow_definition.id,
        stt_config_id=stt_config.id,
        llm_config_id=llm_config.id,
        tts_config_id=tts_config.id,
        greeting_config=greeting_config,
        fallback_policy=fallback_policy,
        created_at=now,
        updated_at=now,
    )

    return bot_profile, stt_config, llm_config, tts_config, flow_definition


def _json_dict(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


def get_default_bot_profile(settings: Settings) -> BotProfile:
    """Return the deterministic default bot profile."""

    bot_profile, _, _, _, _ = _build_default_components(settings)
    return bot_profile


def list_bot_profiles(settings: Settings) -> list[dict[str, Any]]:
    """Return JSON-safe profile summaries available in Phase 1."""

    profile = get_default_bot_profile(settings)
    return [
        {
            "id": profile.id,
            "name": profile.name,
            "description": profile.description,
            "is_active": profile.is_active,
        }
    ]


def get_bot_profile_snapshot(
    settings: Settings,
    profile_id: str | None = None,
) -> dict[str, Any]:
    """Return a deep-copied JSON-safe call config snapshot."""

    requested_profile_id = profile_id.strip() if isinstance(profile_id, str) and profile_id.strip() else None
    used_fallback = requested_profile_id is not None and requested_profile_id != DEFAULT_BOT_PROFILE_ID

    bot_profile, stt_config, llm_config, tts_config, flow_definition = _build_default_components(settings)
    instructions, fallback_text = _resolve_greeting_settings(settings)

    profile_resolution = {
        "requested_profile_id": requested_profile_id,
        "resolved_profile_id": bot_profile.id,
        "used_fallback": used_fallback,
    }

    snapshot = CallConfigSnapshot(
        SYSTEM_PROMPT=settings.system_prompt,
        LLM_MODEL=llm_config.model,
        LLM_TEMPERATURE=llm_config.temperature,
        TTS_VOICE_ID=tts_config.voice_id,
        TTS_MODEL=tts_config.model,
        INITIAL_GREETING_PROMPT=settings.initial_greeting_prompt,
        INITIAL_GREETING_INSTRUCTIONS=instructions,
        INITIAL_GREETING_FALLBACK_TEXT=fallback_text,
        SAMPLE_RATE=V1_SAMPLE_RATE,
        snapshot_version=SNAPSHOT_VERSION,
        bot_profile=_json_dict(bot_profile),
        stt_config=_json_dict(stt_config),
        llm_config=_json_dict(llm_config),
        tts_config=_json_dict(tts_config),
        greeting_config=_json_dict(bot_profile.greeting_config),
        fallback_policy=_json_dict(bot_profile.fallback_policy),
        flow_definition=_json_dict(flow_definition),
        profile_resolution=profile_resolution,
    )

    return deepcopy(snapshot.model_dump(mode="json"))
