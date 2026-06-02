"""Synchronous per-call runtime orchestration for Phase 3 conversation logic."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from app.models.config_models import FallbackPolicy, FlowDefinition
from app.models.conversation_models import (
    ConversationState,
    RuntimeAction,
    RuntimeResult,
    TurnInput,
    normalize_conversation_phase,
)
from app.services.flow_runner import SimpleFlowRunner
from app.services.repeated_question_guard import RepeatedQuestionGuard
from app.services.speech_composer import SpeechComposer


class ConversationRuntime:
    """Own one call's live conversation state and produce runtime results."""

    def __init__(
        self,
        *,
        state: ConversationState,
        flow_definition: FlowDefinition | dict[str, Any],
        fallback_policy: FallbackPolicy | dict[str, Any] | None = None,
        flow_runner: SimpleFlowRunner | None = None,
        repeated_question_guard: RepeatedQuestionGuard | None = None,
        speech_composer: SpeechComposer | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._state = state.model_copy(deep=True)
        self._state.phase = normalize_conversation_phase(self._state.phase)
        self._flow_definition = _flow(flow_definition)
        self._fallback_policy = _fallback_policy(fallback_policy)
        self._flow_runner = flow_runner or SimpleFlowRunner()
        self._repeated_question_guard = repeated_question_guard or RepeatedQuestionGuard()
        self._speech_composer = speech_composer or SpeechComposer()

        with self._lock:
            self._flow_runner.initialize_state(
                state=self._state,
                flow_definition=self._flow_definition,
            )
            self._state.updated_at = _utc_now()

    def handle_turn(self, turn: TurnInput) -> RuntimeResult:
        """Handle one committed user turn and return a JSON-safe runtime result."""

        with self._lock:
            self._validate_turn_identity(turn)
            self._state.phase = normalize_conversation_phase(self._state.phase)
            turn_phase = normalize_conversation_phase(turn.phase)
            if self._state.phase == "greeting":
                self._state.phase = turn_phase if turn_phase != "greeting" else "readiness"

            self._state.turn_count += 1
            self._state.last_user_text = turn.text
            self._state.updated_at = _utc_now()
            self._flow_runner.initialize_state(
                state=self._state,
                flow_definition=self._flow_definition,
            )

            raw_action = self._raw_action_for_turn(turn)
            guarded_action, guard_skipped = self._guard_action(raw_action)
            final_action, sanity_replaced = self._post_guard_sanity_check(guarded_action)
            final_action = self._normalize_close_action(final_action)

            composed_text = self._speech_composer.compose(
                action=final_action,
                state=self._state,
                flow_definition=self._flow_definition,
                fallback_policy=self._fallback_policy,
            )
            self._apply_final_action_side_effects(
                action=final_action,
                assistant_text=composed_text,
            )

            debug = {
                "raw_action": raw_action.model_dump(mode="json"),
                "guarded_action": guarded_action.model_dump(mode="json"),
                "final_action": final_action.model_dump(mode="json"),
                "guard_skipped_for_global_clarify": guard_skipped,
                "sanity_replaced_action": sanity_replaced,
            }
            should_end_call = final_action.should_end_call or final_action.action_type == "close"
            return RuntimeResult(
                action=final_action,
                assistant_text=composed_text,
                should_end_call=should_end_call,
                state=self._state.model_copy(deep=True),
                user_transcript_text=turn.text,
                assistant_transcript_text=composed_text,
                debug=debug,
            )

    def snapshot_state(self) -> ConversationState:
        """Return a deep-copied snapshot of the runtime-owned state."""

        with self._lock:
            return self._state.model_copy(deep=True)

    def set_phase(self, phase: str) -> None:
        """Set the runtime phase after normalizing known legacy names."""

        with self._lock:
            self._state.phase = normalize_conversation_phase(phase)
            self._state.updated_at = _utc_now()

    def _validate_turn_identity(self, turn: TurnInput) -> None:
        if turn.call_sid != self._state.call_sid:
            raise ValueError(
                f"Turn call_sid {turn.call_sid!r} does not match runtime call_sid "
                f"{self._state.call_sid!r}"
            )
        if self._state.stream_sid is None and turn.stream_sid:
            self._state.stream_sid = turn.stream_sid
            return
        if (
            self._state.stream_sid
            and turn.stream_sid
            and turn.stream_sid != self._state.stream_sid
        ):
            raise ValueError(
                f"Turn stream_sid {turn.stream_sid!r} does not match runtime stream_sid "
                f"{self._state.stream_sid!r}"
            )

    def _raw_action_for_turn(self, turn: TurnInput) -> RuntimeAction:
        if self._state.phase == "closing":
            return RuntimeAction(
                action_type="close",
                should_end_call=True,
                reason="already_closing",
            )
        if self._state.phase == "ended":
            return RuntimeAction(
                action_type="close",
                should_end_call=True,
                reason="already_ended",
            )
        return self._flow_runner.handle_turn(
            state=self._state,
            turn=turn,
            flow_definition=self._flow_definition,
            fallback_policy=self._fallback_policy,
        )

    def _guard_action(self, raw_action: RuntimeAction) -> tuple[RuntimeAction, bool]:
        if self._is_global_clarify(raw_action):
            # Global readiness clarifies are not repeated slot questions.
            return raw_action.model_copy(deep=True), True
        guarded_action = self._repeated_question_guard.guard_action(
            action=raw_action,
            state=self._state,
            flow_definition=self._flow_definition,
            fallback_policy=self._fallback_policy,
        )
        return guarded_action, False

    def _is_global_clarify(self, action: RuntimeAction) -> bool:
        return (
            action.action_type == "clarify"
            and action.slot_id is None
            and self._state.phase == "readiness"
        )

    def _post_guard_sanity_check(self, action: RuntimeAction) -> tuple[RuntimeAction, bool]:
        if self._is_allowed_final_action(action):
            return action, False
        return (
            RuntimeAction(
                action_type="close",
                should_end_call=True,
                reason="runtime_invalid_action",
            ),
            True,
        )

    def _is_allowed_final_action(self, action: RuntimeAction) -> bool:
        if action.action_type == "ask_slot":
            return action.slot_id in self._state.slots
        if action.action_type == "clarify":
            return action.slot_id is None or action.slot_id in self._state.slots
        if action.action_type == "answer_then_ask":
            if action.slot_id is None:
                return (
                    self._state.phase == "readiness"
                    and action.reason == "user_question_readiness"
                )
            return action.slot_id in self._state.slots
        return action.action_type in {"close", "fallback", "acknowledge"}

    def _normalize_close_action(self, action: RuntimeAction) -> RuntimeAction:
        if action.action_type == "close" and not action.should_end_call:
            return action.model_copy(update={"should_end_call": True})
        return action

    def _apply_final_action_side_effects(
        self,
        *,
        action: RuntimeAction,
        assistant_text: str,
    ) -> None:
        if action.action_type == "ask_slot" and action.slot_id is not None:
            self._apply_slot_prompt_side_effects(slot_id=action.slot_id, increment_attempts=True)
        elif action.action_type == "clarify" and action.slot_id is not None:
            self._apply_slot_prompt_side_effects(slot_id=action.slot_id, increment_attempts=True)
        elif action.action_type == "answer_then_ask" and action.slot_id is not None:
            self._apply_slot_prompt_side_effects(slot_id=action.slot_id, increment_attempts=False)
        elif action.action_type == "close":
            if self._state.phase != "ended":
                self._state.phase = "closing"
        elif action.action_type == "fallback":
            self._state.fallback_count += 1

        if action.reason == "refusal":
            self._state.refusal_count += 1
            if self._state.phase != "ended":
                self._state.phase = "closing"

        self._state.last_assistant_text = assistant_text
        self._state.updated_at = _utc_now()

    def _apply_slot_prompt_side_effects(self, *, slot_id: str, increment_attempts: bool) -> None:
        if self._state.phase not in {"closing", "ended"}:
            self._state.phase = "collecting"
        self._state.active_slot_id = slot_id
        self._state.last_asked_slot_id = slot_id
        slot_state = self._state.slots[slot_id]
        if increment_attempts:
            slot_state.attempts += 1
        slot_state.last_asked_at_turn = self._state.turn_count


def _flow(flow_definition: FlowDefinition | dict[str, Any]) -> FlowDefinition:
    if isinstance(flow_definition, FlowDefinition):
        return flow_definition
    return FlowDefinition.model_validate(flow_definition)


def _fallback_policy(fallback_policy: FallbackPolicy | dict[str, Any] | None) -> FallbackPolicy:
    if isinstance(fallback_policy, FallbackPolicy):
        return fallback_policy
    return FallbackPolicy.model_validate(fallback_policy or {})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
