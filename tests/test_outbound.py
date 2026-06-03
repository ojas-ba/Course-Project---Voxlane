import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from main import app
from app.config import get_settings
from app.models.runtime_config_models import RuntimeConfig
from app.runtime import PENDING_OUTBOUND_CALLS
from app.services.exotel_client import ExotelCallResult


@pytest.fixture
def client():
    with patch("main.init_database", new_callable=AsyncMock), \
         patch("main.close_database", new_callable=AsyncMock):
        PENDING_OUTBOUND_CALLS.clear()
        with TestClient(app) as test_client:
            yield test_client
        PENDING_OUTBOUND_CALLS.clear()


def _default_config():
    return RuntimeConfig.default(get_settings())


def test_exotel_webhook_json_response(client):
    response = client.get("/telephony/webhook?CallSid=123")
    assert response.status_code == 200
    body = response.json()
    assert body["url"].startswith("wss://")
    assert body["url"].endswith("/telephony/exotel?sample-rate=16000")


def test_outbound_greeting_success_dials(client):
    mock_exotel = AsyncMock()
    mock_exotel.make_call.return_value = ExotelCallResult(success=True, call_sid="call_123")
    client.app.state.exotel_client = mock_exotel

    with patch("app.routers.dashboard.load_runtime_config", new_callable=AsyncMock) as mock_cfg, \
         patch("app.routers.dashboard.generate_outbound_greeting_text", new_callable=AsyncMock) as mock_greet:
        mock_cfg.return_value = _default_config()
        mock_greet.return_value = "Hi there, how can I help you today?"
        response = client.post("/telephony/outbound", data={"to_number": "+919876543210"})

    assert response.status_code == 200
    assert "call_123" in response.text
    mock_exotel.make_call.assert_awaited_once()
    ctx = PENDING_OUTBOUND_CALLS["call_123"]
    assert ctx["greeting_text"] == "Hi there, how can I help you today?"
    assert ctx["greeting_metrics"]["greeting_text_source"] == "llm"
    assert ctx["greeting_metrics"]["greeting_text_ready_before_dial"] is True
    assert ctx["config_snapshot"]["SAMPLE_RATE"] == 16000
    assert ctx["config_snapshot"]["snapshot_version"] == "v3"


def test_outbound_greeting_failure_does_not_dial(client):
    mock_exotel = AsyncMock()
    client.app.state.exotel_client = mock_exotel

    with patch("app.routers.dashboard.load_runtime_config", new_callable=AsyncMock) as mock_cfg, \
         patch("app.routers.dashboard.generate_outbound_greeting_text", new_callable=AsyncMock) as mock_greet:
        mock_cfg.return_value = _default_config()
        mock_greet.side_effect = ValueError("LLM produced no text")
        response = client.post("/telephony/outbound", data={"to_number": "+919876543210"})

    assert response.status_code == 200
    assert "not placed" in response.text.lower()
    mock_exotel.make_call.assert_not_called()
    assert PENDING_OUTBOUND_CALLS == {}


def test_outbound_config_load_failure_does_not_dial(client):
    mock_exotel = AsyncMock()
    client.app.state.exotel_client = mock_exotel

    with patch("app.routers.dashboard.load_runtime_config", new_callable=AsyncMock) as mock_cfg:
        mock_cfg.side_effect = RuntimeError("mongo down")
        response = client.post("/telephony/outbound", data={"to_number": "+919876543210"})

    assert response.status_code == 200
    assert "not placed" in response.text.lower()
    mock_exotel.make_call.assert_not_called()


def test_outbound_rejects_invalid_number(client):
    client.app.state.exotel_client = AsyncMock()
    response = client.post("/telephony/outbound", data={"to_number": "abc"})
    assert response.status_code == 200
    assert "E.164" in response.text
