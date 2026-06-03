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
