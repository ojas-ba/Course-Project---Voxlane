import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import httpx

from main import app
from app.services.exotel_client import ExotelOutboundClient
from app.config import Settings

@pytest.fixture
def client():
    with patch("main.init_database", new_callable=AsyncMock), \
         patch("main.close_database", new_callable=AsyncMock):
        with TestClient(app) as test_client:
            yield test_client

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
