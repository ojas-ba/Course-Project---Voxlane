from datetime import datetime, timezone
import inspect

from app.models.config_models import FallbackPolicy, FlowDefinition, SlotDefinition
from app.models.conversation_models import ConversationState, SlotState, TurnInput
from app.services import flow_runner
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
        nodes=[],
        start_node_id="start",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def _turn(text: str, *, phase: str = "collecting", turn_index: int = 1) -> TurnInput:
    return TurnInput(
        call_sid="call_123",
        turn_index=turn_index,
        text=text,
        normalized_text=" ".join(text.split()),
        phase=phase,
        source="manual_test",
        committed_at=BASE_TIME,
    )


def test_state_initializes_from_configured_flow_slots():
    state = ConversationState(call_sid="call_123")
    runner = SimpleFlowRunner()

    returned = runner.initialize_state(state=state, flow_definition=_flow())

    assert returned is state
    assert set(state.slots) == {"name", "location", "role"}
    assert all(slot_state.status == "missing" for slot_state in state.slots.values())


def test_initialize_state_guarantees_all_configured_slots_and_preserves_existing_state():
    state = ConversationState(
        call_sid="call_123",
        slots={"name": SlotState(slot_id="name", status="collected", value="Ojas")},
    )
    runner = SimpleFlowRunner()

    runner.initialize_state(state=state, flow_definition=_flow())

    assert set(state.slots) == {"name", "location", "role"}
    assert state.slots["name"].value == "Ojas"
    assert state.slots["location"].status == "missing"
    assert state.slots["role"].status == "missing"


def test_only_configured_slots_are_created_and_extra_state_slot_remains_extra():
    state = ConversationState(
        call_sid="call_123",
        slots={"extra": SlotState(slot_id="extra", value="old")},
    )
    runner = SimpleFlowRunner()

    runner.initialize_state(state=state, flow_definition=_flow([_slot("name")]))

    assert set(state.slots) == {"extra", "name"}
    assert state.slots["extra"].value == "old"


def test_initialize_state_sets_active_node_but_not_active_slot():
    state = ConversationState(call_sid="call_123")
    runner = SimpleFlowRunner()

    runner.initialize_state(state=state, flow_definition=_flow())

    assert state.active_node_id == "start"
    assert state.active_slot_id is None


def test_readiness_yes_moves_to_collecting_and_asks_first_slot():
    state = ConversationState(call_sid="call_123", phase="readiness")
    runner = SimpleFlowRunner()

    action = runner.handle_turn(state=state, turn=_turn("yes", phase="readiness"), flow_definition=_flow())

    assert state.phase == "collecting"
    assert state.active_slot_id == "name"
    assert action.action_type == "ask_slot"
    assert action.slot_id == "name"
    assert action.reason == "readiness_confirmed"


def test_random_hello_does_not_start_collection_from_readiness():
    state = ConversationState(call_sid="call_123", phase="readiness")
    runner = SimpleFlowRunner()

    action = runner.handle_turn(state=state, turn=_turn("hello", phase="readiness"), flow_definition=_flow())

    assert state.phase == "readiness"
    assert state.active_slot_id is None
    assert action.action_type == "clarify"
    assert action.reason == "readiness_unclear"


def test_refusal_closes_before_extraction():
    state = ConversationState(call_sid="call_123", phase="collecting")
    runner = SimpleFlowRunner()

    action = runner.handle_turn(
        state=state,
        turn=_turn("I don't want to continue. My name is Ojas."),
        flow_definition=_flow(),
    )

    assert state.phase == "closing"
    assert action.action_type == "close"
    assert action.should_end_call is True
    assert action.reason == "refusal"
    assert state.slots["name"].status == "missing"


def test_short_stop_refusal_uses_phrase_boundary_matching():
    state = ConversationState(call_sid="call_123", phase="collecting")
    runner = SimpleFlowRunner()

    action = runner.handle_turn(state=state, turn=_turn("please stop"), flow_definition=_flow())

    assert state.phase == "closing"
    assert action.action_type == "close"
    assert action.reason == "refusal"


def test_stop_is_not_blindly_matched_inside_unrelated_text():
    state = ConversationState(call_sid="call_123", phase="collecting")
    runner = SimpleFlowRunner()

    action = runner.handle_turn(state=state, turn=_turn("bus stop near my house"), flow_definition=_flow())

    assert state.phase != "closing"
    assert action.action_type == "ask_slot"
    assert action.reason != "refusal"


def test_one_sentence_collects_name_location_and_role():
    state = ConversationState(call_sid="call_123", phase="collecting")
    runner = SimpleFlowRunner()

    action = runner.handle_turn(
        state=state,
        turn=_turn("I am Ojas from Bangalore, applying for backend."),
        flow_definition=_flow(),
    )

    assert state.slots["name"].value == "Ojas"
    assert state.slots["location"].value == "Bangalore"
    assert state.slots["role"].value == "backend"
    assert action.action_type == "close"
    assert action.should_end_call is True


def test_anti_overcapture_name_and_location():
    state = ConversationState(call_sid="call_123", phase="collecting")
    runner = SimpleFlowRunner()

    updates = runner.extract_slot_updates(
        turn=_turn("I am Ojas from Bangalore"),
        state=runner.initialize_state(state=state, flow_definition=_flow()),
        flow_definition=_flow(),
    )

    assert updates["name"] == "Ojas"
    assert updates["location"] == "Bangalore"


def test_anti_overcapture_name_and_role():
    state = ConversationState(call_sid="call_123", phase="collecting")
    runner = SimpleFlowRunner()

    updates = runner.extract_slot_updates(
        turn=_turn("I am Ojas, applying for backend"),
        state=runner.initialize_state(state=state, flow_definition=_flow()),
        flow_definition=_flow(),
    )

    assert updates["name"] == "Ojas"
    assert updates["role"] == "backend"


def test_i_am_applying_for_role_does_not_collect_name():
    state = ConversationState(call_sid="call_123", phase="collecting")
    runner = SimpleFlowRunner()

    updates = runner.extract_slot_updates(
        turn=_turn("I am applying for backend"),
        state=runner.initialize_state(state=state, flow_definition=_flow()),
        flow_definition=_flow(),
    )

    assert updates == {"role": "backend"}


def test_location_and_role_boundary_extraction():
    state = ConversationState(call_sid="call_123", phase="collecting")
    runner = SimpleFlowRunner()

    updates = runner.extract_slot_updates(
        turn=_turn("from Bangalore applying for backend"),
        state=runner.initialize_state(state=state, flow_definition=_flow()),
        flow_definition=_flow(),
    )

    assert updates["location"] == "Bangalore"
    assert updates["role"] == "backend"


def test_ambiguous_input_does_not_guess():
    state = ConversationState(call_sid="call_123", phase="collecting")
    runner = SimpleFlowRunner()

    updates = runner.extract_slot_updates(
        turn=_turn("maybe the usual one"),
        state=runner.initialize_state(state=state, flow_definition=_flow()),
        flow_definition=_flow(),
    )

    assert updates == {}


def test_question_with_active_slot_returns_answer_then_ask():
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        active_slot_id="location",
    )
    runner = SimpleFlowRunner()
    runner.initialize_state(state=state, flow_definition=_flow())

    action = runner.handle_turn(
        state=state,
        turn=_turn("Why do you need my location?"),
        flow_definition=_flow(),
    )

    assert state.active_slot_id == "location"
    assert action.action_type == "answer_then_ask"
    assert action.slot_id == "location"
    assert state.slots["location"].attempts == 0


def test_question_with_no_active_slot_in_readiness_does_not_guess():
    state = ConversationState(call_sid="call_123", phase="readiness")
    runner = SimpleFlowRunner()

    action = runner.handle_turn(
        state=state,
        turn=_turn("What is this for?", phase="readiness"),
        flow_definition=_flow(),
    )

    assert action.action_type == "answer_then_ask"
    assert action.slot_id is None
    assert action.reason == "user_question_readiness"
    assert state.active_slot_id is None


def test_question_with_no_active_slot_in_collecting_uses_next_collectible_slot():
    state = ConversationState(call_sid="call_123", phase="collecting")
    runner = SimpleFlowRunner()

    action = runner.handle_turn(
        state=state,
        turn=_turn("How long will this take?"),
        flow_definition=_flow(),
    )

    assert action.action_type == "answer_then_ask"
    assert action.slot_id == "name"
    assert state.active_slot_id == "name"


def test_correction_updates_prior_slot_only():
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        slots={
            "name": SlotState(slot_id="name", status="collected", value="Ojas"),
            "location": SlotState(slot_id="location", status="collected", value="Bangalore"),
            "role": SlotState(slot_id="role", status="missing"),
        },
    )
    runner = SimpleFlowRunner()

    action = runner.handle_turn(
        state=state,
        turn=_turn("Actually, my name is Rahul", turn_index=3),
        flow_definition=_flow(),
    )

    assert state.slots["name"].value == "Rahul"
    assert state.slots["name"].collected_at_turn == 3
    assert state.slots["location"].value == "Bangalore"
    assert action.action_type == "ask_slot"
    assert action.slot_id == "role"


def test_all_required_slots_collected_returns_close():
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        slots={
            "name": SlotState(slot_id="name", status="collected", value="Ojas"),
            "location": SlotState(slot_id="location", status="collected", value="Bangalore"),
            "role": SlotState(slot_id="role", status="collected", value="backend"),
        },
    )
    runner = SimpleFlowRunner()

    action = runner.handle_turn(state=state, turn=_turn("thanks"), flow_definition=_flow())

    assert state.phase == "closing"
    assert action.action_type == "close"
    assert action.should_end_call is True


def test_dict_fallback_policy_without_max_attempts_uses_default_two():
    runner = SimpleFlowRunner()

    assert runner.resolve_max_attempts({"id": "name"}, {}) == 2


def test_required_slot_at_max_attempts_closes_instead_of_reprompting():
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        slots={"name": SlotState(slot_id="name", attempts=2)},
    )
    runner = SimpleFlowRunner()

    action = runner.handle_turn(
        state=state,
        turn=_turn("unclear"),
        flow_definition=_flow([_slot("name", required=True, max_attempts=2)]),
    )

    assert state.phase == "closing"
    assert action.action_type == "close"
    assert action.should_end_call is True
    assert action.reason == "required_slot_max_attempts"
    assert state.slots["name"].attempts == 2


def test_optional_slot_at_max_attempts_is_skipped_and_advances():
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        slots={"nickname": SlotState(slot_id="nickname", attempts=2)},
    )
    runner = SimpleFlowRunner()
    flow = _flow([_slot("nickname", required=False, max_attempts=2), _slot("role")])

    action = runner.handle_turn(state=state, turn=_turn("unclear"), flow_definition=flow)

    assert state.slots["nickname"].status == "skipped"
    assert state.slots["nickname"].attempts == 2
    assert action.action_type == "ask_slot"
    assert action.slot_id == "role"


def test_extra_state_slot_is_ignored_by_extraction_and_collection():
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        slots={
            "name": SlotState(slot_id="name"),
            "location": SlotState(slot_id="location", value="stale"),
        },
    )
    runner = SimpleFlowRunner()
    flow = _flow([_slot("name")])

    action = runner.handle_turn(
        state=state,
        turn=_turn("I am Ojas from Bangalore"),
        flow_definition=flow,
    )

    assert state.slots["name"].value == "Ojas"
    assert state.slots["location"].value == "stale"
    assert state.slots["location"].status == "missing"
    assert action.action_type == "close"
    assert action.should_end_call is True


def test_next_collectible_slot_ignores_extra_state_slots():
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        slots={
            "name": SlotState(slot_id="name", status="collected", value="Ojas"),
            "extra": SlotState(slot_id="extra", status="missing"),
        },
    )
    runner = SimpleFlowRunner()

    slot = runner.next_collectible_slot(state=state, flow_definition=_flow([_slot("name")]))

    assert slot is None


def test_flow_runner_does_not_increment_attempts_directly():
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        slots={"name": SlotState(slot_id="name", attempts=1)},
    )
    runner = SimpleFlowRunner()

    action = runner.handle_turn(
        state=state,
        turn=_turn("unclear"),
        flow_definition=_flow([_slot("name", max_attempts=2)]),
    )

    assert action.action_type == "ask_slot"
    assert state.slots["name"].attempts == 1


def test_flow_runner_module_has_no_provider_or_pipecat_imports():
    source = inspect.getsource(flow_runner)

    assert "pipecat" not in source.lower()
    assert "cerebras" not in source.lower()
    assert "deepgram" not in source.lower()
    assert "cartesia" not in source.lower()
