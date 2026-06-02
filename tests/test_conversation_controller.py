import pytest

from app.config import get_settings
from app.runtime import build_config_snapshot
from app.services import conversation_controller
from app.services.conversation_controller import (
    validate_greeting_text,
)


def test_greeting_validation_rejects_callee_like_text():
    with pytest.raises(ValueError):
        validate_greeting_text("Yes, this works for me. Go ahead.")


def test_greeting_validation_accepts_readiness_question():
    assert (
        validate_greeting_text("Hello. I am calling for a short screening. Is now a good time?")
        == "Hello. I am calling for a short screening. Is now a good time?"
    )


def test_legacy_turn_decision_api_is_removed():
    assert not hasattr(conversation_controller, "ConversationRuntimeState")
    assert not hasattr(conversation_controller, "TurnDecision")
    assert not hasattr(conversation_controller, "decide_next_turn")
    assert not hasattr(conversation_controller, "apply_turn_decision")


def test_config_snapshot_splits_greeting_fields():
    snapshot = build_config_snapshot(get_settings())
    assert snapshot["INITIAL_GREETING_INSTRUCTIONS"]
    assert snapshot["INITIAL_GREETING_FALLBACK_TEXT"]
