import json

import pytest
from pydantic import ValidationError

from app.config import V1_SAMPLE_RATE, get_settings
from app.models.config_models import STTConfig
from app.runtime import build_config_snapshot
from app.services.config_repository import (
    DEFAULT_BOT_PROFILE_ID,
    DEFAULT_FLOW_ID,
    DEFAULT_LLM_CONFIG_ID,
    DEFAULT_STT_CONFIG_ID,
    DEFAULT_TTS_CONFIG_ID,
    get_bot_profile_snapshot,
    get_default_bot_profile,
    list_bot_profiles,
)


def test_config_model_rejects_non_v1_sample_rate():
    with pytest.raises(ValidationError):
        STTConfig(id="bad_stt", sample_rate=8000)


def test_default_bot_profile_uses_stable_ids():
    settings = get_settings()

    profile = get_default_bot_profile(settings)

    assert profile.id == DEFAULT_BOT_PROFILE_ID
    assert profile.name == "Default Demo Bot"
    assert profile.flow_id == DEFAULT_FLOW_ID
    assert profile.stt_config_id == DEFAULT_STT_CONFIG_ID
    assert profile.llm_config_id == DEFAULT_LLM_CONFIG_ID
    assert profile.tts_config_id == DEFAULT_TTS_CONFIG_ID


def test_list_bot_profiles_returns_json_safe_summary():
    profiles = list_bot_profiles(get_settings())

    assert profiles == [
        {
            "id": DEFAULT_BOT_PROFILE_ID,
            "name": "Default Demo Bot",
            "description": "Seeded default bot profile for the local V2 prototype.",
            "is_active": True,
        }
    ]


def test_default_snapshot_contains_legacy_and_v2_fields_without_secrets():
    settings = get_settings()

    snapshot = build_config_snapshot(settings)

    assert snapshot["SYSTEM_PROMPT"] == settings.system_prompt
    assert snapshot["LLM_MODEL"] == settings.llm_model
    assert snapshot["LLM_TEMPERATURE"] == settings.llm_temperature
    assert snapshot["TTS_VOICE_ID"] == settings.tts_voice_id
    assert snapshot["TTS_MODEL"] == settings.tts_model
    assert snapshot["INITIAL_GREETING_PROMPT"] == settings.initial_greeting_prompt
    assert snapshot["SAMPLE_RATE"] == V1_SAMPLE_RATE
    assert snapshot["snapshot_version"] == "v2_phase1"
    assert snapshot["bot_profile"]["id"] == DEFAULT_BOT_PROFILE_ID
    assert snapshot["stt_config"]["id"] == DEFAULT_STT_CONFIG_ID
    assert snapshot["llm_config"]["id"] == DEFAULT_LLM_CONFIG_ID
    assert snapshot["tts_config"]["id"] == DEFAULT_TTS_CONFIG_ID
    assert snapshot["flow_definition"]["id"] == DEFAULT_FLOW_ID

    serialized = json.dumps(snapshot)
    assert settings.deepgram_api_key not in serialized
    assert settings.cerebras_api_key not in serialized
    assert settings.cartesia_api_key not in serialized
    assert settings.exotel_api_token not in serialized


def test_default_flow_has_expected_slots():
    snapshot = build_config_snapshot(get_settings())

    slot_ids = {slot["id"] for slot in snapshot["flow_definition"]["slots"]}

    assert {"name", "location", "role"}.issubset(slot_ids)


def test_snapshot_preserves_existing_greeting_behavior_fields():
    settings = get_settings()

    snapshot = build_config_snapshot(settings)

    assert snapshot["INITIAL_GREETING_PROMPT"] == settings.initial_greeting_prompt
    assert snapshot["INITIAL_GREETING_INSTRUCTIONS"]
    assert snapshot["INITIAL_GREETING_FALLBACK_TEXT"]
    assert snapshot["greeting_config"]["mode"] == "llm_generated"
    assert snapshot["greeting_config"]["generation_prompt"] == settings.initial_greeting_prompt
    assert snapshot["greeting_config"]["generation_instructions"] == snapshot["INITIAL_GREETING_INSTRUCTIONS"]
    assert snapshot["greeting_config"]["fallback_text"] == snapshot["INITIAL_GREETING_FALLBACK_TEXT"]


def test_missing_profile_id_uses_default_without_fallback_flag():
    snapshot = get_bot_profile_snapshot(get_settings())

    assert snapshot["profile_resolution"] == {
        "requested_profile_id": None,
        "resolved_profile_id": DEFAULT_BOT_PROFILE_ID,
        "used_fallback": False,
    }


def test_unknown_profile_id_falls_back_with_visible_resolution():
    snapshot = get_bot_profile_snapshot(get_settings(), profile_id="missing_profile")

    assert snapshot["bot_profile"]["id"] == DEFAULT_BOT_PROFILE_ID
    assert snapshot["profile_resolution"] == {
        "requested_profile_id": "missing_profile",
        "resolved_profile_id": DEFAULT_BOT_PROFILE_ID,
        "used_fallback": True,
    }


def test_snapshot_is_deep_copied_between_calls():
    settings = get_settings()
    first = build_config_snapshot(settings)
    first["bot_profile"]["name"] = "Mutated"
    first["flow_definition"]["slots"][0]["id"] = "mutated"

    second = build_config_snapshot(settings)

    assert second["bot_profile"]["name"] == "Default Demo Bot"
    assert second["flow_definition"]["slots"][0]["id"] == "name"
