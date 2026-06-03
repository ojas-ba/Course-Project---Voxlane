from app.services.call_metrics import TurnMetricsObserver, _ms


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
