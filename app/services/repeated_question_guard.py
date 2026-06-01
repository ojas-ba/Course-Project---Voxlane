"""Guard invalid or repeated runtime actions before speech composition."""

from __future__ import annotations

from typing import Any

from app.models.config_models import FallbackPolicy, FlowDefinition, SlotDefinition
from app.models.conversation_models import ConversationState, RuntimeAction, SlotState

DEFAULT_MAX_ATTEMPTS = 2
ASKING_ACTION_TYPES = {"ask_slot", "clarify", "answer_then_ask"}
ASKABLE_SLOT_STATUSES = {"missing", "partial", "invalid"}
COMPLETED_SLOT_STATUSES = {"collected", "confirmed", "skipped"}


class RepeatedQuestionGuard:
    """Normalize repeated or invalid slot-asking actions."""

    def guard_action(
        self,
        *,
        action: RuntimeAction,
        state: ConversationState,
        flow_definition: FlowDefinition | dict[str, Any],
        fallback_policy: FallbackPolicy | dict[str, Any] | None = None,
    ) -> RuntimeAction:
        """Return a valid action without mutating conversation state."""

        flow = _flow(flow_definition)
        policy = _fallback_policy(fallback_policy)
        copied_action = action.model_copy(deep=True)

        if copied_action.action_type == "close":
            return copied_action.model_copy(update={"should_end_call": True})

        if (
            copied_action.action_type == "answer_then_ask"
            and copied_action.slot_id is None
            and copied_action.reason == "user_question_readiness"
            and state.phase == "readiness"
        ):
            return copied_action

        if copied_action.action_type not in ASKING_ACTION_TYPES:
            return copied_action

        if state.phase in {"closing", "ended"}:
            return _close_action(reason="conversation_closing")

        configured_slots = _configured_slot_map(flow)
        slot_id = copied_action.slot_id
        if slot_id is None or slot_id not in configured_slots:
            return self._next_valid_action(
                state=state,
                flow_definition=flow,
                fallback_policy=policy,
            )

        slot_definition = configured_slots[slot_id]
        slot_state = _slot_state(state=state, slot_id=slot_id)
        at_max_attempts = _at_max_attempts(
            slot_definition=slot_definition,
            slot_state=slot_state,
            fallback_policy=policy,
        )
        if not self._is_valid_asking_action(
            action=copied_action,
            state=state,
            slot_definition=slot_definition,
            slot_state=slot_state,
            fallback_policy=policy,
        ):
            if (
                slot_state.status in ASKABLE_SLOT_STATUSES
                and at_max_attempts
                and slot_definition.required
            ):
                return _close_action(reason="required_slot_max_attempts")

            excluded_slot_ids = {slot_id} if state.last_asked_slot_id == slot_id else set()
            guarded_action = self._next_valid_action(
                state=state,
                flow_definition=flow,
                fallback_policy=policy,
                excluded_slot_ids=excluded_slot_ids,
            )
            if (
                guarded_action.action_type == "close"
                and copied_action.action_type == "ask_slot"
                and state.last_asked_slot_id == slot_id
                and slot_state.status in ASKABLE_SLOT_STATUSES
                and not at_max_attempts
            ):
                return RuntimeAction(
                    action_type="clarify",
                    slot_id=slot_id,
                    reason="guard_reprompt",
                )
            return guarded_action

        return copied_action

    def _is_valid_asking_action(
        self,
        *,
        action: RuntimeAction,
        state: ConversationState,
        slot_definition: SlotDefinition,
        slot_state: SlotState,
        fallback_policy: FallbackPolicy,
    ) -> bool:
        if slot_state.status in COMPLETED_SLOT_STATUSES:
            return False
        if slot_state.status not in ASKABLE_SLOT_STATUSES:
            return False
        if _at_max_attempts(
            slot_definition=slot_definition,
            slot_state=slot_state,
            fallback_policy=fallback_policy,
        ):
            return False

        if state.last_asked_slot_id != action.slot_id:
            return True

        if action.action_type == "clarify":
            return True

        if action.action_type == "answer_then_ask" and action.reason == "user_question":
            return True

        return False

    def _next_valid_action(
        self,
        *,
        state: ConversationState,
        flow_definition: FlowDefinition,
        fallback_policy: FallbackPolicy,
        excluded_slot_ids: set[str] | None = None,
    ) -> RuntimeAction:
        excluded = excluded_slot_ids or set()
        for slot_definition in flow_definition.slots:
            if slot_definition.id in excluded:
                continue
            slot_state = _slot_state(state=state, slot_id=slot_definition.id)
            if slot_state.status in COMPLETED_SLOT_STATUSES:
                continue
            if slot_state.status not in ASKABLE_SLOT_STATUSES:
                continue
            if _at_max_attempts(
                slot_definition=slot_definition,
                slot_state=slot_state,
                fallback_policy=fallback_policy,
            ):
                continue

            action_type = "ask_slot" if slot_state.status == "missing" else "clarify"
            return RuntimeAction(
                action_type=action_type,
                slot_id=slot_definition.id,
                reason="guard_redirect",
            )

        return _close_action(reason="guard_no_valid_slot")


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


def _slot_state(*, state: ConversationState, slot_id: str) -> SlotState:
    return state.slots.get(slot_id) or SlotState(slot_id=slot_id)


def _resolve_max_attempts(
    *,
    slot_definition: SlotDefinition,
    fallback_policy: FallbackPolicy,
) -> int:
    if isinstance(slot_definition.max_attempts, int) and slot_definition.max_attempts > 0:
        return slot_definition.max_attempts
    if isinstance(fallback_policy.max_attempts, int) and fallback_policy.max_attempts > 0:
        return fallback_policy.max_attempts
    return DEFAULT_MAX_ATTEMPTS


def _at_max_attempts(
    *,
    slot_definition: SlotDefinition,
    slot_state: SlotState,
    fallback_policy: FallbackPolicy,
) -> bool:
    return slot_state.attempts >= _resolve_max_attempts(
        slot_definition=slot_definition,
        fallback_policy=fallback_policy,
    )


def _close_action(*, reason: str) -> RuntimeAction:
    return RuntimeAction(action_type="close", should_end_call=True, reason=reason)
