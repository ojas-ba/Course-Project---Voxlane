import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from main import app
from app.runtime import ACTIVE_SESSIONS

@pytest.fixture
def client():
    ACTIVE_SESSIONS.clear()
    
    with patch("main.init_database", new_callable=AsyncMock), \
         patch("main.close_database", new_callable=AsyncMock):
         
        with TestClient(app) as test_client:
            yield test_client
            
    ACTIVE_SESSIONS.clear()

def test_dashboard_render(client):
    with patch("app.routers.dashboard._fetch_recent_logs", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = ([], True)  # Empty logs, DB connected
        
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "<title>Voice Agent Telemetry Dashboard</title>" in response.text
        assert "htmx.org" in response.text

def test_dashboard_sessions_empty(client):
    response = client.get("/dashboard/sessions")
    assert response.status_code == 200
    assert "No active voice sessions." in response.text

def test_dashboard_sessions_active(client):
    # Mock an active session
    ACTIVE_SESSIONS["test_call_sid"] = {
        "call_sid": "test_call_sid",
        "stream_sid": "test_stream_sid",
        "config_snapshot": {"LLM_MODEL": "test-model"},
        "metrics": {},
        "transcript": [],
        "first_latency": None,
    }
    
    response = client.get("/dashboard/sessions")
    assert response.status_code == 200
    assert "SID: test_call_si..." in response.text
    assert "Model: test-model" in response.text
