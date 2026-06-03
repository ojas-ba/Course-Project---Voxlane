"""Per-turn latency/transcript metrics via a Pipecat observer.

An observer (not a pipeline processor) is used because it sees every frame push
regardless of pipeline position, which is required to time STT->LLM->TTS across
the graph. The side channel (Flux ``on_end_of_turn``) opens a turn record and the
language processor annotates it; this observer fills the timing fields and the
assistant text, then finalizes on ``BotStartedSpeakingFrame``.

Primary metric (server-side proxy, NOT phone-speaker playback):
    primary_latency_ms = first_audio_frame_sent_at - user_stopped_speaking_at
"""

from __future__ import annotations

import time
from typing import Any, Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TTSStartedFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection


def _ms(later: float | None, earlier: float | None) -> float | None:
    if later is None or earlier is None:
        return None
    return round((later - earlier) * 1000.0, 1)


class TurnMetricsObserver(BaseObserver):
    """Build one TurnRecord per user->bot turn and emit it via ``on_record``."""

    def __init__(self, *, on_record: Callable[[dict[str, Any]], None]) -> None:
        super().__init__()
        self._on_record = on_record
        self._open: dict[str, Any] | None = None
        self._pending_user_stopped_at: float | None = None
        self._assistant_parts: list[str] = []

    # -- side-channel annotations (called from the pipeline) ----------------

    def note_user_turn(
        self,
        *,
        turn_id: int,
        user_text: str,
        stt_eot_received_at: float,
        turn_committed_at: float,
    ) -> None:
        self._assistant_parts = []
        self._open = {
            "turn_id": turn_id,
            "user_text": user_text,
            "assistant_text": "",
            "detected_language": None,
            "response_language": None,
            "user_stopped_speaking_at": self._pending_user_stopped_at,
            "stt_eot_received_at": stt_eot_received_at,
            "turn_committed_at": turn_committed_at,
            "llm_request_started_at": None,
            "llm_first_token_or_text_at": None,
            "llm_completed_at": None,
            "tts_request_started_at": None,
            "first_audio_frame_sent_at": None,
            "error_stage": None,
            "error_message": None,
        }
        self._pending_user_stopped_at = None

    def note_language(self, *, detected_language: str | None, response_language: str | None) -> None:
        if self._open is not None:
            self._open["detected_language"] = detected_language
            self._open["response_language"] = response_language

    def note_error(self, *, stage: str, message: str) -> None:
        if self._open is not None:
            self._open["error_stage"] = stage
            self._open["error_message"] = message

    # -- frame observation --------------------------------------------------

    async def on_push_frame(self, data: FramePushed) -> None:
        if data.direction != FrameDirection.DOWNSTREAM:
            return
        frame = data.frame
        now = time.time()

        if isinstance(frame, UserStoppedSpeakingFrame):
            self._pending_user_stopped_at = now
            return
        if self._open is None:
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            self._open["llm_request_started_at"] = now
        elif isinstance(frame, TextFrame):
            if self._open["llm_first_token_or_text_at"] is None:
                self._open["llm_first_token_or_text_at"] = now
            self._assistant_parts.append(frame.text or "")
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._open["llm_completed_at"] = now
            self._open["assistant_text"] = "".join(self._assistant_parts).strip()
        elif isinstance(frame, TTSStartedFrame):
            if self._open["tts_request_started_at"] is None:
                self._open["tts_request_started_at"] = now
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._open["first_audio_frame_sent_at"] = now
            self._finalize()

    def _finalize(self) -> None:
        rec = self._open
        if rec is None:
            return
        rec["primary_latency_ms"] = _ms(rec["first_audio_frame_sent_at"], rec["user_stopped_speaking_at"])
        rec["eot_delay_ms"] = _ms(rec["stt_eot_received_at"], rec["user_stopped_speaking_at"])
        rec["commit_delay_ms"] = _ms(rec["turn_committed_at"], rec["stt_eot_received_at"])
        rec["llm_ttfb_ms"] = _ms(rec["llm_first_token_or_text_at"], rec["llm_request_started_at"])
        rec["llm_total_ms"] = _ms(rec["llm_completed_at"], rec["llm_request_started_at"])
        rec["tts_first_audio_ms"] = _ms(rec["first_audio_frame_sent_at"], rec["tts_request_started_at"])
        self._open = None
        try:
            self._on_record(rec)
        except Exception:
            pass
