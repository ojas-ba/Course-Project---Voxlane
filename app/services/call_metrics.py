"""Per-turn latency/transcript metrics via a Pipecat observer.

An observer (not a pipeline processor) is used because it sees every frame push
regardless of pipeline position, which is required to time STT->LLM->TTS across
the graph. The side channel (Flux ``on_end_of_turn``) opens a turn record; the
``LanguagePrefixProcessor`` annotates the spoken text + chosen language; this
observer fills the timing fields and finalizes the record.

Two correctness properties this file guarantees:

  * **Assistant text is parser-fed, never frame-scraped.** The raw LLM text
    carries the ``<en>``/``<hi>`` tag, so ``assistant_text`` comes only from
    ``note_assistant_text`` (called by the prefix processor with the clean
    spoken text). ``LLMTextFrame`` is used solely for first-token *timing*.

  * **Overlapping user turns are merged, never overwritten.** If the user
    speaks again (a second Flux EOT) before the bot's reply starts, both turns
    are queued and merged into one visible record (``merged_user_turn_ids``)
    with the earliest user-stop as the latency anchor — no turn ID is dropped.

Primary metric (server-side proxy, NOT phone-speaker playback):
    primary_latency_ms = first_audio_frame_sent_at - user_stopped_speaking_at
"""

from __future__ import annotations

import time
from typing import Any, Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    CancelFrame,
    EndFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
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

    def __init__(
        self,
        *,
        on_record: Callable[[dict[str, Any]], None],
        language_provider: Callable[[], tuple[str | None, str | None]] | None = None,
    ) -> None:
        super().__init__()
        self._on_record = on_record
        self._language_provider = language_provider
        self._pending_turns: list[dict[str, Any]] = []
        self._open: dict[str, Any] | None = None
        self._pending_user_stopped_at: float | None = None

    # -- side-channel annotations (called from the pipeline) ----------------

    def note_user_turn(
        self,
        *,
        turn_id: int,
        user_text: str,
        stt_eot_received_at: float,
        turn_committed_at: float,
    ) -> None:
        """Queue a finalized user turn (awaiting the bot's response to time it)."""
        user_stopped = self._pending_user_stopped_at
        user_stopped_is_fallback = user_stopped is None
        if user_stopped_is_fallback:
            # No VAD UserStoppedSpeakingFrame seen; fall back to the EOT time so
            # primary latency is never silently null.
            user_stopped = stt_eot_received_at
        self._pending_user_stopped_at = None
        self._pending_turns.append(
            {
                "turn_id": turn_id,
                "user_text": user_text,
                "assistant_text": "",
                "detected_language": None,
                "response_language": None,
                "user_stopped_speaking_at": user_stopped,
                "user_stopped_is_eot_fallback": user_stopped_is_fallback,
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
        )

    def note_language(self, *, detected_language: str | None, response_language: str | None) -> None:
        if self._open is not None:
            self._open["detected_language"] = detected_language
            self._open["response_language"] = response_language

    def note_assistant_text(self, text: str) -> None:
        """Record the spoken assistant text (tag stripped) — the LLM-done signal."""
        if self._open is not None:
            self._open["assistant_text"] = text
            self._open["_llm_response_noted"] = True
            self._try_finalize()

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
        if isinstance(frame, (EndFrame, CancelFrame)):
            self._flush_incomplete()
            return
        if isinstance(frame, LLMFullResponseStartFrame):
            self._promote_pending(now)
            return
        if self._open is None:
            return

        if isinstance(frame, LLMTextFrame):
            if self._open["llm_first_token_or_text_at"] is None:
                self._open["llm_first_token_or_text_at"] = now
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._open["llm_completed_at"] = now
            self._try_finalize()
        elif isinstance(frame, TTSStartedFrame):
            if self._open["tts_request_started_at"] is None:
                self._open["tts_request_started_at"] = now
        elif isinstance(frame, BotStartedSpeakingFrame):
            if self._open["first_audio_frame_sent_at"] is None:
                self._open["first_audio_frame_sent_at"] = now
            self._try_finalize()

    # -- record lifecycle ---------------------------------------------------

    def _promote_pending(self, now: float) -> None:
        """A bot response is starting: merge queued user turns into one record."""
        if not self._pending_turns:
            return
        if self._open is not None:
            # A new response began before the previous record finalized; don't
            # lose it — emit it (marked) rather than overwrite.
            if self._open.get("error_stage") is None:
                self._open["error_stage"] = "superseded_by_new_response"
                self._open["error_message"] = "A new bot response started before this turn finalized."
            self._emit(self._open)
            self._open = None

        turns = self._pending_turns
        self._pending_turns = []
        merged = dict(turns[0])
        if len(turns) > 1:
            ids = [t["turn_id"] for t in turns]
            merged["merged_user_turn_ids"] = ids
            merged["user_text"] = "\n".join(t["user_text"] for t in turns if t["user_text"])
            stops = [t["user_stopped_speaking_at"] for t in turns if t["user_stopped_speaking_at"] is not None]
            if stops:
                merged["user_stopped_speaking_at"] = min(stops)
            merged["user_stopped_is_eot_fallback"] = all(t["user_stopped_is_eot_fallback"] for t in turns)
        merged["llm_request_started_at"] = now
        self._open = merged

    def _try_finalize(self) -> None:
        rec = self._open
        if rec is None:
            return
        audio_seen = rec["first_audio_frame_sent_at"] is not None
        llm_started = rec["llm_request_started_at"] is not None
        # The parser's note_assistant_text is the authoritative "response done"
        # signal — NOT the raw LLMFullResponseEndFrame. Finalizing on the frame
        # would race ahead of note_error/note_assistant_text and drop both the
        # error_stage and the spoken text on streamed replies (P1-1).
        llm_noted = rec.get("_llm_response_noted", False)
        if audio_seen and (llm_noted or not llm_started):
            self._open = None
            self._emit(rec)

    def _flush_incomplete(self) -> None:
        """On pipeline end/cancel, emit anything still open so no ID is lost."""
        if self._open is not None:
            rec, self._open = self._open, None
            if rec.get("error_stage") is None:
                rec["error_stage"] = "incomplete_at_call_end"
                rec["error_message"] = "Call ended before the turn finalized."
            self._emit(rec)
        for rec in self._pending_turns:
            rec["error_stage"] = rec.get("error_stage") or "no_bot_response"
            rec["error_message"] = rec.get("error_message") or "User turn had no bot response before the call ended."
            self._emit(rec)
        self._pending_turns = []

    def _emit(self, rec: dict[str, Any]) -> None:
        rec.pop("_llm_response_noted", None)
        # Resolve language from the fully-updated per-call state if the parser
        # did not already annotate it.
        if self._language_provider is not None:
            detected, response = self._language_provider()
            if rec.get("detected_language") is None:
                rec["detected_language"] = detected
            if rec.get("response_language") is None:
                rec["response_language"] = response
        rec["primary_latency_ms"] = _ms(rec["first_audio_frame_sent_at"], rec["user_stopped_speaking_at"])
        rec["eot_delay_ms"] = _ms(rec["stt_eot_received_at"], rec["user_stopped_speaking_at"])
        rec["commit_delay_ms"] = _ms(rec["turn_committed_at"], rec["stt_eot_received_at"])
        rec["llm_ttfb_ms"] = _ms(rec["llm_first_token_or_text_at"], rec["llm_request_started_at"])
        rec["llm_total_ms"] = _ms(rec["llm_completed_at"], rec["llm_request_started_at"])
        rec["tts_first_audio_ms"] = _ms(rec["first_audio_frame_sent_at"], rec["tts_request_started_at"])
        try:
            self._on_record(rec)
        except Exception:
            pass
