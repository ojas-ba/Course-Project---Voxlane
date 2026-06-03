import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import httpx

from main import app
from app.runtime import PENDING_OUTBOUND_CALLS
from app.services.exotel_client import ExotelCallResult, ExotelOutboundClient
from app.config import Settings
from app.services.config_repository import DEFAULT_BOT_PROFILE_ID

@pytest.fixture
def client():
    with patch("main.init_database", new_callable=AsyncMock), \
         patch("main.close_database", new_callable=AsyncMock):
        PENDING_OUTBOUND_CALLS.clear()
        with TestClient(app) as test_client:
            yield test_client
        PENDING_OUTBOUND_CALLS.clear()

def test_exotel_webhook_json_response(client):
    # The app is not explicitly configured with settings inside the TestClient
    # unless we mock get_settings. But lifespan sets it up. We can mock config.
    with patch("app.routers.telephony.get_settings") as mock_get_settings:
        mock_settings = Settings()
        mock_settings.public_ngrok_url = "test.ngrok.app"
        mock_get_settings.return_value = mock_settings
        
        response = client.get("/telephony/webhook?CallSid=123")
        assert response.status_code == 200
        assert response.json() == {"url": "wss://test.ngrok.app/telephony/exotel?sample-rate=16000"}


def test_dashboard_outbound_creates_pending_context(client):
    mock_exotel_client = AsyncMock()
    mock_exotel_client.make_call.return_value = ExotelCallResult(
        success=True,
        call_sid="call_123",
    )
    client.app.state.exotel_client = mock_exotel_client

    with patch(
        "app.routers.dashboard.generate_outbound_greeting_text",
        new_callable=AsyncMock,
    ) as mock_greeting:
        mock_greeting.return_value = "Hi, is now a good time to continue?"
        response = client.post(
            "/telephony/outbound",
            data={"to_number": "+919876543210"},
        )

    assert response.status_code == 200
    assert "call_123" in response.text
    assert PENDING_OUTBOUND_CALLS["call_123"]["greeting_text"] == (
        "Hi, is now a good time to continue?"
    )
    greeting_metrics = PENDING_OUTBOUND_CALLS["call_123"]["greeting_metrics"]
    assert greeting_metrics["greeting_text_source"] == "llm"
    assert greeting_metrics["greeting_text_ready_before_dial"] is True
    assert greeting_metrics["greeting_llm_total_latency"] is not None
    assert greeting_metrics["greeting_generation_completed_at"] >= (
        greeting_metrics["greeting_generation_started_at"]
    )
    assert PENDING_OUTBOUND_CALLS["call_123"]["config_snapshot"]["SAMPLE_RATE"] == 16000
    assert (
        PENDING_OUTBOUND_CALLS["call_123"]["config_snapshot"]["bot_profile"]["id"]
        == DEFAULT_BOT_PROFILE_ID
    )
    assert (
        PENDING_OUTBOUND_CALLS["call_123"]["config_snapshot"]["snapshot_version"]
        == "v2_phase1"
    )


def test_dashboard_outbound_accepts_selected_profile_id(client):
    mock_exotel_client = AsyncMock()
    mock_exotel_client.make_call.return_value = ExotelCallResult(
        success=True,
        call_sid="call_456",
    )
    client.app.state.exotel_client = mock_exotel_client

    with patch(
        "app.routers.dashboard.generate_outbound_greeting_text",
        new_callable=AsyncMock,
    ) as mock_greeting:
        mock_greeting.return_value = "Hi, is now a good time to continue?"
        response = client.post(
            "/telephony/outbound",
            data={
                "to_number": "+919876543210",
                "bot_profile_id": DEFAULT_BOT_PROFILE_ID,
            },
        )

    snapshot = PENDING_OUTBOUND_CALLS["call_456"]["config_snapshot"]
    assert response.status_code == 200
    assert snapshot["profile_resolution"] == {
        "requested_profile_id": DEFAULT_BOT_PROFILE_ID,
        "resolved_profile_id": DEFAULT_BOT_PROFILE_ID,
        "used_fallback": False,
    }


def test_dashboard_outbound_unknown_profile_falls_back(client):
    mock_exotel_client = AsyncMock()
    mock_exotel_client.make_call.return_value = ExotelCallResult(
        success=True,
        call_sid="call_789",
    )
    client.app.state.exotel_client = mock_exotel_client

    with patch(
        "app.routers.dashboard.generate_outbound_greeting_text",
        new_callable=AsyncMock,
    ) as mock_greeting:
        mock_greeting.return_value = "Hi, is now a good time to continue?"
        response = client.post(
            "/telephony/outbound",
            data={
                "to_number": "+919876543210",
                "bot_profile_id": "does_not_exist",
            },
        )

    snapshot = PENDING_OUTBOUND_CALLS["call_789"]["config_snapshot"]
    assert response.status_code == 200
    assert snapshot["bot_profile"]["id"] == DEFAULT_BOT_PROFILE_ID
    assert snapshot["profile_resolution"] == {
        "requested_profile_id": "does_not_exist",
        "resolved_profile_id": DEFAULT_BOT_PROFILE_ID,
        "used_fallback": True,
    }


def test_dashboard_outbound_rejects_success_without_call_sid(client):
    mock_exotel_client = AsyncMock()
    mock_exotel_client.make_call.return_value = ExotelCallResult(
        success=True,
        call_sid=None,
    )
    client.app.state.exotel_client = mock_exotel_client

    with patch(
        "app.routers.dashboard.generate_outbound_greeting_text",
        new_callable=AsyncMock,
    ) as mock_greeting:
        mock_greeting.return_value = "Hi, is now a good time to continue?"
        response = client.post(
            "/telephony/outbound",
            data={"to_number": "+919876543210"},
        )

    assert response.status_code == 200
    assert "Exotel did not return a CallSid." in response.text
    assert PENDING_OUTBOUND_CALLS == {}


def test_dashboard_outbound_records_fallback_greeting_metrics_on_generation_failure(client):
    mock_exotel_client = AsyncMock()
    mock_exotel_client.make_call.return_value = ExotelCallResult(
        success=True,
        call_sid="call_fallback",
    )
    client.app.state.exotel_client = mock_exotel_client

    with patch(
        "app.routers.dashboard.generate_outbound_greeting_text",
        new_callable=AsyncMock,
    ) as mock_greeting:
        mock_greeting.side_effect = ValueError("LLM generation returned no text")
        response = client.post(
            "/telephony/outbound",
            data={"to_number": "+919876543210"},
        )

    assert response.status_code == 200
    pending = PENDING_OUTBOUND_CALLS["call_fallback"]
    assert pending["greeting_text"] is None
    assert pending["greeting_error"] == "LLM generation returned no text"
    assert pending["greeting_metrics"]["greeting_text_source"] == "fallback"
    assert pending["greeting_metrics"]["greeting_text_ready_before_dial"] is True
    assert pending["greeting_metrics"]["greeting_llm_total_latency"] is not None

@pytest.mark.asyncio
async def test_exotel_outbound_client_success():
    settings = Settings(
        EXOTEL_ACCOUNT_SID="test_acc",
        EXOTEL_API_KEY="test_key",
        EXOTEL_API_TOKEN="test_token",
        EXOTEL_VIRTUAL_NUMBER="1234567890",
        EXOTEL_FLOW_ID="flow_123",
        EXOTEL_SUBDOMAIN="api.in.exotel.com",
        PUBLIC_NGROK_URL="test.ngrok.app",
    )
    
    exotel_client = ExotelOutboundClient(settings)
    
    mock_request = httpx.Request("POST", "https://api.in.exotel.com")
    mock_response = httpx.Response(
        200, 
        request=mock_request,
        text="<TwilioResponse><Call><Sid>call_123</Sid></Call></TwilioResponse>"
    )
    
    with patch.object(exotel_client._client, 'post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        result = await exotel_client.make_call("+919876543210")
        
        assert result.success is True
        assert result.call_sid == "call_123"
        
        # Verify call arguments
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        form_data = kwargs["data"]
        
        # Assert format stripping logic
        assert form_data["From"] == "09876543210"
        assert form_data["CallerId"] == "1234567890"
        assert form_data["Url"] == "https://my.in.exotel.com/test_acc/exoml/start_voice/flow_123"


@pytest.mark.asyncio
async def test_exotel_outbound_client_missing_call_sid_is_failure():
    settings = Settings(
        EXOTEL_ACCOUNT_SID="test_acc",
        EXOTEL_API_KEY="test_key",
        EXOTEL_API_TOKEN="test_token",
        EXOTEL_VIRTUAL_NUMBER="1234567890",
        EXOTEL_FLOW_ID="flow_123",
        EXOTEL_SUBDOMAIN="api.in.exotel.com",
        PUBLIC_NGROK_URL="test.ngrok.app",
    )
    exotel_client = ExotelOutboundClient(settings)

    mock_request = httpx.Request("POST", "https://api.in.exotel.com")
    mock_response = httpx.Response(
        200,
        request=mock_request,
        text="<TwilioResponse><Call></Call></TwilioResponse>",
    )

    with patch.object(exotel_client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await exotel_client.make_call("+919876543210")

        assert result.success is False
        assert result.call_sid is None
        assert result.error == "Exotel response did not include a CallSid"

@pytest.mark.asyncio
async def test_exotel_outbound_client_error():
    settings = Settings(
        EXOTEL_ACCOUNT_SID="test_acc",
        EXOTEL_API_KEY="test_key",
        EXOTEL_API_TOKEN="test_token",
        EXOTEL_VIRTUAL_NUMBER="1234567890",
        EXOTEL_FLOW_ID="flow_123",
        PUBLIC_NGROK_URL="test.ngrok.app",
    )
    exotel_client = ExotelOutboundClient(settings)
    
    with patch.object(exotel_client._client, 'post', new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.RequestError("Network error")
        
        result = await exotel_client.make_call("9876543210")
        assert result.success is False
        assert "Network error" in result.error
