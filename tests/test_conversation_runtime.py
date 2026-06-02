from datetime import datetime, timezone
import inspect
import json

import pytest

from app.models.config_models import FlowDefinition, FlowNode, SlotDefinition
from app.models.conversation_models import (
    ConversationState,
    RuntimeAction,
    SlotState,
    TurnInput,
)
from app.services import conversation_runtime
from app.services.conversation_runtime import ConversationRuntime
from app.services.flow_runner import SimpleFlowRunner


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _slot(slot_id: str, *, required: bool = True, max_attempts: int = 2) -> SlotDefinition:
    return SlotDefinition(
        id=slot_id,
        label=slot_id.title(),
        required=required,
        question=f"What is your {slot_id}?",
        max_attempts=max_attempts,
    )


def _flow(slots: list[SlotDefinition] | None = None) -> FlowDefinition:
    return FlowDefinition(
        id="flow_123",
        name="Test Flow",
        version="1.0",
        use_case_type="interview",
        slots=slots if slots is not None else [_slot("name"), _slot("location"), _slot("role")],
        nodes=[
            FlowNode(
                id="closing",
                type="closing",
                assistant_text="Thank you. That is all I needed for now.",
            )
        ],
        start_node_id="start",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def _state(**overrides) -> ConversationState:
    values = {"call_sid": "call_123", "phase": "collecting"}
    values.update(overrides)
    return ConversationState(**values)


def _turn(
    text: str,
    *,
    call_sid: str = "call_123",
    stream_sid: str | None = None,
    phase: str = "collecting",
    turn_index: int = 1,
) -> TurnInput:
    return TurnInput(
        call_sid=call_sid,
        stream_sid=stream_sid,
        turn_index=turn_index,
        text=text,
        normalized_text=" ".join(text.split()),
        phase=phase,
        source="manual_test",
        committed_at=BASE_TIME,
    )


class StaticRunner:
    def __init__(self, action: RuntimeAction) -> None:
        self.action = action
        self.handle_calls = 0
        self.initialize_calls = 0
        self.initializer = SimpleFlowRunner()

    def initialize_state(self, *, state, flow_definition):
        self.initialize_calls += 1
        return self.initializer.initialize_state(state=state, flow_definition=flow_definition)

    def handle_turn(self, *, state, turn, flow_definition, fallback_policy=None):
        self.handle_calls += 1
        return self.action.model_copy(deep=True)


class RaisingRunner(StaticRunner):
    def __init__(self) -> None:
        super().__init__(RuntimeAction(action_type="fallback"))

    def handle_turn(self, *, state, turn, flow_definition, fallback_policy=None):
        raise AssertionError("FlowRunner should not be called")


class StaticGuard:
    def __init__(self, action: RuntimeAction) -> None:
        self.action = action

    def guard_action(self, *, action, state, flow_definition, fallback_policy=None):
        return self.action.model_copy(deep=True)


def test_runtime_initializes_configured_slots():
    runtime = ConversationRuntime(state=ConversationState(call_sid="call_123"), flow_definition=_flow())

    snapshot = runtime.snapshot_state()

    assert set(snapshot.slots) == {"name", "location", "role"}
    assert snapshot.active_node_id == "start"


def test_existing_state_and_collected_slots_are_preserved():
    state = _state(
        slots={"name": SlotState(slot_id="name", status="collected", value="Ojas")},
    )

    runtime = ConversationRuntime(state=state, flow_definition=_flow())

    snapshot = runtime.snapshot_state()
    assert snapshot.slots["name"].status == "collected"
    assert snapshot.slots["name"].value == "Ojas"
    assert snapshot.slots["location"].status == "missing"


def test_runtime_owns_deep_copy_of_initial_state():
    state = _state(slots={"name": SlotState(slot_id="name", value="Ojas")})

    runtime = ConversationRuntime(state=state, flow_definition=_flow([_slot("name")]))
    state.phase = "closing"
    state.slots["name"].value = "Mutated"

    snapshot = runtime.snapshot_state()
    assert snapshot.phase == "collecting"
    assert snapshot.slots["name"].value == "Ojas"


def test_mutating_original_state_after_construction_does_not_mutate_runtime():
    original = _state(slots={"name": SlotState(slot_id="name", attempts=1)})
    runtime = ConversationRuntime(state=original, flow_definition=_flow([_slot("name")]))

    original.slots["name"].attempts = 99
    original.last_user_text = "outside mutation"

    snapshot = runtime.snapshot_state()
    assert snapshot.slots["name"].attempts == 1
    assert snapshot.last_user_text is None


def test_set_phase_normalizes_legacy_phase_and_updates_state():
    runtime = ConversationRuntime(state=_state(phase="collecting"), flow_definition=_flow())
    before = runtime.snapshot_state().updated_at

    runtime.set_phase("awaiting_readiness")

    snapshot = runtime.snapshot_state()
    assert snapshot.phase == "readiness"
    assert snapshot.updated_at >= before


def test_unknown_phase_raises_value_error():
    runtime = ConversationRuntime(state=_state(), flow_definition=_flow())

    with pytest.raises(ValueError, match="Unknown conversation phase"):
        runtime.set_phase("TASK")


def test_turn_with_mismatched_call_sid_raises_without_turn_count_increment():
    runtime = ConversationRuntime(state=_state(), flow_definition=_flow())

    with pytest.raises(ValueError, match="call_sid"):
        runtime.handle_turn(_turn("hello", call_sid="call_other"))

    assert runtime.snapshot_state().turn_count == 0


def test_turn_with_conflicting_stream_sid_raises_without_turn_count_increment():
    runtime = ConversationRuntime(state=_state(stream_sid="stream_a"), flow_definition=_flow())

    with pytest.raises(ValueError, match="stream_sid"):
        runtime.handle_turn(_turn("hello", stream_sid="stream_b"))

    snapshot = runtime.snapshot_state()
    assert snapshot.stream_sid == "stream_a"
    assert snapshot.turn_count == 0


def test_first_stream_sid_is_attached():
    runtime = ConversationRuntime(
        state=_state(),
        flow_definition=_flow([_slot("name")]),
        flow_runner=StaticRunner(RuntimeAction(action_type="acknowledge", assistant_text_hint="Thanks.")),
    )

    runtime.handle_turn(_turn("hello", stream_sid="stream_123"))

    assert runtime.snapshot_state().stream_sid == "stream_123"


def test_greeting_first_committed_turn_does_not_get_stuck_in_greeting():
    runtime = ConversationRuntime(state=_state(phase="greeting"), flow_definition=_flow([_slot("name")]))

    result = runtime.handle_turn(_turn("yes", phase="greeting"))

    assert result.state.phase == "collecting"
    assert result.action.action_type == "ask_slot"
    assert result.action.slot_id == "name"


def test_turn_phase_does_not_override_non_greeting_runtime_phase():
    runtime = ConversationRuntime(
        state=_state(phase="collecting"),
        flow_definition=_flow([_slot("name")]),
        flow_runner=StaticRunner(RuntimeAction(action_type="acknowledge", assistant_text_hint="Thanks.")),
    )

    result = runtime.handle_turn(_turn("yes", phase="readiness"))

    assert result.state.phase == "collecting"
    assert result.action.action_type == "acknowledge"


def test_readiness_yes_asks_first_slot_and_moves_to_collecting():
    runtime = ConversationRuntime(state=_state(phase="readiness"), flow_definition=_flow([_slot("name")]))

    result = runtime.handle_turn(_turn("yes", phase="readiness"))

    assert result.action.action_type == "ask_slot"
    assert result.action.slot_id == "name"
    assert result.state.phase == "collecting"
    assert result.state.active_slot_id == "name"
    assert result.state.last_asked_slot_id == "name"
    assert result.state.slots["name"].attempts == 1


def test_unclear_readiness_clarifies_and_stays_readiness_without_attempt_increment():
    runtime = ConversationRuntime(state=_state(phase="readiness"), flow_definition=_flow([_slot("name")]))

    result = runtime.handle_turn(_turn("hello", phase="readiness"))

    assert result.action.action_type == "clarify"
    assert result.action.slot_id is None
    assert result.action.reason == "readiness_unclear"
    assert result.state.phase == "readiness"
    assert result.state.active_slot_id is None
    assert result.state.last_asked_slot_id is None
    assert result.state.slots["name"].attempts == 0


def test_refusal_closes_sets_closing_and_increments_refusal_count():
    runtime = ConversationRuntime(state=_state(), flow_definition=_flow([_slot("name")]))

    result = runtime.handle_turn(_turn("I am not doing this"))

    assert result.action.action_type == "close"
    assert result.action.reason == "refusal"
    assert result.should_end_call is True
    assert result.state.phase == "closing"
    assert result.state.refusal_count == 1


def test_first_final_ask_slot_increments_attempts_once():
    runtime = ConversationRuntime(
        state=_state(slots={"name": SlotState(slot_id="name")}),
        flow_definition=_flow([_slot("name")]),
        flow_runner=StaticRunner(RuntimeAction(action_type="ask_slot", slot_id="name")),
    )

    result = runtime.handle_turn(_turn("unclear"))

    assert result.state.slots["name"].attempts == 1
    assert result.state.slots["name"].last_asked_at_turn == 1


def test_final_slot_bound_clarify_increments_attempts_once():
    runtime = ConversationRuntime(
        state=_state(
            last_asked_slot_id="name",
            slots={"name": SlotState(slot_id="name", status="missing", attempts=1)},
        ),
        flow_definition=_flow([_slot("name", max_attempts=3)]),
        flow_runner=StaticRunner(RuntimeAction(action_type="clarify", slot_id="name")),
    )

    result = runtime.handle_turn(_turn("unclear"))

    assert result.action.action_type == "clarify"
    assert result.state.slots["name"].attempts == 2
    assert result.state.active_slot_id == "name"


def test_answer_then_ask_does_not_increment_attempts_but_tracks_slot():
    runtime = ConversationRuntime(
        state=_state(slots={"name": SlotState(slot_id="name", attempts=1)}),
        flow_definition=_flow([_slot("name")]),
        flow_runner=StaticRunner(
            RuntimeAction(action_type="answer_then_ask", slot_id="name", reason="user_question")
        ),
    )

    result = runtime.handle_turn(_turn("Why do you need this?"))

    assert result.action.action_type == "answer_then_ask"
    assert result.state.phase == "collecting"
    assert result.state.active_slot_id == "name"
    assert result.state.last_asked_slot_id == "name"
    assert result.state.slots["name"].attempts == 1
    assert result.state.slots["name"].last_asked_at_turn == 1


def test_answer_then_ask_readiness_no_slot_keeps_readiness():
    runtime = ConversationRuntime(state=_state(phase="readiness"), flow_definition=_flow([_slot("name")]))

    result = runtime.handle_turn(_turn("What is this for?", phase="readiness"))

    assert result.action.action_type == "answer_then_ask"
    assert result.action.slot_id is None
    assert result.state.phase == "readiness"
    assert result.state.active_slot_id is None
    assert result.state.last_asked_slot_id is None
    assert result.state.slots["name"].attempts == 0


def test_guard_redirect_side_effects_update_redirected_slot():
    runtime = ConversationRuntime(
        state=_state(
            last_asked_slot_id="name",
            slots={
                "name": SlotState(slot_id="name", status="missing", attempts=1),
                "location": SlotState(slot_id="location", status="missing"),
            },
        ),
        flow_definition=_flow([_slot("name"), _slot("location")]),
        flow_runner=StaticRunner(RuntimeAction(action_type="ask_slot", slot_id="name")),
    )

    result = runtime.handle_turn(_turn("unclear"))

    assert result.action.reason == "guard_redirect"
    assert result.action.slot_id == "location"
    assert result.state.active_slot_id == "location"
    assert result.state.slots["location"].attempts == 1
    assert result.state.slots["name"].attempts == 1


def test_post_guard_invalid_ask_converts_to_safe_close():
    runtime = ConversationRuntime(
        state=_state(slots={"name": SlotState(slot_id="name")}),
        flow_definition=_flow([_slot("name")]),
        flow_runner=StaticRunner(RuntimeAction(action_type="ask_slot", slot_id="name")),
        repeated_question_guard=StaticGuard(RuntimeAction(action_type="ask_slot", slot_id="missing")),
    )

    result = runtime.handle_turn(_turn("hello"))

    assert result.action.action_type == "close"
    assert result.action.reason == "runtime_invalid_action"
    assert result.should_end_call is True
    assert result.state.phase == "closing"
    assert result.debug["sanity_replaced_action"] is True


def test_post_guard_invalid_no_slot_answer_then_ask_converts_to_safe_close():
    runtime = ConversationRuntime(
        state=_state(phase="readiness"),
        flow_definition=_flow([_slot("name")]),
        flow_runner=StaticRunner(
            RuntimeAction(
                action_type="answer_then_ask",
                slot_id=None,
                reason="user_question_readiness",
            )
        ),
        repeated_question_guard=StaticGuard(
            RuntimeAction(action_type="answer_then_ask", slot_id=None, reason="bad_no_slot")
        ),
    )

    result = runtime.handle_turn(_turn("What is this for?", phase="readiness"))

    assert result.action.action_type == "close"
    assert result.action.reason == "runtime_invalid_action"
    assert result.should_end_call is True
    assert result.state.phase == "closing"
    assert result.debug["sanity_replaced_action"] is True


def test_late_turn_after_closing_does_not_call_flow_runner_and_returns_close():
    runner = RaisingRunner()
    runtime = ConversationRuntime(
        state=_state(phase="closing"),
        flow_definition=_flow([_slot("name")]),
        flow_runner=runner,
    )

    result = runtime.handle_turn(_turn("hello"))

    assert result.action.action_type == "close"
    assert result.action.reason == "already_closing"
    assert result.should_end_call is True
    assert runner.handle_calls == 0


def test_late_turn_after_ended_does_not_call_flow_runner_and_returns_close():
    runner = RaisingRunner()
    runtime = ConversationRuntime(
        state=_state(phase="ended"),
        flow_definition=_flow([_slot("name")]),
        flow_runner=runner,
    )

    result = runtime.handle_turn(_turn("hello"))

    assert result.action.action_type == "close"
    assert result.action.reason == "already_ended"
    assert result.should_end_call is True
    assert result.state.phase == "ended"
    assert runner.handle_calls == 0


def test_close_does_not_increment_attempts():
    runtime = ConversationRuntime(
        state=_state(slots={"name": SlotState(slot_id="name", attempts=1)}),
        flow_definition=_flow([_slot("name")]),
        flow_runner=StaticRunner(RuntimeAction(action_type="close", should_end_call=True)),
    )

    result = runtime.handle_turn(_turn("stop"))

    assert result.state.slots["name"].attempts == 1
    assert result.state.phase == "closing"


def test_fallback_increments_fallback_count_without_attempt_increment():
    runtime = ConversationRuntime(
        state=_state(slots={"name": SlotState(slot_id="name")}),
        flow_definition=_flow([_slot("name")]),
        flow_runner=StaticRunner(
            RuntimeAction(action_type="fallback", assistant_text_hint="Could you repeat that?")
        ),
    )

    result = runtime.handle_turn(_turn("unclear"))

    assert result.state.fallback_count == 1
    assert result.state.slots["name"].attempts == 0


def test_required_slot_max_attempts_closes():
    runtime = ConversationRuntime(
        state=_state(slots={"name": SlotState(slot_id="name", attempts=2)}),
        flow_definition=_flow([_slot("name", max_attempts=2)]),
    )

    result = runtime.handle_turn(_turn("unclear"))

    assert result.action.action_type == "close"
    assert result.action.reason == "required_slot_max_attempts"
    assert result.should_end_call is True
    assert result.state.phase == "closing"


def test_all_slots_collected_closes_with_should_end_call_true():
    runtime = ConversationRuntime(
        state=_state(
            slots={
                "name": SlotState(slot_id="name", status="collected", value="Ojas"),
                "location": SlotState(slot_id="location", status="collected", value="Bangalore"),
            },
        ),
        flow_definition=_flow([_slot("name"), _slot("location")]),
    )

    result = runtime.handle_turn(_turn("thanks"))

    assert result.action.action_type == "close"
    assert result.action.reason == "no_askable_slots"
    assert result.should_end_call is True


def test_result_state_snapshot_is_stable_after_runtime_state_mutates():
    runtime = ConversationRuntime(state=_state(phase="readiness"), flow_definition=_flow([_slot("name")]))

    first = runtime.handle_turn(_turn("yes", phase="readiness"))
    runtime.handle_turn(_turn("Ojas"))

    assert first.state.turn_count == 1
    assert first.state.slots["name"].attempts == 1
    assert runtime.snapshot_state().turn_count == 2


def test_runtime_result_model_dump_json_serializes_and_debug_is_json_safe():
    runtime = ConversationRuntime(state=_state(phase="readiness"), flow_definition=_flow([_slot("name")]))

    result = runtime.handle_turn(_turn("yes", phase="readiness"))
    dumped = result.model_dump(mode="json")

    json.dumps(dumped)
    json.dumps(result.debug)
    assert dumped["state"]["phase"] == "collecting"
    assert dumped["debug"]["final_action"]["action_type"] == "ask_slot"


def test_two_runtime_instances_are_isolated():
    first = ConversationRuntime(state=_state(call_sid="call_a", phase="readiness"), flow_definition=_flow())
    second = ConversationRuntime(state=_state(call_sid="call_b", phase="readiness"), flow_definition=_flow())

    first.handle_turn(_turn("yes", call_sid="call_a", phase="readiness"))
    second.handle_turn(_turn("hello", call_sid="call_b", phase="readiness"))

    first_snapshot = first.snapshot_state()
    second_snapshot = second.snapshot_state()
    assert first_snapshot.call_sid == "call_a"
    assert second_snapshot.call_sid == "call_b"
    assert first_snapshot.phase == "collecting"
    assert second_snapshot.phase == "readiness"
    assert first_snapshot.active_slot_id == "name"
    assert second_snapshot.active_slot_id is None
    assert first_snapshot.last_asked_slot_id == "name"
    assert second_snapshot.last_asked_slot_id is None
    assert first_snapshot.slots["name"].attempts == 1
    assert second_snapshot.slots["name"].attempts == 0
    assert first_snapshot.last_user_text == "yes"
    assert second_snapshot.last_user_text == "hello"
    assert first_snapshot.fallback_count == 0
    assert second_snapshot.fallback_count == 0


def test_conversation_runtime_has_no_module_level_mutable_runtime_state():
    mutable_values = {
        name: value
        for name, value in vars(conversation_runtime).items()
        if not name.startswith("__") and isinstance(value, (dict, list, set))
    }

    assert mutable_values == {}
    assert not hasattr(conversation_runtime, "ACTIVE_SESSIONS")


def test_runtime_module_has_no_provider_or_pipecat_imports():
    source = inspect.getsource(conversation_runtime)

    assert "import pipecat" not in source.lower()
    assert "from pipecat" not in source.lower()
    assert "import cerebras" not in source.lower()
    assert "import deepgram" not in source.lower()
    assert "import cartesia" not in source.lower()
    assert "active_sessions" not in source.lower()
