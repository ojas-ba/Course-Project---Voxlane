"""Runtime registries and helpers for call state."""

from collections.abc import MutableMapping
import time
from typing import Any, Literal, TypedDict

from app.config import Settings, V1_SAMPLE_RATE


class ConfigSnapshot(TypedDict):
    """Immutable configuration snapshot stored at call start."""

    SYSTEM_PROMPT: str
    LLM_MODEL: str
    LLM_TEMPERATURE: float
    TTS_VOICE_ID: str
    TTS_MODEL: str
    INITIAL_GREETING_PROMPT: str
    INITIAL_GREETING_INSTRUCTIONS: str
    INITIAL_GREETING_FALLBACK_TEXT: str
    SAMPLE_RATE: int


class RuntimeSession(TypedDict):
    """Serializable runtime session state shared across phases."""

    call_sid: str
    stream_sid: str
    metrics: dict[str, Any]
    transcript: list[dict[str, Any]]
    first_latency: float | None
    config_snapshot: ConfigSnapshot


OpeningState = Literal[
    "preparing",
    "dialing",
    "websocket_bound",
    "greeting_speaking",
    "awaiting_readiness",
    "task_active",
    "ended",
]


class PendingOutboundContext(TypedDict):
    """Serializable context prepared before an outbound call connects."""

    call_sid: str | None
    to_number: str
    config_snapshot: ConfigSnapshot
    opening_state: OpeningState
    greeting_text: str | None
    greeting_error: str | None
    created_at: float


ACTIVE_SESSIONS: MutableMapping[str, RuntimeSession] = {}
PENDING_OUTBOUND_CALLS: MutableMapping[str, PendingOutboundContext] = {}


def _looks_like_spoken_greeting(text: str) -> bool:
    normalized = text.strip().lower()
    readiness_cues = ("good time", "available", "ready", "continue", "speak", "talk")
    return "." in normalized and ("?" in normalized or any(cue in normalized for cue in readiness_cues))


def build_config_snapshot(settings: Settings) -> ConfigSnapshot:
    """Create the fixed v1 runtime configuration snapshot for a call."""

    legacy_greeting = settings.initial_greeting_prompt.strip()
    fallback_text = settings.initial_greeting_fallback_text.strip()
    instructions = settings.initial_greeting_instructions.strip()

    if not fallback_text:
        fallback_text = (
            legacy_greeting
            if _looks_like_spoken_greeting(legacy_greeting)
            else "Hello. I am calling for a short conversation. Is now a good time?"
        )
    if not instructions:
        instructions = (
            "Generate a brief personalized outbound opening and ask whether now is a good time."
            if _looks_like_spoken_greeting(legacy_greeting)
            else legacy_greeting
        )

    return {
        "SYSTEM_PROMPT": settings.system_prompt,
        "LLM_MODEL": settings.llm_model,
        "LLM_TEMPERATURE": settings.llm_temperature,
        "TTS_VOICE_ID": settings.tts_voice_id,
        "TTS_MODEL": settings.tts_model,
        "INITIAL_GREETING_PROMPT": settings.initial_greeting_prompt,
        "INITIAL_GREETING_INSTRUCTIONS": instructions,
        "INITIAL_GREETING_FALLBACK_TEXT": fallback_text,
        "SAMPLE_RATE": V1_SAMPLE_RATE,
    }


def create_session(
    call_sid: str,
    stream_sid: str,
    settings: Settings,
    *,
    config_snapshot: ConfigSnapshot | None = None,
) -> RuntimeSession:
    """Create and register a new active runtime session."""

    if call_sid in ACTIVE_SESSIONS:
        raise ValueError(f"Session already exists for call_sid={call_sid}")

    session: RuntimeSession = {
        "call_sid": call_sid,
        "stream_sid": stream_sid,
        "metrics": {},
        "transcript": [],
        "first_latency": None,
        "config_snapshot": config_snapshot or build_config_snapshot(settings),
    }
    ACTIVE_SESSIONS[call_sid] = session
    return session


def get_session(call_sid: str) -> RuntimeSession | None:
    """Return an active runtime session by call SID, if present."""

    return ACTIVE_SESSIONS.get(call_sid)


def delete_session(call_sid: str) -> RuntimeSession | None:
    """Remove and return an active runtime session by call SID, if present."""

    return ACTIVE_SESSIONS.pop(call_sid, None)


# --- Phase 3: Session update helpers for metrics capture ---


def update_session_first_latency(call_sid: str, latency_secs: float) -> None:
    """Set the first-bot-speech latency for a session (once only).

    Called from the on_first_bot_speech_latency observer callback.
    Writes to both session["first_latency"] and
    session["metrics"]["first_bot_speech_latency_secs"].

    Args:
        call_sid: The call identifier to update.
        latency_secs: Time from client connection to first bot speech.
    """

    session = ACTIVE_SESSIONS.get(call_sid)
    if session is None:
        return
    if session["first_latency"] is None:
        session["first_latency"] = latency_secs
        session["metrics"]["first_bot_speech_latency_secs"] = latency_secs


def append_session_turn_metrics(call_sid: str, turn_metrics: dict[str, Any]) -> None:
    """Append a completed turn's metrics to the session.

    Called from the on_latency_breakdown observer callback after
    build_turn_metrics() produces a TurnMetrics and converts it
    to a dict via model_dump().

    Args:
        call_sid: The call identifier to update.
        turn_metrics: A serialized TurnMetrics dict.
    """

    session = ACTIVE_SESSIONS.get(call_sid)
    if session is None:
        return
    turns: list[dict[str, Any]] = session["metrics"].setdefault("turns", [])
    turns.append(turn_metrics)
    session["metrics"]["turn_count"] = len(turns)


def create_pending_outbound_context(
    *,
    to_number: str,
    config_snapshot: ConfigSnapshot,
    greeting_text: str | None = None,
    greeting_error: str | None = None,
) -> PendingOutboundContext:
    """Create a pending outbound context before Exotel returns a call SID."""

    return {
        "call_sid": None,
        "to_number": to_number,
        "config_snapshot": config_snapshot,
        "opening_state": "preparing",
        "greeting_text": greeting_text,
        "greeting_error": greeting_error,
        "created_at": time.time(),
    }


def attach_pending_call_sid(
    context: PendingOutboundContext,
    *,
    call_sid: str,
) -> PendingOutboundContext:
    """Attach Exotel's call SID and register the pending outbound context."""

    context["call_sid"] = call_sid
    context["opening_state"] = "dialing"
    PENDING_OUTBOUND_CALLS[call_sid] = context
    return context


def get_pending_outbound_context(call_sid: str) -> PendingOutboundContext | None:
    """Return a pending outbound context by Exotel call SID, if present."""

    return PENDING_OUTBOUND_CALLS.get(call_sid)


def cleanup_expired_pending_outbound_contexts(max_age_secs: float) -> int:
    """Remove pending outbound contexts older than max_age_secs."""

    now = time.time()
    expired_call_sids = [
        call_sid
        for call_sid, context in PENDING_OUTBOUND_CALLS.items()
        if now - context["created_at"] > max_age_secs
    ]
    for call_sid in expired_call_sids:
        PENDING_OUTBOUND_CALLS.pop(call_sid, None)
    return len(expired_call_sids)


def pop_pending_outbound_context(call_sid: str) -> PendingOutboundContext | None:
    """Remove and return a pending outbound context by Exotel call SID."""

    return PENDING_OUTBOUND_CALLS.pop(call_sid, None)


def update_pending_opening_state(call_sid: str, opening_state: OpeningState) -> None:
    """Update pending outbound opening state if the context is still present."""

    context = PENDING_OUTBOUND_CALLS.get(call_sid)
    if context is not None:
        context["opening_state"] = opening_state


def append_session_transcript_event(
    call_sid: str,
    *,
    role: Literal["assistant", "user"],
    phase: Literal["greeting", "readiness", "task", "closing", "unknown"],
    text: str,
) -> None:
    """Append one chronological transcript event to the active session."""

    session = ACTIVE_SESSIONS.get(call_sid)
    if session is None:
        return

    transcript = session["transcript"]
    transcript.append(
        {
            "event_index": len(transcript) + 1,
            "role": role,
            "phase": phase,
            "text": text,
        }
    )
