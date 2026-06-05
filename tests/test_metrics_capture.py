import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
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
    # New stage-breakdown deltas (the segments that localize a slow turn).
    assert r["user_stop_to_eot_ms"] == round((100.3 - 100.0) * 1000, 1)
    assert r["eot_to_turn_committed_ms"] == round((100.35 - 100.3) * 1000, 1)
    assert r["user_stop_to_llm_start_ms"] == round((100.4 - 100.0) * 1000, 1)
    assert r["turn_committed_to_llm_start_ms"] == round((100.4 - 100.35) * 1000, 1)
    assert r["llm_end_to_tts_start_ms"] == round((101.05 - 101.0) * 1000, 1)
    assert r["user_stop_to_first_audio_ms"] == round((101.2 - 100.0) * 1000, 1)
    assert r["earliest_user_stop_to_first_audio_ms"] == r["user_stop_to_first_audio_ms"]
    assert r["latest_user_stop_to_first_audio_ms"] == r["user_stop_to_first_audio_ms"]


def test_delayed_llm_start_surfaces_in_committed_to_llm_start():
    # A 12s gap between turn-commit and the LLM starting must be visible as the
    # committed_to_llm_start / user_stop_to_llm_start deltas, NOT hidden.
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    rec = {
        "turn_id": 1, "user_text": "x", "assistant_text": "y",
        "detected_language": "en", "response_language": "en",
        "user_stopped_speaking_at": 10.0, "user_stopped_is_eot_fallback": False,
        "stt_eot_received_at": 10.001, "turn_committed_at": 10.002,
        "llm_request_started_at": 22.0, "llm_first_token_or_text_at": 22.5,
        "llm_completed_at": 22.6, "tts_request_started_at": 22.63,
        "first_audio_frame_sent_at": 22.7, "error_stage": None, "error_message": None,
    }
    obs._emit(rec)
    r = out[0]
    assert r["turn_committed_to_llm_start_ms"] == round((22.0 - 10.002) * 1000, 1)  # ~11998
    assert r["user_stop_to_llm_start_ms"] == round((22.0 - 10.0) * 1000, 1)         # 12000
    assert r["llm_ttfb_ms"] == round((22.5 - 22.0) * 1000, 1)                       # 500 (LLM was fine)


def test_merged_turn_stores_earliest_latest_and_subturns():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=3, user_text="Hello.", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=4, user_text="Computer science.", stt_eot_received_at=2.0, turn_committed_at=2.0)
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMTextFrame("<en> ok"))
    _push(obs, LLMFullResponseEndFrame())
    obs.note_assistant_text("Got it.")
    _push(obs, BotStartedSpeakingFrame())

    rec = out[0]
    assert rec["merged_user_turn_ids"] == [3, 4]
    assert rec["merged_user_texts"] == ["Hello.", "Computer science."]
    assert len(rec["merged_user_stop_times"]) == 2
    assert rec["merged_turn_commit_times"] == [1.0, 2.0]
    assert rec["earliest_user_stop_to_first_audio_ms"] is not None
    assert rec["latest_user_stop_to_first_audio_ms"] is not None
    # The latest sub-stop is later, so its latency is <= the earliest-anchored one.
    assert rec["latest_user_stop_to_first_audio_ms"] <= rec["earliest_user_stop_to_first_audio_ms"]
    # Latest commit anchor (2.0) is later than earliest (1.0), so the latest
    # Commit->LLM-start (the real backend delay) is the smaller number (LAT-1).
    assert rec["latest_turn_committed_to_llm_start_ms"] < rec["earliest_turn_committed_to_llm_start_ms"]


def test_merged_turn_earliest_and_latest_commit_to_llm_start():
    # LAT-1: earliest commit (first sub-utterance) inflates Commit->LLM start by
    # the inter-utterance pause; the latest commit gives the real backend delay.
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    rec = {
        "turn_id": 3, "user_text": "a\nb", "assistant_text": "ok",
        "detected_language": "en", "response_language": "en",
        "user_stopped_speaking_at": 1.0, "latest_user_stopped_speaking_at": 9.0,
        "user_stopped_is_eot_fallback": False,
        "stt_eot_received_at": 1.0, "turn_committed_at": 1.0,
        "earliest_turn_committed_at": 1.0, "latest_turn_committed_at": 9.0,
        "merged_user_turn_ids": [3, 4],
        "llm_request_started_at": 10.0, "llm_first_token_or_text_at": 10.4,
        "llm_completed_at": 10.5, "tts_request_started_at": 10.55,
        "first_audio_frame_sent_at": 10.6, "error_stage": None, "error_message": None,
    }
    obs._emit(rec)
    r = out[0]
    assert r["earliest_turn_committed_to_llm_start_ms"] == round((10.0 - 1.0) * 1000, 1)  # 9000 (inflated)
    assert r["latest_turn_committed_to_llm_start_ms"] == round((10.0 - 9.0) * 1000, 1)    # 1000 (real)
    assert r["earliest_user_stop_to_first_audio_ms"] == round((10.6 - 1.0) * 1000, 1)
    assert r["latest_user_stop_to_first_audio_ms"] == round((10.6 - 9.0) * 1000, 1)


def test_normal_turn_latest_commit_equals_committed_to_llm_start():
    # For a non-merged turn, earliest==latest==turn_committed_to_llm_start_ms, so
    # the UI's latest-anchored value is always correct for normal turns too.
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    rec = {
        "turn_id": 1, "user_text": "x", "assistant_text": "y",
        "detected_language": "en", "response_language": "en",
        "user_stopped_speaking_at": 100.0, "user_stopped_is_eot_fallback": False,
        "stt_eot_received_at": 100.0, "turn_committed_at": 100.0,
        "llm_request_started_at": 100.5, "llm_first_token_or_text_at": 100.9,
        "llm_completed_at": 101.0, "tts_request_started_at": 101.05,
        "first_audio_frame_sent_at": 101.1, "error_stage": None, "error_message": None,
    }
    obs._emit(rec)
    r = out[0]
    assert r["turn_committed_to_llm_start_ms"] == round((100.5 - 100.0) * 1000, 1)
    assert r["latest_turn_committed_to_llm_start_ms"] == r["turn_committed_to_llm_start_ms"]
    assert r["earliest_turn_committed_to_llm_start_ms"] == r["turn_committed_to_llm_start_ms"]


def test_negative_llm_end_to_tts_is_preserved_not_clamped():
    # MET-1: under streaming, TTS can start before the LLM end frame -> the metric
    # is legitimately negative (overlap). It must be PRESERVED, never clamped, so a
    # real (large positive) hand-off stall stays visible.
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    rec = {
        "turn_id": 1, "user_text": "x", "assistant_text": "y",
        "detected_language": "en", "response_language": "en",
        "user_stopped_speaking_at": 100.0, "user_stopped_is_eot_fallback": False,
        "stt_eot_received_at": 100.0, "turn_committed_at": 100.0,
        "llm_request_started_at": 100.5, "llm_first_token_or_text_at": 100.9,
        "llm_completed_at": 101.0, "tts_request_started_at": 100.99,  # TTS started before LLM end
        "first_audio_frame_sent_at": 101.1, "error_stage": None, "error_message": None,
    }
    obs._emit(rec)
    r = out[0]
    assert r["llm_end_to_tts_start_ms"] == round((100.99 - 101.0) * 1000, 1)
    assert r["llm_end_to_tts_start_ms"] < 0


def test_bot_was_speaking_when_user_stopped_flag():
    obs = TurnMetricsObserver(on_record=lambda r: None)
    _push(obs, BotStartedSpeakingFrame())   # bot is speaking
    _push(obs, UserStoppedSpeakingFrame())  # ...user stops while it speaks
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0)
    assert obs._pending_turns[0]["bot_was_speaking_when_user_stopped"] is True


def test_bot_not_speaking_when_user_stopped_flag():
    obs = TurnMetricsObserver(on_record=lambda r: None)
    _push(obs, BotStartedSpeakingFrame())
    _push(obs, BotStoppedSpeakingFrame())   # bot finished
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0)
    assert obs._pending_turns[0]["bot_was_speaking_when_user_stopped"] is False


def test_memory_duration_attached_to_turn():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    obs.note_memory_timing(duration_ms=12.5, context_message_count=7)
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMFullResponseEndFrame())
    obs.note_assistant_text("ok")
    _push(obs, BotStartedSpeakingFrame())
    assert out[0]["memory_processor_duration_ms"] == 12.5
    assert out[0]["context_message_count"] == 7


def test_stt_ready_flag_persisted():
    obs = TurnMetricsObserver(on_record=lambda r: None)
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0, stt_ready=False)
    assert obs._pending_turns[0]["stt_ready_before_user_turn"] is False


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


def test_end_frame_alone_does_not_finalize_before_parser_note():
    # P1-1 guard: LLMFullResponseEndFrame is timing only; the record must wait
    # for the parser's note_assistant_text even if audio already arrived.
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, BotStartedSpeakingFrame())   # audio first
    _push(obs, LLMFullResponseEndFrame())   # end frame alone must NOT finalize
    assert out == []
    obs.note_assistant_text("done")          # parser signal finalizes
    assert len(out) == 1
    assert out[0]["assistant_text"] == "done"
