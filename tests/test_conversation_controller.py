import pytest

from app.config import get_settings
from app.runtime import build_config_snapshot
from app.services.conversation_controller import (
    ConversationRuntimeState,
    TurnDecision,
    apply_turn_decision,
    safe_turn_decision,
    validate_greeting_text,
    validate_turn_decision,
)


def test_greeting_validation_rejects_callee_like_text():
    with pytest.raises(ValueError):
        validate_greeting_text("Yes, this works for me. Go ahead.")


def test_greeting_validation_accepts_readiness_question():
    assert (
        validate_greeting_text("Hello. I am calling for a short screening. Is now a good time?")
        == "Hello. I am calling for a short screening. Is now a good time?"
    )


def test_turn_decision_rejects_simulated_user_text():
    with pytest.raises(ValueError):
        validate_turn_decision(
            {
                "phase": "task",
                "assistant_text": "What role are you applying for? User: Product Manager.",
                "user_intent": "answer",
                "should_end_call": False,
            }
        )


def test_turn_decision_rejects_multiple_questions():
    with pytest.raises(ValueError):
        validate_turn_decision(
            {
                "phase": "task",
                "assistant_text": "What is your name? Where are you located?",
                "user_intent": "answer",
                "should_end_call": False,
            }
        )


def test_safe_turn_decision_shape():
    decision = safe_turn_decision(phase="readiness")
    assert decision.phase == "readiness"
    assert decision.user_intent == "unclear"
    assert decision.assistant_text


def test_apply_turn_decision_moves_phase():
    state = ConversationRuntimeState(phase="awaiting_readiness")
    apply_turn_decision(
        state,
        TurnDecision(
            phase="task",
            assistant_text="Great. What is your full name?",
            user_intent="ready",
        ),
    )
    assert state.phase == "task"


def test_config_snapshot_splits_greeting_fields():
    snapshot = build_config_snapshot(get_settings())
    assert snapshot["INITIAL_GREETING_INSTRUCTIONS"]
    assert snapshot["INITIAL_GREETING_FALLBACK_TEXT"]
