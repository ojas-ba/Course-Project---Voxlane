"""JSON-safe conversation event models for committed user turns."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


ConversationPhase = Literal[
    "greeting",
    "readiness",
    "collecting",
    "answering_question",
    "closing",
    "ended",
]
SlotStatus = Literal[
    "missing",
    "partial",
    "collected",
    "skipped",
    "confirmed",
    "invalid",
]
ActionType = Literal[
    "ask_slot",
    "acknowledge",
    "answer_then_ask",
    "clarify",
    "close",
    "fallback",
]


class TurnEvent(BaseModel):
    """Normalized STT/turn event consumed by TurnCollector."""

    event_type: Literal[
        "update",
        "eager_end_of_turn",
        "end_of_turn",
        "turn_resumed",
        "final_transcript",
    ]
    text: str | None = None
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class TurnInput(BaseModel):
    """Committed user turn passed to conversation logic."""

    call_sid: str
    stream_sid: str | None = None
    turn_index: int
    text: str
    normalized_text: str
    phase: str
    source: Literal[
        "deepgram_flux_end_of_turn",
        "pipecat_turn_complete",
        "manual_test",
        "fallback",
    ]
    started_at: datetime | None = None
    committed_at: datetime
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class TurnCollectionResult(BaseModel):
    """Result returned after TurnCollector observes one event."""

    turn: TurnInput | None = None
    ignored_reason: str | None = None
    commit_source: str | None = None
    commit_timestamp: datetime | None = None
    provider_turn_index: int | None = None
    duplicate_suppressed: bool = False
    greeting_pending_suppressed: bool = False


class SlotState(BaseModel):
    """JSON-safe state for one configured conversation slot."""

    slot_id: str
    status: SlotStatus = "missing"
    value: str | None = None
    raw_value: str | None = None
    attempts: int = 0
    last_asked_at_turn: int | None = None
    collected_at_turn: int | None = None


class ConversationState(BaseModel):
    """JSON-safe per-call conversation state owned by ConversationRuntime."""

    call_sid: str
    stream_sid: str | None = None
    phase: ConversationPhase = "greeting"
    active_node_id: str | None = None
    active_slot_id: str | None = None
    slots: dict[str, SlotState] = Field(default_factory=dict)
    turn_count: int = 0
    fallback_count: int = 0
    refusal_count: int = 0
    last_user_text: str | None = None
    last_assistant_text: str | None = None
    last_asked_slot_id: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    debug: dict[str, Any] = Field(default_factory=dict)


class RuntimeAction(BaseModel):
    """Decision instruction produced before final speech composition."""

    action_type: ActionType
    slot_id: str | None = None
    assistant_text_hint: str | None = None
    should_end_call: bool = False
    reason: str | None = None


class RuntimeResult(BaseModel):
    """Final JSON-safe result returned to the pipeline bridge."""

    action: RuntimeAction
    assistant_text: str
    should_end_call: bool = False
    state: ConversationState
    user_transcript_text: str | None = None
    assistant_transcript_text: str | None = None
    debug: dict[str, Any] = Field(default_factory=dict)


def normalize_conversation_phase(phase: str) -> ConversationPhase:
    """Normalize known legacy phase names into Phase 3 conversation phases."""

    cleaned = phase.strip()
    phase_map: dict[str, ConversationPhase] = {
        "awaiting_readiness": "readiness",
        "task": "collecting",
        "greeting": "greeting",
        "readiness": "readiness",
        "collecting": "collecting",
        "answering_question": "answering_question",
        "closing": "closing",
        "ended": "ended",
    }
    try:
        return phase_map[cleaned]
    except KeyError:
        raise ValueError(f"Unknown conversation phase: {cleaned}") from None
