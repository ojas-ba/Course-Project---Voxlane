"""Typed application configuration for the voice agent project."""

from functools import lru_cache
from typing import Final

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

V1_SAMPLE_RATE: Final[int] = 16000


class Settings(BaseSettings):
    """Environment-backed settings used across application phases."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    deepgram_api_key: str = Field(alias="DEEPGRAM_API_KEY")
    cerebras_api_key: str = Field(alias="CEREBRAS_API_KEY")
    cartesia_api_key: str = Field(alias="CARTESIA_API_KEY")
    system_prompt: str = Field(alias="SYSTEM_PROMPT")
    llm_model: str = Field(alias="LLM_MODEL")
    llm_temperature: float = Field(alias="LLM_TEMPERATURE")
    tts_voice_id: str = Field(alias="TTS_VOICE_ID")
    tts_model: str = Field(alias="TTS_MODEL")
    initial_greeting_prompt: str = Field(alias="INITIAL_GREETING_PROMPT")
    initial_greeting_instructions: str = Field(
        default="",
        alias="INITIAL_GREETING_INSTRUCTIONS",
    )
    initial_greeting_fallback_text: str = Field(
        default="",
        alias="INITIAL_GREETING_FALLBACK_TEXT",
    )
    outbound_greeting_prep_timeout_secs: float = Field(
        default=2.0,
        alias="OUTBOUND_GREETING_PREP_TIMEOUT_SECS",
    )
    outbound_greeting_start_delay_ms: int = Field(
        default=100,
        alias="OUTBOUND_GREETING_START_DELAY_MS",
    )
    pending_outbound_ttl_secs: float = Field(
        default=600.0,
        alias="PENDING_OUTBOUND_TTL_SECS",
    )
    mongo_uri: str = Field(alias="MONGO_URI")
    mongo_db_name: str = Field(alias="MONGO_DB_NAME")

    # --- Exotel Outbound Configuration ---
    exotel_account_sid: str = Field(alias="EXOTEL_ACCOUNT_SID")
    exotel_api_key: str = Field(alias="EXOTEL_API_KEY")
    exotel_api_token: str = Field(alias="EXOTEL_API_TOKEN")
    exotel_virtual_number: str = Field(alias="EXOTEL_VIRTUAL_NUMBER")
    exotel_flow_id: str = Field(alias="EXOTEL_FLOW_ID")
    exotel_subdomain: str = Field(
        default="api.in.exotel.com", alias="EXOTEL_SUBDOMAIN"
    )
    public_ngrok_url: str = Field(alias="PUBLIC_NGROK_URL")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance."""

    return Settings()
