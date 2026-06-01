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


class _FakeCollection:
    def __init__(self, document):
        self._document = document

    async def find_one(self, query):
        return self._document


class _FakeDatabase:
    def __init__(self, document):
        self._document = document

    def __getitem__(self, name):
        assert name == "call_logs"
        return _FakeCollection(self._document)


def test_dashboard_log_detail_renders_transcript(client):
    log_document = {
        "call_sid": "call_123",
        "completed": True,
        "error": None,
        "config_snapshot": {
            "LLM_MODEL": "test-model",
            "TTS_VOICE_ID": "test-voice",
            "TTS_MODEL": "test-tts",
            "INITIAL_GREETING_PROMPT": "Hi there",
        },
        "metrics": {
            "first_bot_speech_latency_secs": 0.8,
            "turn_count": 1,
            "turns": [],
        },
        "transcript": [
            {
                "event_index": 1,
                "role": "assistant",
                "phase": "greeting",
                "text": "Hi, is now a good time?",
            },
            {
                "event_index": 2,
                "role": "user",
                "phase": "readiness",
                "text": "Yes, go ahead",
            }
        ],
    }

    with patch("app.routers.dashboard.get_database", return_value=_FakeDatabase(log_document)):
        response = client.get("/dashboard/logs/call_123")

    assert response.status_code == 200
    assert "Conversation Transcript" in response.text
    assert "Event 1" in response.text
    assert "Greeting" in response.text
    assert "Hi, is now a good time?" in response.text
    assert "Yes, go ahead" in response.text


def test_dashboard_log_detail_shows_empty_transcript_state(client):
    log_document = {
        "call_sid": "call_123",
        "completed": True,
        "error": None,
        "config_snapshot": {
            "LLM_MODEL": "test-model",
            "TTS_VOICE_ID": "test-voice",
            "TTS_MODEL": "test-tts",
            "INITIAL_GREETING_PROMPT": "Hi there",
        },
        "metrics": {
            "first_bot_speech_latency_secs": None,
            "turn_count": 0,
            "turns": [],
        },
    }

    with patch("app.routers.dashboard.get_database", return_value=_FakeDatabase(log_document)):
        response = client.get("/dashboard/logs/call_123")

    assert response.status_code == 200
    assert "No transcript captured for this call." in response.text
