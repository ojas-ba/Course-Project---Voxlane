"""Per-turn latency/transcript metrics via a Pipecat observer.

An observer (not a pipeline processor) is used because it sees every frame push
regardless of pipeline position, which is required to time STT->LLM->TTS across
the graph. The side channel (Flux ``on_end_of_turn``) opens a turn record; the
``LanguagePrefixProcessor`` annotates the spoken text + chosen language; the
``MemoryProcessor`` reports its own duration; this observer fills the timing
fields and finalizes the record.

Correctness properties:

  * **Assistant text is parser-fed, never frame-scraped** — ``assistant_text``
    comes only from ``note_assistant_text`` (the raw LLM text carries the
    ``<en>``/``<hi>`` tag). ``LLMTextFrame`` is used solely for first-token timing.
  * **Overlapping user turns are merged, never overwritten** — both turns are
    queued and merged into one record (``merged_user_turn_ids``).

Observability goal: every turn record carries enough timestamps to localize a
slow turn to ONE stage — STT/EOT, commit, the queue *before* the LLM, the LLM,
the LLM->TTS hand-off, or TTS — plus whether the bot was still speaking and how
long memory governance took. For merged turns we keep BOTH the earliest and the
latest user-stop so a long human pause between sub-utterances is distinguishable
from a real backend stall.

Primary metric (server-side proxy, NOT phone-speaker playback):
    user_stop_to_first_audio_ms = first_audio_frame_sent_at - user_stopped_speaking_at
"""

from __future__ import annotations

import time
from typing import Any, Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
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


def _norm_text(text: str | None) -> str:
    """Whitespace-collapsed, lowercased text for the eager==final comparison."""
    return " ".join((text or "").split()).lower()


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
        self._pending_bot_was_speaking: bool | None = None
        self._bot_speaking: bool = False
        # Monotonic per-response id (one per LLMFullResponseStartFrame) so late
        # frames from a superseded response can't cross-wire the active record.
        self._response_seq: int = 0
        # Memory governance timing for the turn currently being assembled.
        self._pending_memory_duration_ms: float | None = None
        self._pending_context_message_count: int | None = None

    # -- side-channel annotations (called from the pipeline) ----------------

    def note_user_turn(
        self,
        *,
        turn_id: int,
        user_text: str,
        stt_eot_received_at: float,
        turn_committed_at: float,
        stt_ready: bool | None = True,
        eager_probe: dict[str, Any] | None = None,
    ) -> None:
        """Queue a finalized user turn (awaiting the bot's response to time it)."""
        user_stopped = self._pending_user_stopped_at
        user_stopped_is_fallback = user_stopped is None
        if user_stopped_is_fallback:
            # No VAD UserStoppedSpeakingFrame seen; fall back to the EOT time so
            # primary latency is never silently null.
            user_stopped = stt_eot_received_at
        bot_was_speaking = (
            self._pending_bot_was_speaking
            if self._pending_bot_was_speaking is not None
            else self._bot_speaking
        )
        self._pending_user_stopped_at = None
        self._pending_bot_was_speaking = None
        pending = {
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
            "bot_started_speaking_at": None,
            "bot_stopped_speaking_at": None,
            "bot_was_speaking_when_user_stopped": bool(bot_was_speaking),
            # Tri-state: True (connected before this turn) / False (not yet) /
            # None (unknown). NOT a one-way startup latch (OBS-1).
            "stt_ready_before_user_turn": stt_ready,
            "memory_processor_duration_ms": None,
            "context_message_count": None,
            "error_stage": None,
            "error_message": None,
        }
        # Stage-1 EagerEOT telemetry: store the raw probe + THIS sub-turn's final
        # transcript/time so the match + head-start are computed per sub-turn at
        # finalize. Only present when eager telemetry is enabled (else the key is
        # omitted and records look exactly as before).
        if eager_probe is not None:
            pending["eager_probe"] = {
                "eager_eot_seen": bool(eager_probe.get("eager_eot_seen")),
                "eager_eot_at": eager_probe.get("eager_eot_at"),
                "eager_transcript": eager_probe.get("eager_transcript", ""),
                "turn_resumed_seen": bool(eager_probe.get("turn_resumed_seen")),
                "turn_resumed_at": eager_probe.get("turn_resumed_at"),
                "resumed_since_eager": bool(eager_probe.get("resumed_since_eager")),
                "final_eot_at": stt_eot_received_at,
                "final_transcript": user_text,
            }
        self._pending_turns.append(pending)

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

    def note_memory_timing(
        self, *, duration_ms: float | None, context_message_count: int | None = None
    ) -> None:
        """Report MemoryProcessor governance time for the next/active turn."""
        self._pending_memory_duration_ms = duration_ms
        self._pending_context_message_count = context_message_count
        if self._open is not None and self._open.get("memory_processor_duration_ms") is None:
            self._open["memory_processor_duration_ms"] = duration_ms
            self._open["context_message_count"] = context_message_count

    # -- frame observation --------------------------------------------------

    async def on_push_frame(self, data: FramePushed) -> None:
        if data.direction != FrameDirection.DOWNSTREAM:
            return
        frame = data.frame
        now = time.time()

        # Bot-speaking state is call-level (tracked even with no open turn).
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            if self._open is not None and self._open["first_audio_frame_sent_at"] is None:
                self._open["first_audio_frame_sent_at"] = now
                self._open["bot_started_speaking_at"] = now
                self._try_finalize()
            return
        if isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            if self._open is not None and self._open.get("bot_stopped_speaking_at") is None:
                self._open["bot_stopped_speaking_at"] = now
            return
        if isinstance(frame, UserStoppedSpeakingFrame):
            self._pending_user_stopped_at = now
            self._pending_bot_was_speaking = self._bot_speaking
            return
        if isinstance(frame, (EndFrame, CancelFrame)):
            self._flush_incomplete()
            return
        if isinstance(frame, LLMFullResponseStartFrame):
            self._response_seq += 1
            self._promote_pending(now)
            return
        if self._open is None:
            return

        if isinstance(frame, LLMTextFrame):
            # A text frame after this response already ended is a stray from a
            # superseded response — ignore it.
            if not self._open.get("_llm_ended") and self._open["llm_first_token_or_text_at"] is None:
                self._open["llm_first_token_or_text_at"] = now
        elif isinstance(frame, LLMFullResponseEndFrame):
            # Accept the End only if this response actually produced text and has
            # not already ended. A real response emits text before it ends, so an
            # End arriving with no first-token (or a second End) is a stray/late
            # frame from a superseded response — ignoring it prevents the
            # impossible "LLM total 3 ms / TTFB —" cross-wiring.
            if not self._open.get("_llm_ended") and self._open["llm_first_token_or_text_at"] is not None:
                self._open["_llm_ended"] = True
                self._open["llm_completed_at"] = now
                self._try_finalize()
        elif isinstance(frame, TTSStartedFrame):
            if self._open["tts_request_started_at"] is None:
                self._open["tts_request_started_at"] = now

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
        stops = [t["user_stopped_speaking_at"] for t in turns if t["user_stopped_speaking_at"] is not None]
        commits = [t["turn_committed_at"] for t in turns if t.get("turn_committed_at") is not None]
        merged["latest_user_stopped_speaking_at"] = max(stops) if stops else merged["user_stopped_speaking_at"]
        # Earliest/latest commit anchors. For a single turn these equal
        # turn_committed_at; for a merged turn the *latest* is the commit of the
        # sub-utterance that actually triggered the LLM, so latest-anchored
        # Commit->LLM-start is the real backend delay (the earliest-anchored one
        # is inflated by the human pause between sub-utterances) — LAT-1.
        merged["earliest_turn_committed_at"] = min(commits) if commits else merged.get("turn_committed_at")
        merged["latest_turn_committed_at"] = max(commits) if commits else merged.get("turn_committed_at")
        if len(turns) > 1:
            merged["merged_user_turn_ids"] = [t["turn_id"] for t in turns]
            merged["merged_user_texts"] = [t["user_text"] for t in turns]
            merged["merged_user_stop_times"] = stops
            merged["merged_turn_commit_times"] = commits
            merged["user_text"] = "\n".join(t["user_text"] for t in turns if t["user_text"])
            if stops:
                merged["user_stopped_speaking_at"] = min(stops)  # earliest = primary anchor
            merged["user_stopped_is_eot_fallback"] = all(t["user_stopped_is_eot_fallback"] for t in turns)
            merged["bot_was_speaking_when_user_stopped"] = any(
                t["bot_was_speaking_when_user_stopped"] for t in turns
            )
            # EagerEOT probe: the LAST sub-turn's EndOfTurn is the one that triggered
            # the LLM, so its eager probe is the relevant head-start measurement.
            if turns[-1].get("eager_probe") is not None:
                merged["eager_probe"] = turns[-1]["eager_probe"]
        merged["llm_request_started_at"] = now
        merged["response_id"] = self._response_seq
        merged["_llm_ended"] = False
        if merged.get("memory_processor_duration_ms") is None:
            merged["memory_processor_duration_ms"] = self._pending_memory_duration_ms
            merged["context_message_count"] = self._pending_context_message_count
        self._pending_memory_duration_ms = None
        self._pending_context_message_count = None
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
        rec.pop("_llm_ended", None)
        # Resolve language from the fully-updated per-call state if the parser
        # did not already annotate it.
        if self._language_provider is not None:
            detected, response = self._language_provider()
            if rec.get("detected_language") is None:
                rec["detected_language"] = detected
            if rec.get("response_language") is None:
                rec["response_language"] = response

        first_audio = rec["first_audio_frame_sent_at"]
        earliest_stop = rec["user_stopped_speaking_at"]
        latest_stop = rec.get("latest_user_stopped_speaking_at", earliest_stop)
        earliest_commit = rec.get("earliest_turn_committed_at", rec["turn_committed_at"])
        latest_commit = rec.get("latest_turn_committed_at", rec["turn_committed_at"])

        # Stage breakdown — each delta localizes time to ONE segment.
        rec["user_stop_to_eot_ms"] = _ms(rec["stt_eot_received_at"], earliest_stop)
        rec["eot_to_turn_committed_ms"] = _ms(rec["turn_committed_at"], rec["stt_eot_received_at"])
        rec["user_stop_to_llm_start_ms"] = _ms(rec["llm_request_started_at"], earliest_stop)
        rec["turn_committed_to_llm_start_ms"] = _ms(rec["llm_request_started_at"], rec["turn_committed_at"])
        # Earliest vs latest commit -> LLM start. For merged turns the earliest is
        # inflated by the inter-utterance pause; the latest is the true backend
        # delay. For normal turns both equal turn_committed_to_llm_start_ms (LAT-1).
        rec["earliest_turn_committed_to_llm_start_ms"] = _ms(rec["llm_request_started_at"], earliest_commit)
        rec["latest_turn_committed_to_llm_start_ms"] = _ms(rec["llm_request_started_at"], latest_commit)
        rec["llm_ttfb_ms"] = _ms(rec["llm_first_token_or_text_at"], rec["llm_request_started_at"])
        rec["llm_total_ms"] = _ms(rec["llm_completed_at"], rec["llm_request_started_at"])
        rec["llm_end_to_tts_start_ms"] = _ms(rec["tts_request_started_at"], rec["llm_completed_at"])
        rec["tts_first_audio_ms"] = _ms(first_audio, rec["tts_request_started_at"])
        rec["user_stop_to_first_audio_ms"] = _ms(first_audio, earliest_stop)
        rec["earliest_user_stop_to_first_audio_ms"] = _ms(first_audio, earliest_stop)
        rec["latest_user_stop_to_first_audio_ms"] = _ms(first_audio, latest_stop)

        # Back-compat aliases (existing UI/tests).
        rec["primary_latency_ms"] = rec["user_stop_to_first_audio_ms"]
        rec["eot_delay_ms"] = rec["user_stop_to_eot_ms"]
        rec["commit_delay_ms"] = rec["eot_to_turn_committed_ms"]

        # Stage-1 EagerEOT telemetry (only present when enabled).
        if isinstance(rec.get("eager_probe"), dict):
            self._finalize_eager_probe(rec)

        try:
            self._on_record(rec)
        except Exception:
            pass

    def _finalize_eager_probe(self, rec: dict[str, Any]) -> None:
        """Compute Stage-1 EagerEOT telemetry on the record's eager probe.

        Produces hard facts (head_start_ms, eager_final_match) plus an ESTIMATED
        saved-latency that assumes a speculative draft's TTFB would match the normal
        LLM TTFB. NO speculative draft is ever run — this only measures the missed
        opportunity, so ``estimated_saved_ms`` is an estimate, not a guaranteed save.

        Formula (epoch seconds; ttfb in ms):
            hypothetical_spec_first_token_at = eager_eot_at + ttfb
            effective_spec_ready_at          = max(final_eot_at, hypothetical_spec_first_token_at)
            estimated_saved_ms               = max(0, normal_first_token_at - effective_spec_ready_at)

        Algebraically this equals (EOT->LLM-start backend gap) + min(head_start, ttfb),
        so the estimate credits the whole commit + turn-stop-debounce window before the
        normal first token, not just the eager head start. The max(0, ...) clamp only
        fires on a clock reorder (LLM start stamped before the final EOT).
        """
        probe = rec["eager_probe"]
        eager_at = probe.get("eager_eot_at")
        final_at = probe.get("final_eot_at")
        eager_seen = bool(probe.get("eager_eot_seen"))
        resumed = bool(probe.get("resumed_since_eager"))
        eager_txt = probe.get("eager_transcript", "")
        final_txt = probe.get("final_transcript", "")
        first_token_at = rec.get("llm_first_token_or_text_at")  # normal_first_token_at
        ttfb_ms = rec.get("llm_ttfb_ms")

        head_start_ms = (
            _ms(final_at, eager_at)
            if (eager_seen and final_at is not None and eager_at is not None)
            else None
        )
        match = (_norm_text(eager_txt) == _norm_text(final_txt)) if eager_seen else None

        if not eager_seen:
            verdict, saved = "no_eager_event", None
        elif resumed:
            verdict, saved = "wasted_turn_resumed", 0.0
        elif not _norm_text(final_txt):
            verdict, saved = "missing_final_transcript", None
        elif match is False:
            verdict, saved = "discarded_transcript_mismatch", 0.0
        elif head_start_ms is None or head_start_ms <= 0:
            verdict, saved = "not_enough_head_start", 0.0
        elif first_token_at is None or ttfb_ms is None:
            # Eager fired and matched, but the normal LLM never produced a first
            # token (e.g. a superseded turn), so the saving is not measurable.
            verdict, saved = "eligible_unmeasurable", None
        else:
            hypothetical_spec_first_token_at = eager_at + (ttfb_ms / 1000.0)
            effective_spec_ready_at = max(final_at, hypothetical_spec_first_token_at)
            saved = round(max(0.0, (first_token_at - effective_spec_ready_at) * 1000.0), 1)
            verdict = "eligible"

        probe["head_start_ms"] = head_start_ms
        probe["eager_final_match"] = match
        probe["estimated_saved_ms"] = saved
        probe["verdict"] = verdict
        # Drop full transcripts (the turn's user_text already holds the final one);
        # keep short previews to avoid bloating persisted records.
        probe["eager_transcript_preview"] = (probe.pop("eager_transcript", "") or "")[:80]
        probe["final_transcript_preview"] = (probe.pop("final_transcript", "") or "")[:80]
