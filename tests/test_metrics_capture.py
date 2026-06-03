import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TextFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSTextFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from app.services.call_metrics import TurnMetricsObserver, _ms


def _push(obs, frame):
    data = FramePushed(
        source=None, destination=None, frame=frame,
        direction=FrameDirection.DOWNSTREAM, timestamp=0,
    )
    asyncio.run(obs.on_push_frame(data))


def test_assistant_text_frame_type_discrimination():
    # The fix: capture only LLMTextFrame, not the broad TextFrame whose
    # subclasses include user transcription and TTS text.
    assert issubclass(LLMTextFrame, TextFrame)
    assert not issubclass(TranscriptionFrame, LLMTextFrame)
    assert not issubclass(TTSTextFrame, LLMTextFrame)


def test_assistant_text_captured_from_llm_text_frames_only():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=1, user_text="hello", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMTextFrame("Hello "))
    _push(obs, LLMTextFrame("there!"))
    _push(obs, LLMFullResponseEndFrame())
    _push(obs, TTSStartedFrame())
    _push(obs, BotStartedSpeakingFrame())

    assert len(out) == 1
    rec = out[0]
    assert rec["assistant_text"] == "Hello there!"
    assert rec["llm_first_token_or_text_at"] is not None
    assert rec["primary_latency_ms"] is not None


def test_user_stopped_falls_back_to_eot_when_no_vad_frame():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    # No UserStoppedSpeakingFrame pushed.
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=5.0, turn_committed_at=5.1)
    _push(obs, BotStartedSpeakingFrame())
    assert out[0]["user_stopped_speaking_at"] == 5.0
    assert out[0]["user_stopped_is_eot_fallback"] is True
    assert out[0]["primary_latency_ms"] is not None


def test_language_resolved_at_finalize_from_provider():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append, language_provider=lambda: ("hi", "hi"))
    obs.note_user_turn(turn_id=1, user_text="haan", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, BotStartedSpeakingFrame())
    assert out[0]["detected_language"] == "hi"
    assert out[0]["response_language"] == "hi"


def test_ms_helper():
    assert _ms(1.5, 1.0) == 500.0
    assert _ms(None, 1.0) is None
    assert _ms(1.0, None) is None


def test_turn_record_finalize_computes_breakdown():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)

    obs._pending_user_stopped_at = 100.0
    obs.note_user_turn(
        turn_id=1, user_text="hello", stt_eot_received_at=100.3, turn_committed_at=100.35
    )
    obs.note_language(detected_language="en", response_language="en")

    rec = obs._open
    rec["llm_request_started_at"] = 100.4
    rec["llm_first_token_or_text_at"] = 100.7
    rec["llm_completed_at"] = 101.0
    rec["tts_request_started_at"] = 101.05
    rec["first_audio_frame_sent_at"] = 101.2
    rec["assistant_text"] = "hi there"
    obs._finalize()

    assert len(out) == 1
    r = out[0]
    assert r["turn_id"] == 1
    assert r["user_text"] == "hello"
    assert r["assistant_text"] == "hi there"
    assert r["detected_language"] == "en"
    assert r["response_language"] == "en"
    assert r["primary_latency_ms"] == round((101.2 - 100.0) * 1000, 1)
    assert r["eot_delay_ms"] == round((100.3 - 100.0) * 1000, 1)
    assert r["commit_delay_ms"] == round((100.35 - 100.3) * 1000, 1)
    assert r["llm_ttfb_ms"] == round((100.7 - 100.4) * 1000, 1)
    assert r["llm_total_ms"] == round((101.0 - 100.4) * 1000, 1)
    assert r["tts_first_audio_ms"] == round((101.2 - 101.05) * 1000, 1)


def test_note_user_turn_consumes_pending_user_stopped():
    obs = TurnMetricsObserver(on_record=lambda r: None)
    obs._pending_user_stopped_at = 50.0
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=50.2, turn_committed_at=50.25)
    assert obs._open["user_stopped_speaking_at"] == 50.0
    assert obs._pending_user_stopped_at is None
