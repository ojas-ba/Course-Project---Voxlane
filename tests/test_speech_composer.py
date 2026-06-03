from datetime import datetime, timezone
import inspect

from app.models.config_models import FallbackPolicy, FlowDefinition, FlowNode, SlotDefinition
from app.models.conversation_models import ConversationState, RuntimeAction, SlotState
from app.services import speech_composer
from app.services.speech_composer import SpeechComposer


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _slot(
    slot_id: str,
    *,
    question: str | None = None,
    reprompt: str | None = None,
) -> SlotDefinition:
    return SlotDefinition(
        id=slot_id,
        label=slot_id.title(),
        question=question or f"What is your {slot_id}?",
        reprompt=reprompt,
    )


def _flow(
    slots: list[SlotDefinition] | None = None,
    *,
    closing_text: str | None = "Thank you. That is all I needed for now.",
) -> FlowDefinition:
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
                assistant_text=closing_text,
            )
        ],
        start_node_id="start",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def test_ask_slot_uses_configured_question():
    composer = SpeechComposer()

    text = composer.compose(
        action=RuntimeAction(action_type="ask_slot", slot_id="name"),
        state=ConversationState(call_sid="call_123", phase="collecting"),
        flow_definition=_flow([_slot("name", question="May I have your name?")]),
    )

    assert text == "May I have your name?"


def test_clarify_uses_configured_reprompt():
    composer = SpeechComposer()

    text = composer.compose(
        action=RuntimeAction(action_type="clarify", slot_id="location"),
        state=ConversationState(call_sid="call_123", phase="collecting"),
        flow_definition=_flow(
            [
                _slot(
                    "location",
                    question="Where are you located?",
                    reprompt="Could you share your city?",
                )
            ]
        ),
    )

    assert text == "Could you share your city?"


def test_answer_then_ask_asks_one_question_only():
    composer = SpeechComposer()

    text = composer.compose(
        action=RuntimeAction(
            action_type="answer_then_ask",
            slot_id="role",
            reason="user_question",
        ),
        state=ConversationState(call_sid="call_123", phase="collecting"),
        flow_definition=_flow([_slot("role", question="What role are you interested in?")]),
    )

    assert text == "This helps me complete the short call. What role are you interested in?"
    assert text.count("?") == 1


def test_readiness_answer_then_ask_without_slot_is_safe():
    composer = SpeechComposer()

    text = composer.compose(
        action=RuntimeAction(
            action_type="answer_then_ask",
            slot_id=None,
            reason="user_question_readiness",
        ),
        state=ConversationState(call_sid="call_123", phase="readiness"),
        flow_definition=_flow(),
    )

    assert "short call" in text
    assert text.count("?") == 1


def test_readiness_clarify_without_slot_asks_direct_yes_no_question():
    composer = SpeechComposer()

    text = composer.compose(
        action=RuntimeAction(action_type="clarify", slot_id=None, reason="readiness_unclear"),
        state=ConversationState(call_sid="call_123", phase="readiness"),
        flow_definition=_flow(),
    )

    assert "Should I continue" in text
    assert text.count("?") == 1


def test_refusal_close_uses_fallback_refusal_text():
    composer = SpeechComposer()
    policy = FallbackPolicy(refusal_text="No problem, we can stop here. Thank you.")

    text = composer.compose(
        action=RuntimeAction(action_type="close", reason="refusal", should_end_call=True),
        state=ConversationState(call_sid="call_123", phase="closing"),
        flow_definition=_flow(),
        fallback_policy=policy,
    )

    assert text == "No problem, we can stop here. Thank you."


def test_completion_close_uses_safe_closing_node_text():
    composer = SpeechComposer()

    text = composer.compose(
        action=RuntimeAction(action_type="close", reason="no_askable_slots", should_end_call=True),
        state=ConversationState(call_sid="call_123", phase="closing"),
        flow_definition=_flow(closing_text="All set. Thank you for your time."),
    )

    assert text == "All set. Thank you for your time."


def test_other_close_uses_stop_text():
    composer = SpeechComposer()
    policy = FallbackPolicy(stop_text="Understood. I will end the call now. Thank you.")

    text = composer.compose(
        action=RuntimeAction(
            action_type="close",
            reason="required_slot_max_attempts",
            should_end_call=True,
        ),
        state=ConversationState(call_sid="call_123", phase="closing"),
        flow_definition=_flow(),
        fallback_policy=policy,
    )

    assert text == "Understood. I will end the call now. Thank you."


def test_readiness_max_attempts_close_uses_safe_stop_text():
    composer = SpeechComposer()

    text = composer.compose(
        action=RuntimeAction(
            action_type="close",
            reason="readiness_max_attempts",
            should_end_call=True,
        ),
        state=ConversationState(call_sid="call_123", phase="closing"),
        flow_definition=_flow(),
    )

    assert text == "Sorry, I am having trouble confirming readiness. We can stop here for now."


def test_unsafe_configured_question_falls_back_safely():
    composer = SpeechComposer()

    text = composer.compose(
        action=RuntimeAction(action_type="ask_slot", slot_id="name"),
        state=ConversationState(call_sid="call_123", phase="collecting"),
        flow_definition=_flow([_slot("name", question='{"slot_id": "name"}')]),
    )

    assert text == "What is your name?"


def test_unsafe_configured_reprompt_falls_back_safely():
    composer = SpeechComposer()

    text = composer.compose(
        action=RuntimeAction(action_type="clarify", slot_id="location"),
        state=ConversationState(call_sid="call_123", phase="collecting"),
        flow_definition=_flow(
            [
                _slot(
                    "location",
                    question="Where are you located?",
                    reprompt="The provider debug runtime needs location.",
                )
            ]
        ),
    )

    assert text == "Where are you located?"


def test_unsafe_closing_text_falls_back_safely():
    composer = SpeechComposer()

    text = composer.compose(
        action=RuntimeAction(action_type="close", reason="no_askable_slots", should_end_call=True),
        state=ConversationState(call_sid="call_123", phase="closing"),
        flow_definition=_flow(closing_text='{"debug": true}'),
    )

    assert text == "Thank you. That is all I needed for now."


def test_unsafe_hints_empty_overlong_json_and_internal_terms_are_replaced():
    composer = SpeechComposer()
    state = ConversationState(call_sid="call_123", phase="collecting")
    flow = _flow()

    unsafe_hints = [
        "",
        "x" * 221,
        '{"assistant_text": "hello"}',
        "The runtime debug details are available.",
        "The provider can answer this.",
    ]

    for hint in unsafe_hints:
        text = composer.compose(
            action=RuntimeAction(action_type="fallback", assistant_text_hint=hint),
            state=state,
            flow_definition=flow,
        )

        assert text == "Sorry, I want to make sure I got that right. Could you repeat that briefly?"


def test_normal_words_state_and_model_are_not_blanket_banned():
    composer = SpeechComposer()

    state_text = composer.compose(
        action=RuntimeAction(
            action_type="fallback",
            assistant_text_hint="Which state are you located in?",
        ),
        state=ConversationState(call_sid="call_123", phase="collecting"),
        flow_definition=_flow(),
    )
    model_text = composer.compose(
        action=RuntimeAction(
            action_type="fallback",
            assistant_text_hint="Which hiring model works for you?",
        ),
        state=ConversationState(call_sid="call_123", phase="collecting"),
        flow_definition=_flow(),
    )

    assert state_text == "Which state are you located in?"
    assert model_text == "Which hiring model works for you?"


def test_internal_term_matching_uses_word_boundaries():
    composer = SpeechComposer()

    text = composer.compose(
        action=RuntimeAction(
            action_type="fallback",
            assistant_text_hint="This cartesian plan is ready.",
        ),
        state=ConversationState(call_sid="call_123", phase="collecting"),
        flow_definition=_flow(),
    )

    assert text == "This cartesian plan is ready."


def test_completed_or_skipped_slot_questions_are_not_repeated():
    composer = SpeechComposer()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        slots={
            "name": SlotState(slot_id="name", status="collected", value="Ojas"),
            "location": SlotState(slot_id="location", status="skipped"),
        },
    )
    flow = _flow(
        [
            _slot("name", question="What is your name?"),
            _slot("location", question="Where are you located?"),
        ]
    )

    name_text = composer.compose(
        action=RuntimeAction(action_type="ask_slot", slot_id="name"),
        state=state,
        flow_definition=flow,
    )
    location_text = composer.compose(
        action=RuntimeAction(action_type="ask_slot", slot_id="location"),
        state=state,
        flow_definition=flow,
    )

    assert name_text != "What is your name?"
    assert location_text != "Where are you located?"
    assert "name" not in name_text.lower()
    assert "located" not in location_text.lower()


def test_safe_hint_is_used_for_acknowledge():
    composer = SpeechComposer()

    text = composer.compose(
        action=RuntimeAction(action_type="acknowledge", assistant_text_hint="Thanks, got it."),
        state=ConversationState(call_sid="call_123", phase="collecting"),
        flow_definition=_flow(),
    )

    assert text == "Thanks, got it."


def test_composer_does_not_mutate_conversation_state():
    composer = SpeechComposer()
    state = ConversationState(
        call_sid="call_123",
        phase="collecting",
        slots={"name": SlotState(slot_id="name", status="missing")},
        debug={"before": True},
    )
    before = state.model_dump(mode="json")

    composer.compose(
        action=RuntimeAction(action_type="ask_slot", slot_id="name"),
        state=state,
        flow_definition=_flow([_slot("name")]),
    )

    assert state.model_dump(mode="json") == before


def test_composer_module_has_no_provider_or_pipecat_imports():
    source = inspect.getsource(speech_composer)

    assert "import pipecat" not in source.lower()
    assert "from pipecat" not in source.lower()
    assert "import cerebras" not in source.lower()
    assert "import deepgram" not in source.lower()
    assert "import cartesia" not in source.lower()
