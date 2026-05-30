from unittest.mock import Mock

import pytest

from app.utils.metrics_logger import build_turn_metrics, normalize_latency_breakdown

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
