"""Stream the LLM's ``<en>``/``<hi>`` language tag into Cartesia routing.

The main Cerebras reply starts with a tiny tag the model chooses, e.g.::

    <en> Sure, I'll continue in English. What would you like to discuss?
    <hi> Haan bilkul, batao kya help chahiye?

This processor sits between the LLM and Cartesia TTS. It:

  1. Buffers only the first few characters until it can read the tag (which may
     be split across LLM text frames).
  2. Sets Cartesia's language (``TTSUpdateSettingsFrame``) *before* any spoken
     text is forwarded.
  3. Strips the tag and streams the remaining text straight to TTS — streaming
     is preserved; there is no full-response buffering.
  4. Never speaks or stores the tag.
  5. Feeds the metrics observer the spoken text + chosen language (so metrics
     never show the raw tag).

If the tag is missing/malformed it falls back (last response language → STT
hint → English), records ``error_stage="language_prefix_parse"``, and still
speaks the text — it never stalls the turn or calls the LLM again.
"""

from __future__ import annotations

from typing import Any

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TextFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.cartesia.tts import CartesiaTTSSettings

from app.runtime import get_session_language_state, set_session_language_state
from app.services.language import (
    LANGUAGE_PREFIX_MAX_CHARS,
    fallback_response_language,
    new_language_state,
    record_response_language,
    to_language_enum,
    try_parse_language_prefix,
)

_PREFIX_ERROR_STAGE = "language_prefix_parse"


class LanguagePrefixProcessor(FrameProcessor):
    """Parse the LLM ``<en>``/``<hi>`` prefix and route Cartesia language."""

    def __init__(
        self,
        *,
        call_sid: str,
        metrics_observer: Any | None = None,
        max_prefix_chars: int = LANGUAGE_PREFIX_MAX_CHARS,
    ) -> None:
        super().__init__()
        self._call_sid = call_sid
        self._metrics = metrics_observer
        self._max_prefix_chars = max_prefix_chars
        self._reset_response_state()

    def _reset_response_state(self) -> None:
        self._active = False          # inside an LLM response (Start..End)
        self._prefix_done = False     # tag resolved (parsed or fell back)
        self._prefix_buffer = ""      # accumulates until the tag resolves
        self._spoken_parts: list[str] = []
        self._prefix_error: str | None = None

    async def process_frame(
        self, frame: Any, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMFullResponseStartFrame):
            self._reset_response_state()
            self._active = True
            await self.push_frame(frame, direction)
            return

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMTextFrame) and self._active:
            await self._handle_llm_text(frame.text or "", direction)
            return  # the raw (tagged) LLM text frame is never forwarded

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMFullResponseEndFrame) and self._active:
            await self._finish_response(direction)
            await self.push_frame(frame, direction)
            self._active = False
            return

        # Greeting TTSSpeakFrame and everything else passes straight through.
        await self.push_frame(frame, direction)

    async def _handle_llm_text(self, text: str, direction: FrameDirection) -> None:
        if self._prefix_done:
            await self._emit_spoken(text, direction)
            return

        self._prefix_buffer += text
        language, remainder, done = try_parse_language_prefix(
            self._prefix_buffer, max_prefix_chars=self._max_prefix_chars
        )
        if not done:
            return  # keep buffering until the tag (or its absence) is clear

        await self._resolve_prefix(language, remainder, direction)

    async def _finish_response(self, direction: FrameDirection) -> None:
        if not self._prefix_done:
            # Force a decision: response ended before a full tag formed.
            language, remainder, _ = try_parse_language_prefix(
                self._prefix_buffer, max_prefix_chars=0
            )
            await self._resolve_prefix(language, remainder, direction)

        if self._metrics is not None:
            self._metrics.note_assistant_text("".join(self._spoken_parts).strip())
            if self._prefix_error:
                self._metrics.note_error(
                    stage=self._prefix_error,
                    message="LLM reply missing/malformed <en>/<hi> tag; used fallback language.",
                )

    async def _resolve_prefix(
        self, language: str | None, remainder: str, direction: FrameDirection
    ) -> None:
        if language is None:
            language = fallback_response_language(get_session_language_state(self._call_sid))
            self._prefix_error = _PREFIX_ERROR_STAGE
        self._prefix_done = True
        await self._apply_language(language, direction)
        await self._emit_spoken(remainder, direction)

    async def _apply_language(self, language: str, direction: FrameDirection) -> None:
        state = get_session_language_state(self._call_sid) or new_language_state()
        record_response_language(state, language)
        set_session_language_state(self._call_sid, state)

        enum = to_language_enum(language)
        if enum is not None:
            await self.push_frame(
                TTSUpdateSettingsFrame(delta=CartesiaTTSSettings(language=enum)), direction
            )
        if self._metrics is not None:
            self._metrics.note_language(
                detected_language=state.get("last_user_language"),
                response_language=language,
            )

    async def _emit_spoken(self, text: str, direction: FrameDirection) -> None:
        # Strip the space that follows the tag from the very first spoken chunk.
        if not self._spoken_parts:
            text = text.lstrip()
        if not text:
            return
        self._spoken_parts.append(text)
        await self.push_frame(TextFrame(text), direction)
