"""Runtime session registry and helpers for active call state."""

from collections.abc import MutableMapping
from typing import Any, TypedDict

from app.config import Settings, V1_SAMPLE_RATE


class ConfigSnapshot(TypedDict):
    """Immutable configuration snapshot stored at call start."""

    SYSTEM_PROMPT: str
    LLM_MODEL: str
    LLM_TEMPERATURE: float
    TTS_VOICE_ID: str
    TTS_MODEL: str
    INITIAL_GREETING_PROMPT: str
    SAMPLE_RATE: int


class RuntimeSession(TypedDict):
    """Serializable runtime session state shared across phases."""

    call_sid: str
    stream_sid: str
    metrics: dict[str, Any]
    transcript: list[dict[str, Any]]
    first_latency: float | None
    config_snapshot: ConfigSnapshot


ACTIVE_SESSIONS: MutableMapping[str, RuntimeSession] = {}


def build_config_snapshot(settings: Settings) -> ConfigSnapshot:
    """Create the fixed v1 runtime configuration snapshot for a call."""

    return {
        "SYSTEM_PROMPT": settings.system_prompt,
        "LLM_MODEL": settings.llm_model,
        "LLM_TEMPERATURE": settings.llm_temperature,
        "TTS_VOICE_ID": settings.tts_voice_id,
        "TTS_MODEL": settings.tts_model,
        "INITIAL_GREETING_PROMPT": settings.initial_greeting_prompt,
        "SAMPLE_RATE": V1_SAMPLE_RATE,
    }


def create_session(call_sid: str, stream_sid: str, settings: Settings) -> RuntimeSession:
    """Create and register a new active runtime session."""

    if call_sid in ACTIVE_SESSIONS:
        raise ValueError(f"Session already exists for call_sid={call_sid}")

    session: RuntimeSession = {
        "call_sid": call_sid,
        "stream_sid": stream_sid,
        "metrics": {},
        "transcript": [],
        "first_latency": None,
        "config_snapshot": build_config_snapshot(settings),
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
