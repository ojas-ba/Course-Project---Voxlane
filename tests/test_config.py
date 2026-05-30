import os
from unittest.mock import patch

from app.config import Settings, V1_SAMPLE_RATE

def test_v1_sample_rate_is_fixed():
    assert V1_SAMPLE_RATE == 16000

@patch.dict(os.environ, {
    "DEEPGRAM_API_KEY": "dg_key",
    "CEREBRAS_API_KEY": "cb_key",
    "CARTESIA_API_KEY": "ct_key",
    "SYSTEM_PROMPT": "You are a test agent.",
    "LLM_MODEL": "test-model",
    "LLM_TEMPERATURE": "0.5",
    "TTS_VOICE_ID": "voice_123",
    "TTS_MODEL": "tts-model",
    "INITIAL_GREETING_PROMPT": "Hello test.",
    "MONGO_URI": "mongodb://localhost",
    "MONGO_DB_NAME": "test_db",
    "EXOTEL_ACCOUNT_SID": "acc_sid",
    "EXOTEL_API_KEY": "api_key",
    "EXOTEL_API_TOKEN": "api_token",
    "EXOTEL_VIRTUAL_NUMBER": "1234567890",
    "EXOTEL_FLOW_ID": "flow_1",
    "EXOTEL_SUBDOMAIN": "api.test.exotel.com",
    "PUBLIC_NGROK_URL": "test.ngrok.app",
})
def test_settings_load_successfully():
    settings = Settings()
    
    assert settings.deepgram_api_key == "dg_key"
    assert settings.llm_temperature == 0.5
    assert settings.mongo_db_name == "test_db"
    assert settings.exotel_subdomain == "api.test.exotel.com"
    assert settings.public_ngrok_url == "test.ngrok.app"
