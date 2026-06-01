import os
import pytest

from dotenv import load_dotenv

def _is_live_run_enabled(config: pytest.Config) -> bool:
    """Return True when live tests are explicitly requested."""
    env_flag = os.getenv("RUN_LIVE_TESTS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    markexpr = (config.getoption("-m") or "").strip().lower()
    return env_flag or ("live" in markexpr)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live tests by default unless explicitly enabled."""
    if _is_live_run_enabled(config):
        return

    skip_live = pytest.mark.skip(
        reason=(
            "Live provider test skipped by default. "
            "Run `pytest -m live` or `RUN_LIVE_TESTS=1 pytest`."
        )
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def mock_env_vars(request):
    """Ensure all required environment variables are set for tests."""
    from app.config import get_settings
    markers = [marker.name for marker in request.node.iter_markers()]
    if "live" in markers:
        # Resolve absolute path to project root .env
        dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
        loaded = load_dotenv(dotenv_path, override=True)
        get_settings.cache_clear()
        return

    os.environ["DEEPGRAM_API_KEY"] = "test_dg"
    os.environ["CEREBRAS_API_KEY"] = "test_cb"
    os.environ["CARTESIA_API_KEY"] = "test_ct"
    os.environ["SYSTEM_PROMPT"] = "test_prompt"
    os.environ["LLM_MODEL"] = "test_model"
    os.environ["LLM_TEMPERATURE"] = "0.5"
    os.environ["TTS_VOICE_ID"] = "test_voice"
    os.environ["TTS_MODEL"] = "test_tts"
    os.environ["INITIAL_GREETING_PROMPT"] = "test_greeting"
    os.environ["MONGO_URI"] = "mongodb://localhost:27017"
    os.environ["MONGO_DB_NAME"] = "test_db"
    
    # Exotel strict config
    os.environ["EXOTEL_ACCOUNT_SID"] = "test_exotel_acc"
    os.environ["EXOTEL_API_KEY"] = "test_exotel_key"
    os.environ["EXOTEL_API_TOKEN"] = "test_exotel_token"
    os.environ["EXOTEL_VIRTUAL_NUMBER"] = "09876543210"
    os.environ["EXOTEL_FLOW_ID"] = "test_flow"
    os.environ["PUBLIC_NGROK_URL"] = "test.ngrok.app"
    os.environ["EXOTEL_SUBDOMAIN"] = "api.in.exotel.com"
    
    get_settings.cache_clear()
