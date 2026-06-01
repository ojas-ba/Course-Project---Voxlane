import json

import pytest

from app.models.conversation_models import (
    ConversationState,
    RuntimeAction,
    RuntimeResult,
    SlotState,
    normalize_conversation_phase,
)


def _is_timezone_aware(timestamp):
    return timestamp.tzinfo is not None and timestamp.utcoffset() is not None


def test_slot_state_defaults():
    state = SlotState(slot_id="name")

    assert state.slot_id == "name"
    assert state.status == "missing"
    assert state.value is None
    assert state.raw_value is None
    assert state.attempts == 0
    assert state.last_asked_at_turn is None
    assert state.collected_at_turn is None


def test_conversation_state_defaults_and_independent_dicts():
    first = ConversationState(call_sid="call_123")
    second = ConversationState(call_sid="call_456")

    first.slots["name"] = SlotState(slot_id="name", status="collected", value="Ojas")
    first.debug["source"] = "test"

    assert first.phase == "greeting"
    assert first.stream_sid is None
    assert first.turn_count == 0
    assert first.fallback_count == 0
    assert first.refusal_count == 0
    assert second.slots == {}
    assert second.debug == {}


def test_conversation_state_timestamps_are_timezone_aware():
    state = ConversationState(call_sid="call_123")

    assert _is_timezone_aware(state.started_at)
    assert _is_timezone_aware(state.updated_at)


def test_conversation_state_model_dump_json_is_serializable():
    state = ConversationState(
        call_sid="call_123",
        stream_sid="stream_456",
        phase="collecting",
        active_slot_id="name",
        slots={"name": SlotState(slot_id="name", status="collected", value="Ojas")},
        debug={"safe": True},
    )

    dumped = state.model_dump(mode="json")

    assert dumped["slots"]["name"]["status"] == "collected"
    assert dumped["slots"]["name"]["value"] == "Ojas"
    assert isinstance(dumped["started_at"], str)
    json.dumps(dumped)


def test_runtime_action_can_represent_close_with_should_end_call():
    action = RuntimeAction(
        action_type="close",
        should_end_call=True,
        reason="all_required_slots_collected",
    )

    assert action.action_type == "close"
    assert action.should_end_call is True
    assert action.reason == "all_required_slots_collected"


def test_runtime_result_serializes_cleanly():
    action = RuntimeAction(action_type="ask_slot", slot_id="name")
    state = ConversationState(call_sid="call_123")
    result = RuntimeResult(
        action=action,
        assistant_text="What is your name?",
        state=state.model_copy(deep=True),
        user_transcript_text="hello",
        assistant_transcript_text="What is your name?",
        debug={"path": "test"},
    )

    dumped = result.model_dump(mode="json")

    assert dumped["action"]["action_type"] == "ask_slot"
    assert dumped["state"]["call_sid"] == "call_123"
    assert dumped["assistant_text"] == "What is your name?"
    json.dumps(dumped)


def test_runtime_result_snapshot_copy_remains_stable_after_original_state_mutates():
    original_state = ConversationState(
        call_sid="call_123",
        slots={"name": SlotState(slot_id="name", status="collected", value="Ojas")},
    )
    result = RuntimeResult(
        action=RuntimeAction(action_type="close", should_end_call=True),
        assistant_text="Thank you.",
        should_end_call=True,
        state=original_state.model_copy(deep=True),
    )

    original_state.slots["name"].value = "Rahul"
    original_state.debug["mutated"] = True

    assert result.state.slots["name"].value == "Ojas"
    assert "mutated" not in result.state.debug


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("awaiting_readiness", "readiness"),
        (" task ", "collecting"),
    ],
)
def test_normalize_conversation_phase_maps_legacy_names(phase, expected):
    assert normalize_conversation_phase(phase) == expected


@pytest.mark.parametrize(
    "phase",
    [
        "greeting",
        "readiness",
        "collecting",
        "answering_question",
        "closing",
        "ended",
    ],
)
def test_normalize_conversation_phase_passes_valid_new_phases(phase):
    assert normalize_conversation_phase(phase) == phase


def test_normalize_conversation_phase_raises_for_unknown_phase():
    with pytest.raises(ValueError, match="Unknown conversation phase: unknown"):
        normalize_conversation_phase("unknown")


@pytest.mark.parametrize("phase", ["TASK", "Collecting"])
def test_normalize_conversation_phase_raises_for_case_mismatched_phase(phase):
    with pytest.raises(ValueError, match=f"Unknown conversation phase: {phase}"):
        normalize_conversation_phase(phase)
