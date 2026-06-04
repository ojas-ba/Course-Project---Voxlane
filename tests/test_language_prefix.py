"""Tests for LanguagePrefixProcessor: <en>/<hi> -> Cartesia routing + streaming."""

import asyncio

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TextFrame,
    TTSSpeakFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transcriptions.language import Language

from app.runtime import ACTIVE_SESSIONS
from app.services.language_prefix import LanguagePrefixProcessor


class _FakeMetrics:
    def __init__(self):
        self.assistant_text = None
        self.language = None
        self.errors = []

    def note_assistant_text(self, text):
        self.assistant_text = text

    def note_language(self, *, detected_language, response_language):
        self.language = (detected_language, response_language)

    def note_error(self, *, stage, message):
        self.errors.append((stage, message))


def _make(call_sid="c_lp", metrics=None, seed_state=None):
    if seed_state is not None:
        ACTIVE_SESSIONS[call_sid] = {
            "call_sid": call_sid, "stream_sid": "s", "metrics": {}, "transcript": [],
            "first_latency": None, "config_snapshot": {}, "language_state": seed_state,
        }
    proc = LanguagePrefixProcessor(call_sid=call_sid, metrics_observer=metrics)
    captured: list = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    proc.push_frame = _capture  # type: ignore[method-assign]
    return proc, captured


async def _run(proc, frames):
    for f in frames:
        await proc.process_frame(f, FrameDirection.DOWNSTREAM)


def _spoken(captured):
    return "".join(f.text for f in captured if type(f) is TextFrame)


def _tts_lang_index(captured):
    for i, f in enumerate(captured):
        if isinstance(f, TTSUpdateSettingsFrame):
            return i, f.delta.language
    return None, None


def _first_text_index(captured):
    for i, f in enumerate(captured):
        if type(f) is TextFrame:
            return i
    return None


def test_en_prefix_sets_english_and_forwards_spoken_only():
    m = _FakeMetrics()
    proc, captured = _make(metrics=m)
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("<en> Sure, I'll continue in English."),
        LLMFullResponseEndFrame(),
    ]))
    idx, lang_enum = _tts_lang_index(captured)
    assert lang_enum == Language.EN
    assert _spoken(captured) == "Sure, I'll continue in English."
    assert "<en>" not in _spoken(captured)
    assert m.assistant_text == "Sure, I'll continue in English."
    # No STT session seeded -> detected defaults to new_language_state()'s "en".
    assert m.language == ("en", "en")
    assert isinstance(captured[0], LLMFullResponseStartFrame)
    assert isinstance(captured[-1], LLMFullResponseEndFrame)


def test_hi_prefix_sets_hindi():
    m = _FakeMetrics()
    proc, captured = _make(metrics=m)
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("<hi> Haan bilkul, batao."),
        LLMFullResponseEndFrame(),
    ]))
    _, lang_enum = _tts_lang_index(captured)
    assert lang_enum == Language.HI
    assert _spoken(captured) == "Haan bilkul, batao."


def test_prefix_split_across_frames():
    proc, captured = _make()
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("<"),
        LLMTextFrame("en"),
        LLMTextFrame("> Sure, "),
        LLMTextFrame("happy to help."),
        LLMFullResponseEndFrame(),
    ]))
    _, lang_enum = _tts_lang_index(captured)
    assert lang_enum == Language.EN
    assert _spoken(captured) == "Sure, happy to help."


def test_language_update_emitted_before_first_spoken_text():
    proc, captured = _make()
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("<hi> Namaste"),
        LLMFullResponseEndFrame(),
    ]))
    tts_idx, _ = _tts_lang_index(captured)
    text_idx = _first_text_index(captured)
    assert tts_idx is not None and text_idx is not None
    assert tts_idx < text_idx


def test_missing_prefix_falls_back_without_speaking_a_tag():
    m = _FakeMetrics()
    # Seed a prior Hindi response so fallback resolves to 'hi'.
    proc, captured = _make(metrics=m, seed_state={
        "last_user_language": "hi", "response_language": "hi", "turn_count": 1,
    })
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("Hello, I forgot my tag entirely."),
        LLMFullResponseEndFrame(),
    ]))
    _, lang_enum = _tts_lang_index(captured)
    assert lang_enum == Language.HI  # fell back to last response language
    assert _spoken(captured) == "Hello, I forgot my tag entirely."
    assert m.errors and m.errors[0][0] == "language_prefix_parse"
    ACTIVE_SESSIONS.pop("c_lp", None)


def test_prefix_never_forwarded_so_not_stored_in_context():
    # The assistant aggregator stores forwarded TextFrames; assert none carry the tag.
    proc, captured = _make()
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("<en> Clean text only."),
        LLMFullResponseEndFrame(),
    ]))
    for f in captured:
        assert "<en>" not in getattr(f, "text", "")
        assert "<hi>" not in getattr(f, "text", "")


def test_greeting_ttsspeak_passes_through_untouched():
    proc, captured = _make()
    greeting = TTSSpeakFrame("Hello, you're connected.")
    asyncio.run(_run(proc, [greeting]))
    assert captured == [greeting]  # forwarded unchanged, not intercepted
