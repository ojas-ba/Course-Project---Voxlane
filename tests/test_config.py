"""Server settings contain secrets, never conversation tuning."""

import os

from app.config import Settings, V1_SAMPLE_RATE


def test_sample_rate_remains_fixed():
    assert V1_SAMPLE_RATE == 16000


def test_settings_need_only_three_provider_keys(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("EXOTEL_", "MONGO_", "PUBLIC_NGROK")):
            monkeypatch.delenv(name)
    settings = Settings(_env_file=None)
    assert settings.deepgram_api_key == "test_dg"
    assert settings.host == "127.0.0.1"
    assert set(Settings.model_fields) == {"host", "port", "deepgram_api_key", "cerebras_api_key", "cartesia_api_key"}
    assert "test_dg" not in repr(settings)
