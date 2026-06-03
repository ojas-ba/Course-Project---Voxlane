"""Typed schemas for call-level and turn-level persistence data.

These Pydantic models define the persistence-safe shapes written to MongoDB.
They are output schemas only — internal runtime containers remain as
plain dicts in app/runtime.py.

Transcript data is stored as chronological role-based events.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LatencyEvent(BaseModel):
    """Single normalized latency event from a UserBotLatencyObserver breakdown.

    This is the required persistence shape per TECHNICAL_PLAN.md:
    {"time": float, "event": str}
    """

    time: float
    event: str


class TTFBEntry(BaseModel):
    """Single TTFB entry captured from the observer breakdown."""

    processor: str
    start_time: float
    duration_secs: float


class LatencySummary(BaseModel):
    """Normalized latency summary for a single user-to-bot turn."""

    total_latency_secs: float | None = None
    user_turn_secs: float | None = None
    stt_ttfb_secs: float | None = None
    llm_ttfb_secs: float | None = None
    tts_ttfb_secs: float | None = None
    text_aggregation_secs: float | None = None
    function_call_secs_total: float | None = None
    residual_secs: float | None = None
    unmapped_ttfb_entries: list[TTFBEntry] = Field(default_factory=list)


class TurnMetrics(BaseModel):
    """Metrics captured for a single user-to-bot turn.

    Each on_latency_breakdown observer callback produces one TurnMetrics
    containing the total user-to-bot latency and the normalized per-service
    timeline events.
    """

    user_bot_latency_secs: float | None = None
    latency_events: list[LatencyEvent] = Field(default_factory=list)
    latency_summary: LatencySummary | None = None


class TranscriptEvent(BaseModel):
    """Single chronological transcript event.

    Supports assistant-only greetings and user-only readiness utterances.
    """

    event_index: int
    role: Literal["assistant", "user"]
    phase: Literal["greeting", "readiness", "task", "closing", "unknown"]
    text: str


class CallMetrics(BaseModel):
    """Aggregated metrics for an entire call.

    Accumulated during the call via runtime session helpers and
    finalized into this shape by finalize_call_log().
    """

    first_bot_speech_latency_secs: float | None = None
    first_bot_audio_started_at: float | None = None
    first_tts_frame_queued_at: float | None = None
    greeting_generation_started_at: float | None = None
    greeting_generation_completed_at: float | None = None
    greeting_llm_ttfb: float | None = None
    greeting_llm_total_latency: float | None = None
    greeting_text_ready_before_dial: bool | None = None
    greeting_text_source: str | None = None
    turn_count: int = 0
    turns: list[TurnMetrics] = Field(default_factory=list)


class CallLog(BaseModel):
    """Top-level persistence document for a completed call.

    This is the shape written to the call_logs MongoDB collection.
    Transcript uses role-based events so greetings can be stored without
    requiring a paired user turn.
    """

    call_sid: str
    stream_sid: str
    config_snapshot: dict  # ConfigSnapshot as plain dict for MongoDB
    conversation_state: dict[str, Any] | None = None
    metrics: CallMetrics = Field(default_factory=CallMetrics)
    transcript: list[TranscriptEvent] = Field(default_factory=list)
    completed: bool = True
    error: str | None = None
    call_transport_status: str = "completed"
    conversation_outcome: str = "incomplete"
    final_conversation_phase: str | None = None
    slots_collected_count: int = 0
    required_slots_missing: list[str] = Field(default_factory=list)
