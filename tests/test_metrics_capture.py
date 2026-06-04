import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    EndFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSStartedFrame,
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


def test_assistant_text_is_parser_fed_not_frame_scraped():
    # The raw LLM text carries the <en>/<hi> tag; assistant_text must come from
    # note_assistant_text (the prefix processor), never from LLMTextFrame.
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=1, user_text="hello", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMTextFrame("<en> Hi"))          # tagged raw text -> timing only
    _push(obs, LLMFullResponseEndFrame())
    obs.note_language(detected_language="en", response_language="en")
    obs.note_assistant_text("Hi there!")          # clean spoken text
    _push(obs, TTSStartedFrame())
    _push(obs, BotStartedSpeakingFrame())

    assert len(out) == 1
    rec = out[0]
    assert rec["assistant_text"] == "Hi there!"
    assert "<en>" not in rec["assistant_text"]
    assert rec["turn_id"] == 1
    assert rec["response_language"] == "en"
    assert rec["llm_first_token_or_text_at"] is not None
    assert rec["primary_latency_ms"] is not None


def test_two_eots_before_reply_merge_without_losing_ids():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=3, user_text="first", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=4, user_text="second", stt_eot_received_at=2.0, turn_committed_at=2.0)
    # A single bot response for both queued user turns.
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMTextFrame("<en> ok"))
    _push(obs, LLMFullResponseEndFrame())
    obs.note_assistant_text("Got it.")
    _push(obs, BotStartedSpeakingFrame())

    assert len(out) == 1
    rec = out[0]
    assert rec["turn_id"] == 3
    assert rec["merged_user_turn_ids"] == [3, 4]
    assert "first" in rec["user_text"] and "second" in rec["user_text"]
    assert rec["assistant_text"] == "Got it."
    assert rec["primary_latency_ms"] is not None


def test_user_stopped_falls_back_to_eot_when_no_vad_frame():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=5.0, turn_committed_at=5.1)
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMFullResponseEndFrame())
    obs.note_assistant_text("ok")
    _push(obs, BotStartedSpeakingFrame())
    assert out[0]["user_stopped_speaking_at"] == 5.0
    assert out[0]["user_stopped_is_eot_fallback"] is True
    assert out[0]["primary_latency_ms"] is not None


def test_language_resolved_at_finalize_from_provider():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append, language_provider=lambda: ("hi", "hi"))
    obs.note_user_turn(turn_id=1, user_text="haan", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMFullResponseEndFrame())
    obs.note_assistant_text("theek hai")
    _push(obs, BotStartedSpeakingFrame())
    assert out[0]["detected_language"] == "hi"
    assert out[0]["response_language"] == "hi"


def test_ms_helper():
    assert _ms(1.5, 1.0) == 500.0
    assert _ms(None, 1.0) is None
    assert _ms(1.0, None) is None


def test_emit_computes_breakdown():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    rec = {
        "turn_id": 1, "user_text": "hello", "assistant_text": "hi there",
        "detected_language": "en", "response_language": "en",
        "user_stopped_speaking_at": 100.0, "user_stopped_is_eot_fallback": False,
        "stt_eot_received_at": 100.3, "turn_committed_at": 100.35,
        "llm_request_started_at": 100.4, "llm_first_token_or_text_at": 100.7,
        "llm_completed_at": 101.0, "tts_request_started_at": 101.05,
        "first_audio_frame_sent_at": 101.2, "error_stage": None, "error_message": None,
    }
    obs._emit(rec)
    r = out[0]
    assert r["primary_latency_ms"] == round((101.2 - 100.0) * 1000, 1)
    assert r["eot_delay_ms"] == round((100.3 - 100.0) * 1000, 1)
    assert r["commit_delay_ms"] == round((100.35 - 100.3) * 1000, 1)
    assert r["llm_ttfb_ms"] == round((100.7 - 100.4) * 1000, 1)
    assert r["llm_total_ms"] == round((101.0 - 100.4) * 1000, 1)
    assert r["tts_first_audio_ms"] == round((101.2 - 101.05) * 1000, 1)


def test_note_user_turn_queues_pending_not_open():
    obs = TurnMetricsObserver(on_record=lambda r: None)
    obs._pending_user_stopped_at = 50.0
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=50.2, turn_committed_at=50.25)
    assert obs._open is None
    assert len(obs._pending_turns) == 1
    assert obs._pending_turns[0]["user_stopped_speaking_at"] == 50.0
    assert obs._pending_user_stopped_at is None


def test_flush_emits_pending_turn_on_call_end():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, EndFrame())
    assert len(out) == 1
    assert out[0]["error_stage"] == "no_bot_response"
