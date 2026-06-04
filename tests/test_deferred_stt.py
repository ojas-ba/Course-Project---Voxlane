"""DeferredConnectDeepgramFluxSTTService: connect in background, don't block start."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from pipecat.services.deepgram.flux.base import DeepgramFluxSTTBase

from app.config import get_settings
from app.services.deferred_flux_stt import DeferredConnectDeepgramFluxSTTService


def _make_service():
    settings = get_settings()
    return DeferredConnectDeepgramFluxSTTService(
        api_key=settings.deepgram_api_key, sample_rate=16000
    )


def _grandparent(svc):
    mro = type(svc).__mro__
    return mro[mro.index(DeepgramFluxSTTBase) + 1]


def test_start_runs_base_setup_and_defers_connect_to_background():
    svc = _make_service()
    grandparent = _grandparent(svc)
    scheduled: list = []

    def fake_create_task(coro, *a, **k):
        fut = asyncio.ensure_future(coro)
        scheduled.append(fut)
        return fut

    async def noop_bg():
        return None

    async def run():
        with patch.object(grandparent, "start", new_callable=AsyncMock) as gp_start, \
             patch.object(svc, "_background_connect", noop_bg), \
             patch.object(svc, "create_task", side_effect=fake_create_task):
            # start() must return promptly (StartFrame is free to propagate).
            await asyncio.wait_for(svc.start(MagicMock()), timeout=1.0)
            gp_start.assert_awaited_once()        # base setup ran
            assert len(scheduled) == 1            # connect scheduled, not awaited inline
            assert svc._deferred_connect_task is not None
        for f in scheduled:
            await f

    asyncio.run(run())


def test_start_does_not_block_even_if_connect_is_slow():
    svc = _make_service()
    grandparent = _grandparent(svc)
    connect_started = asyncio.Event()
    release = asyncio.Event()

    async def slow_connect():
        connect_started.set()
        await release.wait()

    def fake_create_task(coro, *a, **k):
        return asyncio.ensure_future(coro)

    async def run():
        with patch.object(grandparent, "start", new_callable=AsyncMock), \
             patch.object(svc, "_connect", slow_connect), \
             patch.object(svc, "create_task", side_effect=fake_create_task):
            # Even though _connect blocks forever, start() returns.
            await asyncio.wait_for(svc.start(MagicMock()), timeout=1.0)
            await asyncio.wait_for(connect_started.wait(), timeout=1.0)
            release.set()
            try:
                await asyncio.wait_for(svc._deferred_connect_task, timeout=1.0)
            except Exception:
                pass

    asyncio.run(run())


def test_background_connect_failure_is_surfaced_as_error_frame():
    svc = _make_service()
    errs: list = []

    async def boom():
        raise RuntimeError("flux connect failed")

    async def run():
        with patch.object(svc, "_connect", boom), \
             patch.object(svc, "push_error_frame", new=AsyncMock(side_effect=lambda f: errs.append(f))):
            await svc._background_connect()

    asyncio.run(run())
    assert errs, "a connect failure should push an ErrorFrame"
