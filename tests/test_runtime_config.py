"""Local configuration, snapshot isolation, and cancellation-safe saves."""

import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from pydantic import ValidationError

from app.models.runtime_config_models import RuntimeConfig
from app.services import runtime_config
from app.services.config_store import ConfigStore
from app.services.runtime_config import build_effective_snapshot


def test_defaults_and_snapshot_are_browser_specific():
    config = RuntimeConfig.default()
    snapshot = build_effective_snapshot(config)
    assert config.llm.model == "qwen-3.8-27b"
    assert config.llm.reasoning_effort == "none"
    assert config.tts.voice_id == ""
    assert snapshot["GREETING_TEXT"] == config.prompt.greeting_text
    snapshot["runtime_config"]["llm"]["model"] = "changed"
    assert config.llm.model == "qwen-3.8-27b"
    assert snapshot["SAMPLE_RATE"] == 16000


def test_model_reasoning_combination_is_validated():
    with pytest.raises(ValidationError, match="GPT-OSS"):
        RuntimeConfig(llm={"model": "gpt-oss-120b", "reasoning_effort": "none"})
    assert RuntimeConfig(llm={"model": "gpt-oss-120b", "reasoning_effort": "low"})


@pytest.mark.asyncio
async def test_seed_edit_restart_and_cache_copy():
    store = ConfigStore()
    original = await store.load()
    original.tts.voice_id = "chosen-voice"
    await store.save(original)
    copy = store.get()
    copy.tts.voice_id = "not-saved"
    assert store.get().tts.voice_id == "chosen-voice"
    assert (await ConfigStore().load()).tts.voice_id == "chosen-voice"


@pytest.mark.asyncio
async def test_failed_write_preserves_cache():
    store = ConfigStore()
    await store.load()
    changed = store.get()
    changed.tts.voice_id = "new"
    with patch("app.storage.write_config", new=AsyncMock(side_effect=OSError("denied"))):
        with pytest.raises(OSError):
            await store.save(changed)
    assert store.get().tts.voice_id == ""


@pytest.mark.asyncio
async def test_canceled_request_finishes_file_and_cache_transaction():
    store = ConfigStore()
    await store.load()
    entered, release = asyncio.Event(), asyncio.Event()
    from app import storage
    actual = storage.write_config

    async def slow(config):
        entered.set()
        await release.wait()
        await actual(config)

    changed = store.get()
    changed.tts.voice_id = "kept"
    with patch("app.storage.write_config", new=slow):
        request = asyncio.create_task(store.save(changed))
        await entered.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        release.set()
        await store.close()
    assert store.get().tts.voice_id == "kept"
    assert (await ConfigStore().load()).tts.voice_id == "kept"
