import pytest

from app.config import get_settings
from app.runtime import build_config_snapshot
from app.services import conversation_controller
from app.services.conversation_controller import validate_greeting_text


def test_greeting_validation_accepts_clean_open_ended_greeting():
    text = "Hi there, this is your assistant — how can I help you today?"
    assert validate_greeting_text(text) == text


def test_greeting_validation_rejects_empty():
    with pytest.raises(ValueError):
        validate_greeting_text("   ")


def test_greeting_validation_rejects_simulated_speaker_label():
    with pytest.raises(ValueError):
        validate_greeting_text("Assistant: Hello there, how can I help?")


def test_legacy_turn_decision_api_is_removed():
    assert not hasattr(conversation_controller, "ConversationRuntimeState")
    assert not hasattr(conversation_controller, "TurnDecision")
    assert not hasattr(conversation_controller, "decide_next_turn")
    assert not hasattr(conversation_controller, "apply_turn_decision")


def test_config_snapshot_provides_v3_greeting_fields():
    snapshot = build_config_snapshot(get_settings())
    assert snapshot["SYSTEM_PROMPT"]
    assert snapshot["GREETING_PROMPT"]
    assert snapshot["runtime_config"]["llm"]["model"]
