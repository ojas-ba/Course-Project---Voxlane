"""Metrics logging helpers and call finalization.

Owns the normalization of LatencyBreakdown into the persistence-required
[{"time": float, "event": str}] shape, and the finalize_call_log() helper
that converts runtime session state into a persistence-safe CallLog.

The normalize function accesses LatencyBreakdown attributes directly
instead of calling chronological_events(), because that method returns
list[str] and discards the timestamps we need for the persistence shape.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.models.schemas import (
    CallLog,
    CallMetrics,
    LatencyEvent,
    LatencySummary,
    TTFBEntry,
    TurnMetrics,
)

TTFB_TIE_EPSILON_SECS = 1e-6
COMPLETED_SLOT_STATUSES = {"collected", "confirmed"}


def _map_processor_to_category(processor: str) -> str | None:
    normalized = processor.strip().lower()
    if "deepgram" in normalized or "stt" in normalized:
        return "stt"
    if "cerebras" in normalized or "llm" in normalized:
        return "llm"
    if "cartesia" in normalized or "tts" in normalized:
        return "tts"
    return None


def _select_ttfb_summary(entries: list[TTFBEntry]) -> float | None:
    if not entries:
        return None
    if len(entries) == 1:
        return entries[0].duration_secs

    entries_sorted = sorted(entries, key=lambda e: e.start_time)
    earliest = entries_sorted[0]
    tie_entries = [
        entry
        for entry in entries_sorted
        if abs(entry.start_time - earliest.start_time) <= TTFB_TIE_EPSILON_SECS
    ]
    if len(tie_entries) > 1:
        return None
    return earliest.duration_secs


def _build_latency_summary(
    *,
    latency_secs: float | None,
    breakdown: Any | None,
) -> LatencySummary | None:
    if latency_secs is None and breakdown is None:
        return None

    summary = LatencySummary(total_latency_secs=latency_secs)
    if breakdown is None:
        return summary

    summary.user_turn_secs = getattr(breakdown, "user_turn_secs", None)

    categories: dict[str, list[TTFBEntry]] = {
        "stt": [],
        "llm": [],
        "tts": [],
    }
    unmapped: list[TTFBEntry] = []
    for ttfb in getattr(breakdown, "ttfb", []) or []:
        entry = TTFBEntry(
            processor=ttfb.processor,
            start_time=ttfb.start_time,
            duration_secs=ttfb.duration_secs,
        )
        category = _map_processor_to_category(ttfb.processor)
        if category is None:
            unmapped.append(entry)
        else:
            categories[category].append(entry)

    summary.stt_ttfb_secs = _select_ttfb_summary(categories["stt"])
    summary.llm_ttfb_secs = _select_ttfb_summary(categories["llm"])
    summary.tts_ttfb_secs = _select_ttfb_summary(categories["tts"])
    summary.unmapped_ttfb_entries = unmapped

    text_aggregation = getattr(breakdown, "text_aggregation", None)
    if text_aggregation is not None:
        summary.text_aggregation_secs = text_aggregation.duration_secs

    function_calls = getattr(breakdown, "function_calls", None)
    if function_calls is not None:
        summary.function_call_secs_total = sum(fc.duration_secs for fc in function_calls)

    core_components = [
        summary.user_turn_secs,
        summary.stt_ttfb_secs,
        summary.llm_ttfb_secs,
        summary.tts_ttfb_secs,
    ]
    if summary.total_latency_secs is not None and all(val is not None for val in core_components):
        optional_sum = 0.0
        if summary.text_aggregation_secs is not None:
            optional_sum += summary.text_aggregation_secs
        if summary.function_call_secs_total is not None:
            optional_sum += summary.function_call_secs_total

        unmapped_sum = sum(e.duration_secs for e in summary.unmapped_ttfb_entries)

        residual = summary.total_latency_secs - (sum(core_components) + optional_sum + unmapped_sum)
        if residual >= 0:
            summary.residual_secs = residual

    return summary


def normalize_latency_breakdown(breakdown: Any) -> list[dict[str, Any]]:
    """Normalize a LatencyBreakdown into persistence-safe dictionaries.

    Extracts timing data from the breakdown's typed sub-fields and
    returns sorted dictionaries in the required {"time": float, "event": str}
    shape.

    Uses duck-typed access on the breakdown to avoid importing Pipecat
    types into this module.

    Args:
        breakdown: A pipecat LatencyBreakdown instance with .ttfb,
            .text_aggregation, .user_turn_start_time, .user_turn_secs,
            and .function_calls attributes.

    Returns:
        List of {"time": float, "event": str} dicts sorted by time.
    """

    events: list[tuple[float, str]] = []

    if breakdown.user_turn_start_time is not None and breakdown.user_turn_secs is not None:
        events.append((
            breakdown.user_turn_start_time,
            f"User turn: {breakdown.user_turn_secs:.3f}s",
        ))

    for t in getattr(breakdown, "ttfb", []) or []:
        events.append((t.start_time, f"{t.processor}: TTFB {t.duration_secs:.3f}s"))

    for fc in getattr(breakdown, "function_calls", []) or []:
        events.append((fc.start_time, f"{fc.function_name}: {fc.duration_secs:.3f}s"))

    ta = getattr(breakdown, "text_aggregation", None)
    if ta:
        events.append((
            ta.start_time,
            f"{ta.processor}: text aggregation {ta.duration_secs:.3f}s",
        ))

    events.sort(key=lambda e: e[0])

    return [{"time": t, "event": ev} for t, ev in events]


def build_turn_metrics(
    *,
    latency_secs: float | None,
    breakdown: Any | None,
) -> TurnMetrics:
    """Build a TurnMetrics from a single user-to-bot turn.

    Args:
        latency_secs: Total user-to-bot latency from on_latency_measured,
            or None if no user speech preceded this bot response.
        breakdown: A pipecat LatencyBreakdown instance, or None if no
            breakdown was emitted for this turn.

    Returns:
        A TurnMetrics with normalized latency events and summary fields.
    """

    latency_events: list[LatencyEvent] = []
    if breakdown is not None:
        raw = normalize_latency_breakdown(breakdown)
        latency_events = [LatencyEvent(time=e["time"], event=e["event"]) for e in raw]

    latency_summary = _build_latency_summary(latency_secs=latency_secs, breakdown=breakdown)

    return TurnMetrics(
        user_bot_latency_secs=latency_secs,
        latency_events=latency_events,
        latency_summary=latency_summary,
    )


def _required_slot_ids(config_snapshot: dict[str, Any]) -> list[str]:
    flow_definition = config_snapshot.get("flow_definition")
    if not isinstance(flow_definition, dict):
        return []

    slot_ids: list[str] = []
    for slot_definition in flow_definition.get("slots", []) or []:
        if not isinstance(slot_definition, dict):
            continue
        slot_id = slot_definition.get("id")
        if isinstance(slot_id, str) and slot_definition.get("required", True):
            slot_ids.append(slot_id)
    return slot_ids


def _conversation_summary(
    *,
    config_snapshot: dict[str, Any],
    conversation_state: dict[str, Any] | None,
    completed: bool,
    error: str | None,
) -> dict[str, Any]:
    if not conversation_state:
        outcome = "runtime_error" if error else "incomplete"
        return {
            "call_transport_status": "completed" if completed else "failed",
            "conversation_outcome": outcome,
            "final_conversation_phase": None,
            "slots_collected_count": 0,
            "required_slots_missing": _required_slot_ids(config_snapshot),
        }

    phase = conversation_state.get("phase")
    slots = conversation_state.get("slots") or {}
    if not isinstance(slots, dict):
        slots = {}

    slots_collected_count = sum(
        1
        for slot in slots.values()
        if isinstance(slot, dict) and slot.get("status") in COMPLETED_SLOT_STATUSES
    )

    required_slot_ids = _required_slot_ids(config_snapshot) or [
        slot_id for slot_id in slots if isinstance(slot_id, str)
    ]
    required_slots_missing = [
        slot_id
        for slot_id in required_slot_ids
        if not (
            isinstance(slots.get(slot_id), dict)
            and slots[slot_id].get("status") in COMPLETED_SLOT_STATUSES
        )
    ]

    if error:
        conversation_outcome = (
            "user_abandoned" if "disconnected" in error.casefold() else "runtime_error"
        )
    elif phase in {"closing", "ended"} and not required_slots_missing:
        conversation_outcome = "completed"
    elif phase == "readiness":
        conversation_outcome = "readiness_failed"
    else:
        conversation_outcome = "incomplete"

    return {
        "call_transport_status": "completed" if completed else "failed",
        "conversation_outcome": conversation_outcome,
        "final_conversation_phase": phase if isinstance(phase, str) else None,
        "slots_collected_count": slots_collected_count,
        "required_slots_missing": required_slots_missing,
    }


def finalize_call_log(
    *,
    call_sid: str,
    stream_sid: str,
    config_snapshot: dict[str, Any],
    conversation_state: dict[str, Any] | None = None,
    metrics: dict[str, Any],
    transcript: list[dict[str, Any]],
    completed: bool,
    error: str | None,
) -> CallLog:
    """Build a persistence-safe CallLog from runtime session state.

    Converts the runtime session's accumulated metrics dict into a
    typed CallLog ready for database insertion.

    Args:
        call_sid: Exotel call identifier.
        stream_sid: Exotel stream identifier.
        config_snapshot: The immutable per-call config snapshot.
        metrics: The accumulated metrics dict from the runtime session.
        transcript: The completed transcript turns from the runtime session.
        completed: Whether the pipeline completed normally.
        error: Error description if the pipeline failed.

    Returns:
        A CallLog ready for persist_call_log().
    """

    log = logger.bind(call_sid=call_sid)

    # Reconstruct typed CallMetrics from the accumulated dict
    call_metrics = CallMetrics(
        first_bot_speech_latency_secs=metrics.get("first_bot_speech_latency_secs"),
        first_bot_audio_started_at=metrics.get("first_bot_audio_started_at"),
        first_tts_frame_queued_at=metrics.get("first_tts_frame_queued_at"),
        greeting_generation_started_at=metrics.get("greeting_generation_started_at"),
        greeting_generation_completed_at=metrics.get("greeting_generation_completed_at"),
        greeting_llm_ttfb=metrics.get("greeting_llm_ttfb"),
        greeting_llm_total_latency=metrics.get("greeting_llm_total_latency"),
        greeting_text_ready_before_dial=metrics.get("greeting_text_ready_before_dial"),
        greeting_text_source=metrics.get("greeting_text_source"),
        turn_count=metrics.get("turn_count", 0),
        turns=metrics.get("turns", []),
    )
    conversation_summary = _conversation_summary(
        config_snapshot=config_snapshot,
        conversation_state=conversation_state,
        completed=completed,
        error=error,
    )

    call_log = CallLog(
        call_sid=call_sid,
        stream_sid=stream_sid,
        config_snapshot=config_snapshot,
        conversation_state=conversation_state,
        metrics=call_metrics,
        transcript=transcript,
        completed=completed,
        error=error,
        **conversation_summary,
    )

    log.info(
        "Call log finalized",
        turn_count=call_metrics.turn_count,
        first_latency=call_metrics.first_bot_speech_latency_secs,
    )

    return call_log
