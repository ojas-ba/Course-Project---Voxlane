from unittest.mock import Mock

import pytest

from app.config import get_settings
from app.runtime import build_config_snapshot
from app.utils.metrics_logger import (
    build_turn_metrics,
    finalize_call_log,
    normalize_latency_breakdown,
)

def test_normalize_latency_breakdown():
    # Simulate a LatencyBreakdown object without importing Pipecat directly
    mock_breakdown = Mock()
    mock_breakdown.user_turn_start_time = 1.0
    mock_breakdown.user_turn_secs = 2.5
    
    # Mock TTFB
    ttfb1 = Mock()
    ttfb1.start_time = 3.5
    ttfb1.processor = "LLM"
    ttfb1.duration_secs = 0.5
    mock_breakdown.ttfb = [ttfb1]
    
    # Mock function calls
    mock_breakdown.function_calls = []
    
    # Mock text aggregation
    ta = Mock()
    ta.start_time = 4.0
    ta.processor = "TTS"
    ta.duration_secs = 0.1
    mock_breakdown.text_aggregation = ta

    normalized = normalize_latency_breakdown(mock_breakdown)
    
    assert len(normalized) == 3
    assert normalized[0]["time"] == 1.0
    assert normalized[0]["event"] == "User turn: 2.500s"
    
    assert normalized[1]["time"] == 3.5
    assert normalized[1]["event"] == "LLM: TTFB 0.500s"
    
    assert normalized[2]["time"] == 4.0
    assert normalized[2]["event"] == "TTS: text aggregation 0.100s"

def test_normalize_latency_breakdown_missing_fields():
    mock_breakdown = Mock()
    mock_breakdown.user_turn_start_time = None
    mock_breakdown.user_turn_secs = None
    mock_breakdown.ttfb = []
    mock_breakdown.function_calls = []
    mock_breakdown.text_aggregation = None
    
    normalized = normalize_latency_breakdown(mock_breakdown)
    assert len(normalized) == 0


def test_build_turn_metrics_summary_basic():
    mock_breakdown = Mock()
    mock_breakdown.user_turn_start_time = 1.0
    mock_breakdown.user_turn_secs = 0.6

    ttfb_stt = Mock()
    ttfb_stt.start_time = 1.5
    ttfb_stt.processor = "Deepgram"
    ttfb_stt.duration_secs = 0.3

    ttfb_llm = Mock()
    ttfb_llm.start_time = 2.0
    ttfb_llm.processor = "Cerebras"
    ttfb_llm.duration_secs = 0.4

    ttfb_tts = Mock()
    ttfb_tts.start_time = 2.5
    ttfb_tts.processor = "Cartesia"
    ttfb_tts.duration_secs = 0.5

    ttfb_unmapped = Mock()
    ttfb_unmapped.start_time = 2.7
    ttfb_unmapped.processor = "OtherProc"
    ttfb_unmapped.duration_secs = 0.2

    mock_breakdown.ttfb = [ttfb_stt, ttfb_llm, ttfb_tts, ttfb_unmapped]

    fc = Mock()
    fc.start_time = 2.6
    fc.function_name = "do_work"
    fc.duration_secs = 0.2
    mock_breakdown.function_calls = [fc]

    ta = Mock()
    ta.start_time = 2.9
    ta.processor = "Cartesia"
    ta.duration_secs = 0.1
    mock_breakdown.text_aggregation = ta

    turn = build_turn_metrics(latency_secs=3.0, breakdown=mock_breakdown)

    assert turn.latency_summary is not None
    summary = turn.latency_summary
    assert summary.total_latency_secs == 3.0
    assert summary.user_turn_secs == 0.6
    assert summary.stt_ttfb_secs == 0.3
    assert summary.llm_ttfb_secs == 0.4
    assert summary.tts_ttfb_secs == 0.5
    assert summary.text_aggregation_secs == 0.1
    assert summary.function_call_secs_total == pytest.approx(0.2)
    assert len(summary.unmapped_ttfb_entries) == 1
    assert summary.unmapped_ttfb_entries[0].processor == "OtherProc"
    # 3.0 - (0.6 + 0.3 + 0.4 + 0.5 + 0.1 + 0.2 + 0.2) = 0.7
    assert summary.residual_secs == pytest.approx(0.7)


def test_build_turn_metrics_ambiguous_ttfb_sets_none():
    mock_breakdown = Mock()
    mock_breakdown.user_turn_start_time = 1.0
    mock_breakdown.user_turn_secs = 0.5

    ttfb_one = Mock()
    ttfb_one.start_time = 1.5
    ttfb_one.processor = "Deepgram"
    ttfb_one.duration_secs = 0.2

    ttfb_two = Mock()
    ttfb_two.start_time = 1.5
    ttfb_two.processor = "Deepgram"
    ttfb_two.duration_secs = 0.25

    mock_breakdown.ttfb = [ttfb_one, ttfb_two]
    mock_breakdown.function_calls = []
    mock_breakdown.text_aggregation = None

    turn = build_turn_metrics(latency_secs=2.0, breakdown=mock_breakdown)
    assert turn.latency_summary is not None
    assert turn.latency_summary.stt_ttfb_secs is None
    assert turn.latency_summary.residual_secs is None


def test_build_turn_metrics_optional_fields_none():
    mock_breakdown = Mock()
    mock_breakdown.user_turn_start_time = 1.0
    mock_breakdown.user_turn_secs = 0.5

    ttfb_stt = Mock()
    ttfb_stt.start_time = 1.5
    ttfb_stt.processor = "Deepgram"
    ttfb_stt.duration_secs = 0.2

    ttfb_llm = Mock()
    ttfb_llm.start_time = 1.8
    ttfb_llm.processor = "Cerebras"
    ttfb_llm.duration_secs = 0.3

    ttfb_tts = Mock()
    ttfb_tts.start_time = 2.2
    ttfb_tts.processor = "Cartesia"
    ttfb_tts.duration_secs = 0.4

    mock_breakdown.ttfb = [ttfb_stt, ttfb_llm, ttfb_tts]
    mock_breakdown.function_calls = None
    mock_breakdown.text_aggregation = None

    turn = build_turn_metrics(latency_secs=2.0, breakdown=mock_breakdown)
    assert turn.latency_summary is not None
    summary = turn.latency_summary
    assert summary.total_latency_secs == 2.0
    assert summary.user_turn_secs == 0.5
    assert summary.stt_ttfb_secs == 0.2
    assert summary.llm_ttfb_secs == 0.3
    assert summary.tts_ttfb_secs == 0.4
    assert summary.text_aggregation_secs is None
    assert summary.function_call_secs_total is None
    assert len(summary.unmapped_ttfb_entries) == 0
    # 2.0 - (0.5 + 0.2 + 0.3 + 0.4) = 0.6
    assert summary.residual_secs == pytest.approx(0.6)


def test_finalize_call_log_includes_transcript():
    call_log = finalize_call_log(
        call_sid="call_123",
        stream_sid="stream_456",
        config_snapshot={"LLM_MODEL": "test-model"},
        conversation_state={
            "phase": "collecting",
            "slots": {"name": {"status": "collected", "value": "Ana"}},
        },
        metrics={
            "first_bot_speech_latency_secs": 0.9,
            "turn_count": 1,
            "turns": [],
        },
        transcript=[
            {
                "event_index": 1,
                "role": "user",
                "phase": "task",
                "text": "Hello",
            },
            {
                "event_index": 2,
                "role": "assistant",
                "phase": "task",
                "text": "Hi there",
            }
        ],
        completed=True,
        error=None,
    )

    assert call_log.metrics.turn_count == 1
    assert len(call_log.transcript) == 2
    assert call_log.transcript[0].event_index == 1
    assert call_log.transcript[0].text == "Hello"
    assert call_log.conversation_state == {
        "phase": "collecting",
        "slots": {"name": {"status": "collected", "value": "Ana"}},
    }
    assert call_log.conversation_outcome == "incomplete"
    assert call_log.final_conversation_phase == "collecting"


def test_finalize_call_log_skips_incomplete_turns():
    call_log = finalize_call_log(
        call_sid="call_123",
        stream_sid="stream_456",
        config_snapshot={"LLM_MODEL": "test-model"},
        metrics={},
        transcript=[],
        completed=False,
        error="User disconnected",
    )

    assert call_log.transcript == []
    assert call_log.completed is False
    assert call_log.error == "User disconnected"


def test_finalize_call_log_accepts_expanded_v2_snapshot():
    snapshot = build_config_snapshot(get_settings())

    call_log = finalize_call_log(
        call_sid="call_123",
        stream_sid="stream_456",
        config_snapshot=snapshot,
        metrics={},
        transcript=[],
        completed=True,
        error=None,
    )

    assert call_log.config_snapshot["snapshot_version"] == "v2_phase1"
    assert call_log.config_snapshot["bot_profile"]["id"] == "default_demo_bot"


def test_finalize_call_log_separates_transport_completed_from_readiness_failed():
    snapshot = build_config_snapshot(get_settings())

    call_log = finalize_call_log(
        call_sid="call_123",
        stream_sid="stream_456",
        config_snapshot=snapshot,
        conversation_state={
            "phase": "readiness",
            "slots": {
                "name": {"status": "missing", "value": None},
                "location": {"status": "missing", "value": None},
                "role": {"status": "missing", "value": None},
            },
        },
        metrics={
            "greeting_text_source": "fallback",
            "greeting_text_ready_before_dial": True,
            "greeting_llm_total_latency": 0.2,
            "first_tts_frame_queued_at": 1.0,
            "first_bot_audio_started_at": 2.0,
        },
        transcript=[],
        completed=True,
        error=None,
    )

    assert call_log.completed is True
    assert call_log.call_transport_status == "completed"
    assert call_log.conversation_outcome == "readiness_failed"
    assert call_log.final_conversation_phase == "readiness"
    assert call_log.slots_collected_count == 0
    assert call_log.required_slots_missing == ["name", "location", "role"]
    assert call_log.metrics.greeting_text_source == "fallback"
    assert call_log.metrics.greeting_text_ready_before_dial is True
    assert call_log.metrics.greeting_llm_total_latency == 0.2
    assert call_log.metrics.first_tts_frame_queued_at == 1.0
    assert call_log.metrics.first_bot_audio_started_at == 2.0


def test_finalize_call_log_marks_completed_logical_outcome_when_required_slots_collected():
    snapshot = build_config_snapshot(get_settings())

    call_log = finalize_call_log(
        call_sid="call_123",
        stream_sid="stream_456",
        config_snapshot=snapshot,
        conversation_state={
            "phase": "closing",
            "slots": {
                "name": {"status": "collected", "value": "Ojas"},
                "location": {"status": "collected", "value": "Bangalore"},
                "role": {"status": "collected", "value": "backend developer"},
            },
        },
        metrics={},
        transcript=[],
        completed=True,
        error=None,
    )

    assert call_log.conversation_outcome == "completed"
    assert call_log.final_conversation_phase == "closing"
    assert call_log.slots_collected_count == 3
    assert call_log.required_slots_missing == []
