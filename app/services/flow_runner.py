"""Deterministic flow progression for committed conversation turns."""

from __future__ import annotations

import re
from typing import Any, Protocol

from app.models.config_models import FallbackPolicy, FlowDefinition, SlotDefinition
from app.models.conversation_models import (
    ConversationState,
    RuntimeAction,
    SlotState,
    TurnInput,
)

DEFAULT_MAX_ATTEMPTS = 2


class FlowRunner(Protocol):
    """Protocol for deterministic conversation flow progression."""

    def initialize_state(
        self,
        *,
        state: ConversationState,
        flow_definition: FlowDefinition | dict[str, Any],
    ) -> ConversationState:
        """Initialize flow-owned state before turns are handled."""

    def handle_turn(
        self,
        *,
        state: ConversationState,
        turn: TurnInput,
        flow_definition: FlowDefinition | dict[str, Any],
        fallback_policy: FallbackPolicy | dict[str, Any] | None = None,
    ) -> RuntimeAction:
        """Handle one committed turn and return the next runtime action."""


class SimpleFlowRunner:
    """Minimal deterministic runner for ordered slot collection."""

    def initialize_state(
        self,
        *,
        state: ConversationState,
        flow_definition: FlowDefinition | dict[str, Any],
    ) -> ConversationState:
        """Create missing configured slot states without deleting existing state."""

        flow = self._flow(flow_definition)
        for slot_definition in flow.slots:
            if slot_definition.id not in state.slots:
                state.slots[slot_definition.id] = SlotState(slot_id=slot_definition.id)
        if state.active_node_id is None:
            state.active_node_id = flow.start_node_id
        return state

    def handle_turn(
        self,
        *,
        state: ConversationState,
        turn: TurnInput,
        flow_definition: FlowDefinition | dict[str, Any],
        fallback_policy: FallbackPolicy | dict[str, Any] | None = None,
    ) -> RuntimeAction:
        """Apply deterministic turn handling and return one next action."""

        flow = self._flow(flow_definition)
        policy = self._fallback_policy(fallback_policy)
        self.initialize_state(state=state, flow_definition=flow)

        text = _normalize_text(turn.normalized_text or turn.text)
        lowered = text.lower()

        if _is_refusal(lowered):
            state.phase = "closing"
            return RuntimeAction(
                action_type="close",
                should_end_call=True,
                reason="refusal",
            )

        if _is_recognized_question(lowered):
            return self._question_action(state=state, flow_definition=flow, fallback_policy=policy)

        correction_updates = self._extract_correction_updates(
            text=text,
            turn=turn,
            state=state,
            flow_definition=flow,
        )
        if correction_updates:
            self._apply_slot_updates(state=state, updates=correction_updates, turn=turn)
            return self._next_slot_action(
                state=state,
                flow_definition=flow,
                fallback_policy=policy,
            )

        if state.phase == "readiness":
            if _is_ready(lowered):
                state.phase = "collecting"
                return self._next_slot_action(
                    state=state,
                    flow_definition=flow,
                    fallback_policy=policy,
                    reason="readiness_confirmed",
                )
            return RuntimeAction(action_type="clarify", reason="readiness_unclear")

        extraction_updates = self.extract_slot_updates(
            turn=turn,
            state=state,
            flow_definition=flow,
        )
        if extraction_updates:
            self._apply_slot_updates(state=state, updates=extraction_updates, turn=turn)

        return self._next_slot_action(
            state=state,
            flow_definition=flow,
            fallback_policy=policy,
        )

    def next_collectible_slot(
        self,
        *,
        state: ConversationState,
        flow_definition: FlowDefinition | dict[str, Any],
        fallback_policy: FallbackPolicy | dict[str, Any] | None = None,
    ) -> SlotDefinition | None:
        """Return the next askable configured slot in flow order."""

        flow = self._flow(flow_definition)
        policy = self._fallback_policy(fallback_policy)
        self.initialize_state(state=state, flow_definition=flow)
        for slot_definition in flow.slots:
            slot_state = state.slots[slot_definition.id]
            if slot_state.status in {"collected", "confirmed", "skipped"}:
                continue
            if slot_state.status in {"missing", "partial", "invalid"}:
                max_attempts = self.resolve_max_attempts(slot_definition, policy)
                if slot_state.attempts < max_attempts:
                    return slot_definition
        return None

    def extract_slot_updates(
        self,
        *,
        turn: TurnInput,
        state: ConversationState,
        flow_definition: FlowDefinition | dict[str, Any],
    ) -> dict[str, str]:
        """Extract obvious configured slot values from one committed turn."""

        flow = self._flow(flow_definition)
        configured_slot_ids = _configured_slot_ids(flow)
        allowed_slot_ids = configured_slot_ids.intersection(state.slots)
        text = _normalize_text(turn.normalized_text or turn.text)
        updates: dict[str, str] = {}

        if "role" in allowed_slot_ids:
            role = _extract_role(text)
            if role is not None:
                updates["role"] = role
        if "location" in allowed_slot_ids:
            location = _extract_location(text)
            if location is not None:
                updates["location"] = location
        if "name" in allowed_slot_ids:
            name = _extract_name(text)
            if name is not None:
                updates["name"] = name

        return updates

    def resolve_max_attempts(
        self,
        slot_definition: SlotDefinition | dict[str, Any],
        fallback_policy: FallbackPolicy | dict[str, Any] | None = None,
    ) -> int:
        """Resolve max attempts from slot, fallback policy, then default."""

        slot_attempts = (
            slot_definition.get("max_attempts")
            if isinstance(slot_definition, dict)
            else getattr(slot_definition, "max_attempts", None)
        )
        if isinstance(slot_attempts, int) and slot_attempts > 0:
            return slot_attempts

        if isinstance(fallback_policy, dict):
            policy_attempts = fallback_policy.get("max_attempts")
        else:
            policy_attempts = getattr(fallback_policy, "max_attempts", None)
        if isinstance(policy_attempts, int) and policy_attempts > 0:
            return policy_attempts

        return DEFAULT_MAX_ATTEMPTS

    def _question_action(
        self,
        *,
        state: ConversationState,
        flow_definition: FlowDefinition,
        fallback_policy: FallbackPolicy,
    ) -> RuntimeAction:
        configured_slot_ids = _configured_slot_ids(flow_definition)
        if state.active_slot_id in configured_slot_ids and state.active_slot_id in state.slots:
            return RuntimeAction(
                action_type="answer_then_ask",
                slot_id=state.active_slot_id,
                reason="user_question",
            )
        if state.phase == "readiness":
            return RuntimeAction(
                action_type="answer_then_ask",
                slot_id=None,
                reason="user_question_readiness",
            )
        if state.phase == "collecting":
            next_slot = self.next_collectible_slot(
                state=state,
                flow_definition=flow_definition,
                fallback_policy=fallback_policy,
            )
            if next_slot is not None:
                state.active_slot_id = next_slot.id
                return RuntimeAction(
                    action_type="answer_then_ask",
                    slot_id=next_slot.id,
                    reason="user_question",
                )
        return RuntimeAction(action_type="clarify", reason="user_question_no_context")

    def _extract_correction_updates(
        self,
        *,
        text: str,
        turn: TurnInput,
        state: ConversationState,
        flow_definition: FlowDefinition,
    ) -> dict[str, str]:
        configured_slot_ids = _configured_slot_ids(flow_definition)
        allowed_slot_ids = configured_slot_ids.intersection(state.slots)
        updates: dict[str, str] = {}

        if "name" in allowed_slot_ids:
            match = re.search(
                r"\b(?:actually,\s*|no,\s*)my name is\s+(.+)$",
                text,
                flags=re.IGNORECASE,
            )
            if match:
                value = _clean_name_value(match.group(1))
                if value is not None:
                    updates["name"] = value

        if "location" in allowed_slot_ids:
            match = re.search(
                r"\b(?:actually,\s*|no,\s*)i am from\s+(.+)$",
                text,
                flags=re.IGNORECASE,
            )
            if match:
                value = _clean_location_value(match.group(1))
                if value is not None:
                    updates["location"] = value

        if "role" in allowed_slot_ids:
            match = re.search(
                r"\b(?:correction,\s*role is|actually,\s*role is)\s+(.+)$",
                text,
                flags=re.IGNORECASE,
            )
            if match:
                value = _clean_role_value(match.group(1))
                if value is not None:
                    updates["role"] = value

        return updates

    def _next_slot_action(
        self,
        *,
        state: ConversationState,
        flow_definition: FlowDefinition,
        fallback_policy: FallbackPolicy,
        reason: str | None = None,
    ) -> RuntimeAction:
        self.initialize_state(state=state, flow_definition=flow_definition)
        for slot_definition in flow_definition.slots:
            slot_state = state.slots[slot_definition.id]
            if slot_state.status in {"collected", "confirmed", "skipped"}:
                continue
            if slot_state.status not in {"missing", "partial", "invalid"}:
                continue

            max_attempts = self.resolve_max_attempts(slot_definition, fallback_policy)
            if slot_state.attempts >= max_attempts:
                if slot_definition.required:
                    state.phase = "closing"
                    return RuntimeAction(
                        action_type="close",
                        slot_id=slot_definition.id,
                        should_end_call=True,
                        reason="required_slot_max_attempts",
                    )
                slot_state.status = "skipped"
                continue

            state.active_slot_id = slot_definition.id
            action_type = "ask_slot" if slot_state.status == "missing" else "clarify"
            return RuntimeAction(
                action_type=action_type,
                slot_id=slot_definition.id,
                reason=reason or f"{slot_state.status}_slot",
            )

        state.phase = "closing"
        state.active_slot_id = None
        return RuntimeAction(
            action_type="close",
            should_end_call=True,
            reason="no_askable_slots",
        )

    def _apply_slot_updates(
        self,
        *,
        state: ConversationState,
        updates: dict[str, str],
        turn: TurnInput,
    ) -> None:
        for slot_id, value in updates.items():
            if slot_id not in state.slots:
                continue
            slot_state = state.slots[slot_id]
            slot_state.status = "collected"
            slot_state.value = value
            slot_state.raw_value = value
            slot_state.collected_at_turn = turn.turn_index

    def _flow(self, flow_definition: FlowDefinition | dict[str, Any]) -> FlowDefinition:
        if isinstance(flow_definition, FlowDefinition):
            return flow_definition
        return FlowDefinition.model_validate(flow_definition)

    def _fallback_policy(
        self,
        fallback_policy: FallbackPolicy | dict[str, Any] | None,
    ) -> FallbackPolicy:
        if isinstance(fallback_policy, FallbackPolicy):
            return fallback_policy
        return FallbackPolicy.model_validate(fallback_policy or {})


def _configured_slot_ids(flow_definition: FlowDefinition) -> set[str]:
    return {slot_definition.id for slot_definition in flow_definition.slots}


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _is_refusal(lowered_text: str) -> bool:
    patterns = [
        r"^(?:please\s+)?stop(?:\s+(?:this|now|here|calling|the call))?[\W_]*$",
        r"\bnot now\b",
        r"\bi don'?t want to continue\b",
        r"\bi do not want to continue\b",
        r"\bcall me later\b",
        r"\bi am not doing this\b",
        r"\bi'?m not doing this\b",
    ]
    return any(re.search(pattern, lowered_text, flags=re.IGNORECASE) for pattern in patterns)


def _is_recognized_question(lowered_text: str) -> bool:
    return any(
        phrase in lowered_text
        for phrase in (
            "why do you need",
            "what is this for",
            "how long",
        )
    )


def _is_ready(lowered_text: str) -> bool:
    cleaned = _normalize_for_intent(lowered_text)
    if not cleaned:
        return False
    if re.search(r"\b(?:no|nope|not|don'?t|do not|cannot|can'?t)\b", cleaned):
        return False
    if lowered_text.strip().endswith("?") and re.match(
        r"^(?:what|why|how|when|where|who|are|is|do|can|could|should)\b",
        cleaned,
    ):
        return False

    ready_phrases = (
        "go ahead",
        "please continue",
        "continue",
        "i am ready",
        "i'm ready",
        "im ready",
        "i am good",
        "i'm good",
        "all set",
        "lets start",
        "let's start",
        "start",
        "sounds good",
        "that works",
        "now is fine",
    )
    if any(re.search(rf"\b{re.escape(phrase)}\b", cleaned) for phrase in ready_phrases):
        return True

    affirmation_tokens = {
        "yes",
        "yeah",
        "yep",
        "ya",
        "sure",
        "ok",
        "okay",
        "ready",
        "please",
        "fine",
        "good",
        "man",
        "bro",
        "sir",
        "maam",
        "mam",
    }
    tokens = cleaned.split()
    return bool(tokens) and all(token in affirmation_tokens for token in tokens)


def _normalize_for_intent(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9'\s]+", " ", text.casefold())
    return " ".join(cleaned.split())


def _extract_name(text: str) -> str | None:
    match = re.search(r"\b(?:i am|i'm)\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        return _clean_name_value(match.group(1))
    match = re.search(r"\bmy name is\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        return _clean_name_value(match.group(1))
    return None


def _extract_location(text: str) -> str | None:
    for pattern in (
        r"\bfrom\s+(.+)$",
        r"\bbased in\s+(.+)$",
        r"\blocated in\s+(.+)$",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _clean_location_value(match.group(1))
            if value is not None:
                return value
    return None


def _extract_role(text: str) -> str | None:
    for pattern in (
        r"\bapplying for\s+(.+)$",
        r"\bfor\s+(.+?)\s+position\b",
        r"\b([A-Za-z][A-Za-z0-9+#.\- ]{0,60}?)\s+role\b",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _clean_role_value(match.group(1))
            if value is not None:
                return value
    return None


def _clean_name_value(raw_value: str) -> str | None:
    lowered = raw_value.strip().lower()
    if lowered.startswith(("applying for", "from ", "based in", "located in")):
        return None
    return _clean_value(
        raw_value,
        boundary_patterns=[
            r"\s+from\b",
            r"\s+based in\b",
            r"\s+located in\b",
            r"\s+applying for\b",
            r",",
        ],
    )


def _clean_location_value(raw_value: str) -> str | None:
    return _clean_value(
        raw_value,
        boundary_patterns=[
            r"\s+applying for\b",
            r"\s+for\s+.+?\s+position\b",
            r",",
        ],
    )


def _clean_role_value(raw_value: str) -> str | None:
    value = raw_value.strip()
    lowered = value.lower()
    if "applying for " in lowered:
        value = value[lowered.rfind("applying for ") + len("applying for ") :]
    value = _clean_value(
        value,
        boundary_patterns=[
            r"\s+from\b",
            r"\s+based in\b",
            r"\s+located in\b",
            r",",
        ],
    )
    if value is None:
        return None
    value = re.sub(r"\s+(?:role|position)$", "", value, flags=re.IGNORECASE).strip()
    return value or None


def _clean_value(raw_value: str, *, boundary_patterns: list[str]) -> str | None:
    value = raw_value.strip()
    earliest_boundary: int | None = None
    for pattern in boundary_patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match and (earliest_boundary is None or match.start() < earliest_boundary):
            earliest_boundary = match.start()
    if earliest_boundary is not None:
        value = value[:earliest_boundary]
    value = value.strip(" \t\r\n,.;:!?")
    return value or None
