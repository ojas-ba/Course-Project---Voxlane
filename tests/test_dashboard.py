import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from main import app
from app.config import get_settings
from app.models.runtime_config_models import RuntimeConfig
from app.runtime import ACTIVE_SESSIONS


@pytest.fixture
def client():
    ACTIVE_SESSIONS.clear()
    with patch("main.init_database", new_callable=AsyncMock), \
         patch("main.close_database", new_callable=AsyncMock):
        with TestClient(app) as test_client:
            yield test_client
    ACTIVE_SESSIONS.clear()


def test_config_page_renders(client):
    response = client.get("/config")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Runtime Configuration" in response.text
    assert "flux-general-multi" in response.text  # default STT model shown
    assert "Place outbound call" in response.text
    # Grouped sections + new editable fields with helper text.
    assert "Defer connect on start" in response.text
    assert "Memory" in response.text
    assert "EOT timeout (ms)" in response.text


def test_root_renders_config(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Runtime Configuration" in response.text


def test_config_save(client):
    config = RuntimeConfig.default(get_settings())
    with patch(
        "app.routers.dashboard.save_runtime_config", new_callable=AsyncMock
    ) as mock_save:
        mock_save.return_value = config
        response = client.post(
            "/config",
            data={
                "stt_model": "flux-general-multi",
                "stt_language_hints": "en, hi",
                "stt_eot_threshold": "0.65",
                "stt_eot_timeout_ms": "700",
                "llm_model": "gpt-oss-120b",
                "llm_temperature": "0.7",
                "llm_max_tokens": "160",
                "llm_request_timeout_seconds": "10",
                "tts_model": "sonic-3.5",
                "tts_voice_id": "voice-x",
                "tts_language_mode": "per_turn",
                "system_prompt": "Be helpful.",
                "greeting_prompt": "Say hi.",
                "debug_log_level": "INFO",
            },
        )
    assert response.status_code == 200
    assert mock_save.await_count == 1
    # The saved config passed to save_runtime_config reflects the edits.
    saved = mock_save.call_args.args[0]
    assert saved.tts.voice_id == "voice-x"
    assert saved.prompt.system_prompt == "Be helpful."


def test_calls_list_renders(client):
    with patch("app.routers.dashboard._fetch_recent_logs", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = ([], True)
        response = client.get("/calls")
    assert response.status_code == 200
    assert "No calls yet." in response.text


def test_call_detail_renders_turn_table(client):
    fake_log = {
        "_id": "x",
        "call_sid": "call_abc",
        "stream_sid": "stream_abc",
        "config_snapshot": {"runtime_config": {"llm": {"model": "gpt-oss-120b"}}},
        "transcript": [{"role": "assistant", "phase": "greeting", "text": "Hi there!"}],
        "metrics": {
            "greeting_text_source": "llm",
            "turn_records": [
                {
                    "turn_id": 1, "user_text": "hello", "assistant_text": "hi",
                    "detected_language": "en", "response_language": "en",
                    "primary_latency_ms": 850.0, "eot_delay_ms": 300.0,
                    "llm_ttfb_ms": 200.0, "llm_total_ms": 400.0, "tts_first_audio_ms": 120.0,
                    "error_stage": None, "error_message": None,
                }
            ],
        },
    }

    async def _find_one(query):
        return fake_log

    fake_collection = {"call_logs": type("C", (), {"find_one": staticmethod(_find_one)})()}
    with patch("app.routers.dashboard.get_database", return_value=fake_collection):
        response = client.get("/calls/call_abc")

    assert response.status_code == 200
    assert "call_abc" in response.text
    assert "Hi there!" in response.text
    assert "hello" in response.text
    assert "850 ms" in response.text


def test_call_detail_renders_breakdown_merged_and_error(client):
    fake_log = {
        "_id": "x",
        "call_sid": "call_xyz",
        "stream_sid": "s",
        "config_snapshot": {"runtime_config": {}},
        "transcript": [{"role": "assistant", "phase": "greeting", "text": "Hi!"}],
        "metrics": {
            "greeting_text_source": "llm",
            "turn_records": [
                {
                    "turn_id": 3, "merged_user_turn_ids": [3, 4],
                    "merged_user_texts": ["Hello.", "Computer science."],
                    "merged_user_stop_times": [1.0, 12.0],
                    "user_text": "Hello.\nComputer science.", "assistant_text": "Samajh gaya.",
                    "detected_language": "hi", "response_language": "hi",
                    "user_stop_to_first_audio_ms": 12552.0, "primary_latency_ms": 12552.0,
                    "user_stop_to_llm_start_ms": 11800.0, "turn_committed_to_llm_start_ms": 11790.0,
                    "llm_ttfb_ms": 558.0, "llm_total_ms": 592.0, "llm_end_to_tts_start_ms": 30.0,
                    "tts_first_audio_ms": 114.0, "memory_processor_duration_ms": 3.2,
                    "earliest_user_stop_to_first_audio_ms": 12552.0,
                    "latest_user_stop_to_first_audio_ms": 700.0,
                    "bot_was_speaking_when_user_stopped": False,
                    "error_stage": "language_prefix_parse", "error_message": "missing tag",
                }
            ],
        },
    }

    async def _find_one(query):
        return fake_log

    fake_collection = {"call_logs": type("C", (), {"find_one": staticmethod(_find_one)})()}
    with patch("app.routers.dashboard.get_database", return_value=fake_collection):
        response = client.get("/calls/call_xyz")

    assert response.status_code == 200
    assert "merged 3, 4" in response.text                 # merged ids visible
    assert "User&#8594;LLM start" in response.text or "User→LLM start" in response.text  # breakdown chip
    assert "latest&#8594;audio" in response.text or "latest→audio" in response.text       # merged earliest/latest
    assert "language_prefix_parse" in response.text       # error visible
    assert "12552 ms" in response.text                    # primary latency badge
