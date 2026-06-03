"""Compose short, safe assistant speech for runtime actions."""

from __future__ import annotations

import re
from typing import Any

from app.models.config_models import FallbackPolicy, FlowDefinition, SlotDefinition
from app.models.conversation_models import ConversationState, RuntimeAction

MAX_ASSISTANT_TEXT_CHARS = 220
DEFAULT_CLARIFICATION_TEXT = "Could you repeat that briefly?"
DEFAULT_COMPLETION_TEXT = "Thank you. That is all I needed for now."
DEFAULT_STOP_TEXT = "Understood. I will end the call now. Thank you."
DEFAULT_ACKNOWLEDGEMENT_TEXT = "Thanks."
DEFAULT_READINESS_TEXT = (
    "This is a short call to collect a few details. "
    "Should I continue, or would you prefer to stop here?"
)
DEFAULT_SLOT_QUESTIONS = {
    "name": "What is your name?",
    "location": "Where are you located?",
    "role": "What role are you interested in?",
}
INTERNAL_TERMS = (
    "json",
    "debug",
    "provider",
    "pipecat",
    "cerebras",
    "deepgram",
    "cartesia",
    "llm",
    "runtime",
)
COMPLETED_SLOT_STATUSES = {"collected", "confirmed", "skipped"}


class SpeechComposer:
    """Create final assistant text from a guarded runtime action."""

    def compose(
        self,
        *,
        action: RuntimeAction,
        state: ConversationState,
        flow_definition: FlowDefinition | dict[str, Any],
        fallback_policy: FallbackPolicy | dict[str, Any] | None = None,
    ) -> str:
        """Return short, safe assistant speech without mutating state."""

        flow = _flow(flow_definition)
        policy = _fallback_policy(fallback_policy)

        if action.action_type == "ask_slot":
            return self._safe_slot_text(
                slot_id=action.slot_id,
                state=state,
                flow_definition=flow,
                fallback_policy=policy,
                use_reprompt=False,
            )

        if action.action_type == "clarify":
            return self._safe_slot_text(
                slot_id=action.slot_id,
                state=state,
                flow_definition=flow,
                fallback_policy=policy,
                use_reprompt=True,
            )

        if action.action_type == "answer_then_ask":
            return self._answer_then_ask_text(
                action=action,
                state=state,
                flow_definition=flow,
                fallback_policy=policy,
            )

        if action.action_type == "close":
            return self._close_text(
                action=action,
                state=state,
                flow_definition=flow,
                fallback_policy=policy,
            )

        if action.action_type == "acknowledge":
            return self._safe_hint_or_default(
                hint=action.assistant_text_hint,
                state=state,
                flow_definition=flow,
                default_text=DEFAULT_ACKNOWLEDGEMENT_TEXT,
            )

        return self._safe_hint_or_default(
            hint=action.assistant_text_hint,
            state=state,
            flow_definition=flow,
            default_text=_safe_policy_text(
                text=policy.clarification_text,
                state=state,
                flow_definition=flow,
                default_text=DEFAULT_CLARIFICATION_TEXT,
            ),
        )

    def _answer_then_ask_text(
        self,
        *,
        action: RuntimeAction,
        state: ConversationState,
        flow_definition: FlowDefinition,
        fallback_policy: FallbackPolicy,
    ) -> str:
        if action.slot_id is None and action.reason == "user_question_readiness":
            return _safe_policy_text(
                text=DEFAULT_READINESS_TEXT,
                state=state,
                flow_definition=flow_definition,
                default_text="Should I continue, or would you prefer to stop here?",
            )

        question = self._safe_slot_text(
            slot_id=action.slot_id,
            state=state,
            flow_definition=flow_definition,
            fallback_policy=fallback_policy,
            use_reprompt=False,
        )
        text = f"This helps me complete the short call. {question}"
        if _is_safe_text(text=text, state=state, flow_definition=flow_definition):
            return text
        return question

    def _close_text(
        self,
        *,
        action: RuntimeAction,
        state: ConversationState,
        flow_definition: FlowDefinition,
        fallback_policy: FallbackPolicy,
    ) -> str:
        if action.reason == "refusal":
            return _safe_policy_text(
                text=fallback_policy.refusal_text,
                state=state,
                flow_definition=flow_definition,
                default_text="No problem, we can stop here. Thank you.",
            )

        if action.reason == "readiness_max_attempts":
            return _safe_policy_text(
                text="Sorry, I am having trouble confirming readiness. We can stop here for now.",
                state=state,
                flow_definition=flow_definition,
                default_text=DEFAULT_STOP_TEXT,
            )

        if action.reason in {"no_askable_slots", "guard_no_valid_slot"}:
            return _safe_policy_text(
                text=_closing_node_text(flow_definition),
                state=state,
                flow_definition=flow_definition,
                default_text=DEFAULT_COMPLETION_TEXT,
            )

        return _safe_policy_text(
            text=fallback_policy.stop_text,
            state=state,
            flow_definition=flow_definition,
            default_text=DEFAULT_STOP_TEXT,
        )

    def _safe_slot_text(
        self,
        *,
        slot_id: str | None,
        state: ConversationState,
        flow_definition: FlowDefinition,
        fallback_policy: FallbackPolicy,
        use_reprompt: bool,
    ) -> str:
        if slot_id is None:
            if state.phase == "readiness":
                return _safe_policy_text(
                    text=DEFAULT_READINESS_TEXT,
                    state=state,
                    flow_definition=flow_definition,
                    default_text="Should I continue, or would you prefer to stop here?",
                )
            return _safe_policy_text(
                text=fallback_policy.clarification_text,
                state=state,
                flow_definition=flow_definition,
                default_text=DEFAULT_CLARIFICATION_TEXT,
            )

        slot_definition = _configured_slot_map(flow_definition).get(slot_id)
        slot_state = state.slots.get(slot_id)
        if slot_state is not None and slot_state.status in COMPLETED_SLOT_STATUSES:
            return _safe_policy_text(
                text=fallback_policy.clarification_text,
                state=state,
                flow_definition=flow_definition,
                default_text=DEFAULT_CLARIFICATION_TEXT,
            )
        if slot_definition is None:
            return _safe_policy_text(
                text=fallback_policy.clarification_text,
                state=state,
                flow_definition=flow_definition,
                default_text=DEFAULT_CLARIFICATION_TEXT,
            )

        candidates: list[str | None] = []
        if use_reprompt:
            candidates.append(slot_definition.reprompt)
        candidates.append(slot_definition.question)
        candidates.append(DEFAULT_SLOT_QUESTIONS.get(slot_id))
        candidates.append(fallback_policy.clarification_text)
        candidates.append(DEFAULT_CLARIFICATION_TEXT)

        for candidate in candidates:
            if candidate and _is_safe_text(
                text=candidate,
                state=state,
                flow_definition=flow_definition,
            ):
                return candidate

        return DEFAULT_CLARIFICATION_TEXT

    def _safe_hint_or_default(
        self,
        *,
        hint: str | None,
        state: ConversationState,
        flow_definition: FlowDefinition,
        default_text: str,
    ) -> str:
        if hint and _is_safe_text(text=hint, state=state, flow_definition=flow_definition):
            return hint
        return _safe_policy_text(
            text=default_text,
            state=state,
            flow_definition=flow_definition,
            default_text=DEFAULT_CLARIFICATION_TEXT,
        )


def _flow(flow_definition: FlowDefinition | dict[str, Any]) -> FlowDefinition:
    if isinstance(flow_definition, FlowDefinition):
        return flow_definition
    return FlowDefinition.model_validate(flow_definition)


def _fallback_policy(fallback_policy: FallbackPolicy | dict[str, Any] | None) -> FallbackPolicy:
    if isinstance(fallback_policy, FallbackPolicy):
        return fallback_policy
    return FallbackPolicy.model_validate(fallback_policy or {})


def _configured_slot_map(flow_definition: FlowDefinition) -> dict[str, SlotDefinition]:
    return {slot_definition.id: slot_definition for slot_definition in flow_definition.slots}


def _closing_node_text(flow_definition: FlowDefinition) -> str | None:
    for node in flow_definition.nodes:
        if node.id == "closing" and node.assistant_text:
            return node.assistant_text
    for node in flow_definition.nodes:
        if node.type == "closing" and node.assistant_text:
            return node.assistant_text
    return None


def _safe_policy_text(
    *,
    text: str | None,
    state: ConversationState,
    flow_definition: FlowDefinition,
    default_text: str,
) -> str:
    if text and _is_safe_text(text=text, state=state, flow_definition=flow_definition):
        return text
    if _is_safe_text(text=default_text, state=state, flow_definition=flow_definition):
        return default_text
    return DEFAULT_CLARIFICATION_TEXT


def _is_safe_text(
    *,
    text: str,
    state: ConversationState,
    flow_definition: FlowDefinition,
) -> bool:
    stripped = " ".join(text.split())
    if not stripped:
        return False
    if len(stripped) > MAX_ASSISTANT_TEXT_CHARS:
        return False
    if stripped.count("?") > 1:
        return False
    if _looks_like_json(stripped):
        return False
    if _has_internal_term(stripped):
        return False
    if _repeats_completed_slot_question(
        text=stripped,
        state=state,
        flow_definition=flow_definition,
    ):
        return False
    return True


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    if (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    ):
        return True
    return bool(re.search(r'"\s*(?:action_type|slot_id|assistant_text|state|debug)\s*"', stripped))


def _has_internal_term(text: str) -> bool:
    return any(
        re.search(rf"\b{re.escape(term)}\b", text, flags=re.IGNORECASE)
        for term in INTERNAL_TERMS
    )


def _repeats_completed_slot_question(
    *,
    text: str,
    state: ConversationState,
    flow_definition: FlowDefinition,
) -> bool:
    normalized_text = _normalize_for_compare(text)
    for slot_definition in flow_definition.slots:
        slot_state = state.slots.get(slot_definition.id)
        if slot_state is None or slot_state.status not in COMPLETED_SLOT_STATUSES:
            continue
        question = _normalize_for_compare(slot_definition.question)
        if question and question in normalized_text:
            return True
    return False


def _normalize_for_compare(text: str) -> str:
    return " ".join(text.casefold().split()).strip()
