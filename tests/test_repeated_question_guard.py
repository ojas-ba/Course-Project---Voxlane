from datetime import datetime, timezone
import inspect

from app.models.config_models import FallbackPolicy, FlowDefinition, FlowNode, SlotDefinition
from app.models.conversation_models import ConversationState, RuntimeAction, SlotState
from app.services import repeated_question_guard
from app.services.repeated_question_guard import RepeatedQuestionGuard


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _slot(
    slot_id: str,
    *,
    required: bool = True,
    max_attempts: int = 2,
) -> SlotDefinition:
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
        nodes=[FlowNode(id="closing", type="closing", assistant_text="Thank you.")],
        start_node_id="start",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def test_readiness_answer_then_ask_without_slot_passes_unchanged():
    guard = RepeatedQuestionGuard()
    state = ConversationState(call_sid="call_123", phase="readiness")
    action = RuntimeAction(
        action_type="answer_then_ask",
        slot_id=None,
        reason="user_question_readiness",
    )

    guarded = guard.guard_action(action=action, state=state, flow_definition=_flow())

    assert guarded.action_type == "answer_then_ask"
    assert guarded.slot_id is None
    assert guarded.reason == "user_question_readiness"


def test_collected_slot_ask_redirects_to_next_missing_slot():
    guard = RepeatedQuestionGuard()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        slots={
            "name": SlotState(slot_id="name", status="collected", value="Ojas"),
            "location": SlotState(slot_id="location", status="missing"),
        },
    )

    guarded = guard.guard_action(
        action=RuntimeAction(action_type="ask_slot", slot_id="name"),
        state=state,
        flow_definition=_flow([_slot("name"), _slot("location")]),
    )

    assert guarded.action_type == "ask_slot"
    assert guarded.slot_id == "location"
    assert guarded.reason == "guard_redirect"


def test_confirmed_and_skipped_slots_are_replaced():
    guard = RepeatedQuestionGuard()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        slots={
            "name": SlotState(slot_id="name", status="confirmed"),
            "location": SlotState(slot_id="location", status="skipped"),
            "role": SlotState(slot_id="role", status="missing"),
        },
    )

    confirmed = guard.guard_action(
        action=RuntimeAction(action_type="ask_slot", slot_id="name"),
        state=state,
        flow_definition=_flow(),
    )
    skipped = guard.guard_action(
        action=RuntimeAction(action_type="ask_slot", slot_id="location"),
        state=state,
        flow_definition=_flow(),
    )

    assert confirmed.slot_id == "role"
    assert skipped.slot_id == "role"


def test_unknown_and_non_configured_slots_are_replaced_or_closed():
    guard = RepeatedQuestionGuard()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        slots={"extra": SlotState(slot_id="extra", status="missing")},
    )

    replaced = guard.guard_action(
        action=RuntimeAction(action_type="ask_slot", slot_id="extra"),
        state=state,
        flow_definition=_flow([_slot("name")]),
    )
    closed = guard.guard_action(
        action=RuntimeAction(action_type="ask_slot", slot_id="extra"),
        state=state,
        flow_definition=_flow([]),
    )

    assert replaced.action_type == "ask_slot"
    assert replaced.slot_id == "name"
    assert closed.action_type == "close"
    assert closed.should_end_call is True


def test_same_slot_clarify_allowed_for_missing_partial_and_invalid_with_attempts_remaining():
    guard = RepeatedQuestionGuard()
    flow = _flow([_slot("name", max_attempts=2)])

    for status in ("missing", "partial", "invalid"):
        state = ConversationState(
            call_sid="call_123",
            phase="collecting",
            last_asked_slot_id="name",
            slots={"name": SlotState(slot_id="name", status=status, attempts=1)},
        )

        guarded = guard.guard_action(
            action=RuntimeAction(action_type="clarify", slot_id="name"),
            state=state,
            flow_definition=flow,
        )

        assert guarded.action_type == "clarify"
        assert guarded.slot_id == "name"


def test_same_slot_repeat_blocked_at_max_attempts():
    guard = RepeatedQuestionGuard()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        last_asked_slot_id="name",
        slots={"name": SlotState(slot_id="name", status="missing", attempts=2)},
    )

    guarded = guard.guard_action(
        action=RuntimeAction(action_type="clarify", slot_id="name"),
        state=state,
        flow_definition=_flow([_slot("name", max_attempts=2)]),
    )

    assert guarded.action_type == "close"
    assert guarded.should_end_call is True


def test_required_slot_at_max_attempts_preserves_max_attempt_close_reason():
    guard = RepeatedQuestionGuard()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        last_asked_slot_id="name",
        slots={"name": SlotState(slot_id="name", status="missing", attempts=2)},
    )

    guarded = guard.guard_action(
        action=RuntimeAction(action_type="ask_slot", slot_id="name"),
        state=state,
        flow_definition=_flow([_slot("name", required=True, max_attempts=2)]),
    )

    assert guarded.action_type == "close"
    assert guarded.should_end_call is True
    assert guarded.reason == "required_slot_max_attempts"


def test_required_slot_at_max_attempts_closes_even_when_next_slot_is_missing():
    guard = RepeatedQuestionGuard()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        last_asked_slot_id="name",
        slots={
            "name": SlotState(slot_id="name", status="missing", attempts=2),
            "location": SlotState(slot_id="location", status="missing"),
        },
    )

    guarded = guard.guard_action(
        action=RuntimeAction(action_type="ask_slot", slot_id="name"),
        state=state,
        flow_definition=_flow(
            [
                _slot("name", required=True, max_attempts=2),
                _slot("location", required=True, max_attempts=2),
            ]
        ),
    )

    assert guarded.action_type == "close"
    assert guarded.should_end_call is True
    assert guarded.reason == "required_slot_max_attempts"
    assert guarded.slot_id is None


def test_repeated_ask_slot_for_only_remaining_slot_normalizes_to_clarify():
    guard = RepeatedQuestionGuard()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        last_asked_slot_id="name",
        slots={"name": SlotState(slot_id="name", status="missing", attempts=1)},
    )

    guarded = guard.guard_action(
        action=RuntimeAction(action_type="ask_slot", slot_id="name"),
        state=state,
        flow_definition=_flow([_slot("name", max_attempts=2)]),
    )

    assert guarded.action_type == "clarify"
    assert guarded.slot_id == "name"
    assert guarded.reason == "guard_reprompt"


def test_repeated_ask_slot_redirects_to_another_missing_slot():
    guard = RepeatedQuestionGuard()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        last_asked_slot_id="name",
        slots={
            "name": SlotState(slot_id="name", status="missing", attempts=1),
            "location": SlotState(slot_id="location", status="missing"),
        },
    )

    guarded = guard.guard_action(
        action=RuntimeAction(action_type="ask_slot", slot_id="name"),
        state=state,
        flow_definition=_flow([_slot("name"), _slot("location")]),
    )

    assert guarded.action_type == "ask_slot"
    assert guarded.slot_id == "location"


def test_answer_then_ask_for_active_user_question_can_return_to_same_slot():
    guard = RepeatedQuestionGuard()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        last_asked_slot_id="location",
        slots={"location": SlotState(slot_id="location", status="missing", attempts=1)},
    )

    guarded = guard.guard_action(
        action=RuntimeAction(
            action_type="answer_then_ask",
            slot_id="location",
            reason="user_question",
        ),
        state=state,
        flow_definition=_flow([_slot("location")]),
    )

    assert guarded.action_type == "answer_then_ask"
    assert guarded.slot_id == "location"


def test_close_refusal_stays_close_and_does_not_redirect_to_slot():
    guard = RepeatedQuestionGuard()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        slots={"name": SlotState(slot_id="name", status="missing")},
    )

    guarded = guard.guard_action(
        action=RuntimeAction(action_type="close", should_end_call=False, reason="refusal"),
        state=state,
        flow_definition=_flow([_slot("name")]),
    )

    assert guarded.action_type == "close"
    assert guarded.reason == "refusal"
    assert guarded.should_end_call is True
    assert guarded.slot_id is None


def test_closing_or_ended_state_converts_asking_action_to_close():
    guard = RepeatedQuestionGuard()
    flow = _flow([_slot("name")])

    for phase in ("closing", "ended"):
        state = ConversationState(call_sid="call_123", phase=phase)
        guarded = guard.guard_action(
            action=RuntimeAction(action_type="ask_slot", slot_id="name"),
            state=state,
            flow_definition=flow,
        )

        assert guarded.action_type == "close"
        assert guarded.should_end_call is True


def test_extra_state_slots_are_ignored():
    guard = RepeatedQuestionGuard()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        slots={
            "extra": SlotState(slot_id="extra", status="missing"),
            "name": SlotState(slot_id="name", status="collected", value="Ojas"),
        },
    )

    guarded = guard.guard_action(
        action=RuntimeAction(action_type="ask_slot", slot_id="extra"),
        state=state,
        flow_definition=_flow([_slot("name")]),
    )

    assert guarded.action_type == "close"
    assert guarded.should_end_call is True


def test_guard_uses_fallback_policy_max_attempts_only_for_attempt_checks():
    guard = RepeatedQuestionGuard()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        last_asked_slot_id="name",
        slots={"name": SlotState(slot_id="name", status="missing", attempts=2)},
    )

    guarded = guard.guard_action(
        action=RuntimeAction(action_type="clarify", slot_id="name"),
        state=state,
        flow_definition=_flow([_slot("name", max_attempts=0)]),
        fallback_policy=FallbackPolicy(max_attempts=3, refusal_text="Do not speak me."),
    )

    assert guarded.action_type == "clarify"
    assert guarded.slot_id == "name"


def test_guard_does_not_mutate_conversation_state():
    guard = RepeatedQuestionGuard()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        active_slot_id="name",
        last_asked_slot_id="name",
        slots={
            "name": SlotState(slot_id="name", status="collected", value="Ojas"),
            "location": SlotState(slot_id="location", status="missing"),
        },
        debug={"before": True},
    )
    before = state.model_dump(mode="json")

    guard.guard_action(
        action=RuntimeAction(action_type="ask_slot", slot_id="name"),
        state=state,
        flow_definition=_flow([_slot("name"), _slot("location")]),
    )

    assert state.model_dump(mode="json") == before


def test_guard_module_has_no_provider_or_pipecat_imports():
    source = inspect.getsource(repeated_question_guard)

    assert "import pipecat" not in source.lower()
    assert "from pipecat" not in source.lower()
    assert "import cerebras" not in source.lower()
    assert "import deepgram" not in source.lower()
    assert "import cartesia" not in source.lower()
