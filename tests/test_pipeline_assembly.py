import copy

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.cerebras.llm import CerebrasLLMService
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService

from app.config import get_settings
from app.models.runtime_config_models import STTCfg
from app.runtime import build_config_snapshot
from app.services.call_metrics import TurnMetricsObserver
from app.services.deferred_flux_stt import DeferredConnectDeepgramFluxSTTService
from app.services.language_prefix import LanguagePrefixProcessor
from app.services.pipecat_pipeline import (
    MemoryProcessor,
    SttLanguageHintProcessor,
    _build_llm,
    _build_stt,
    _build_user_turn_strategies,
    _llm_cfg,
    _stt_cfg,
    _stt_ready_state,
)


def test_snapshot_exposes_stt_and_llm_config():
    snap = build_config_snapshot(get_settings())
    assert _stt_cfg(snap)["model"] == "flux-general-multi"
    assert _stt_cfg(snap)["language_hints"] == ["en", "hi"]
    assert _llm_cfg(snap)["model"] == "gpt-oss-120b"


def test_build_stt_and_llm_construct():
    snap = build_config_snapshot(get_settings())
    settings = get_settings()
    assert isinstance(_build_stt(snap, settings), DeepgramFluxSTTService)
    assert isinstance(_build_llm(snap, settings), CerebrasLLMService)


def test_build_stt_defers_connect_by_default_and_respects_flag():
    settings = get_settings()
    snap = build_config_snapshot(settings)
    # Default flag (True) -> deferred-connect subclass.
    assert isinstance(_build_stt(snap, settings), DeferredConnectDeepgramFluxSTTService)

    # Flag False -> stock blocking service (still a DeepgramFluxSTTService).
    snap_off = copy.deepcopy(snap)
    snap_off["runtime_config"]["stt"]["defer_connect_on_start"] = False
    stt_off = _build_stt(snap_off, settings)
    assert isinstance(stt_off, DeepgramFluxSTTService)
    assert not isinstance(stt_off, DeferredConnectDeepgramFluxSTTService)


def test_pipeline_order_has_prefix_between_llm_and_tts():
    snap = build_config_snapshot(get_settings())
    transport = MagicMock()
    obs = TurnMetricsObserver(on_record=lambda r: None)
    from app.services.pipecat_pipeline import _build_pipeline_processors

    procs = _build_pipeline_processors(
        transport=transport,
        snapshot=snap,
        settings=get_settings(),
        call_sid="c1",
        stream_sid="s1",
        metrics_observer=obs,
    )
    # input, stt, stt_hint, memory, user(), llm, prefix, tts, output(), assistant()
    assert len(procs) == 10
    assert isinstance(procs[1], DeepgramFluxSTTService)        # stt
    assert isinstance(procs[2], SttLanguageHintProcessor)      # weak STT hint
    assert isinstance(procs[3], MemoryProcessor)               # memory (before user())
    assert isinstance(procs[5], CerebrasLLMService)            # llm
    assert isinstance(procs[6], LanguagePrefixProcessor)       # prefix BETWEEN llm and tts
    assert isinstance(procs[7], CartesiaTTSService)            # tts
    # user aggregator [4] distinct from assistant aggregator [9]; assistant is
    # AFTER transport.output() [8].
    assert procs[4] is not procs[9]
    assert procs[8] is transport.output.return_value


def test_turn_stop_timeout_default_is_500_and_range_enforced():
    # LAT-2: default preserves current behavior; out-of-range is rejected so the
    # /config save fails loudly rather than silently clamping.
    assert STTCfg().turn_stop_timeout_ms == 500
    with pytest.raises(ValidationError):
        STTCfg(turn_stop_timeout_ms=50)     # below minimum (100)
    with pytest.raises(ValidationError):
        STTCfg(turn_stop_timeout_ms=1500)   # above maximum (1000)


def test_turn_stop_strategy_uses_configured_timeout():
    # The configured ms wires straight into ExternalUserTurnStopStrategy(timeout=s).
    s200 = _build_user_turn_strategies(turn_stop_timeout_ms=200)
    assert s200.stop[0]._timeout == 0.2
    s500 = _build_user_turn_strategies(turn_stop_timeout_ms=500)
    assert s500.stop[0]._timeout == 0.5


def test_aggregator_pair_threads_turn_stop_timeout_from_config():
    from app.services.pipecat_pipeline import _build_pipeline_processors
    settings = get_settings()
    snap = copy.deepcopy(build_config_snapshot(settings))
    snap["runtime_config"]["stt"]["turn_stop_timeout_ms"] = 200
    transport = MagicMock()
    obs = TurnMetricsObserver(on_record=lambda r: None)
    procs = _build_pipeline_processors(
        transport=transport, snapshot=snap, settings=settings,
        call_sid="c1", stream_sid="s1", metrics_observer=obs,
    )
    user_agg = procs[4]
    assert user_agg._params.user_turn_strategies.stop[0]._timeout == 0.2


def test_build_llm_wires_request_timeout_seconds():
    # LLM-2: request_timeout_seconds must reach the Cerebras client (retry_timeout_secs),
    # otherwise the config field is a lie and the 5.0s base default silently applies.
    settings = get_settings()
    snap = copy.deepcopy(build_config_snapshot(settings))
    snap["runtime_config"]["llm"]["request_timeout_seconds"] = 7.5
    llm = _build_llm(snap, settings)
    assert llm._retry_timeout_secs == 7.5


def test_stt_ready_state_is_tri_state_not_a_latch():
    # OBS-1: readiness is per-turn from connected_at, not a one-way startup latch.
    class _Svc:
        pass

    svc = _Svc()
    svc.connected_at = 100.0
    assert _stt_ready_state(svc, 200.0) is True    # connected before this turn
    assert _stt_ready_state(svc, 50.0) is False     # turn happened before connect
    svc.connected_at = None
    assert _stt_ready_state(svc, 200.0) is None      # connect not observed -> unknown
    non_deferred = _Svc()                            # no connected_at attribute
    assert _stt_ready_state(non_deferred, 200.0) is True


def test_greeting_settle_delay_default_is_500():
    assert get_settings().greeting_settle_delay_ms == 500


def test_opening_greeting_fires_on_client_connected_autonomously():
    # The greeting must play on carrier-connect, exactly once, WITHOUT waiting for
    # the caller to speak. Speaking before connect lets the carrier drop the audio.
    import asyncio

    from loguru import logger
    from pipecat.frames.frames import TTSSpeakFrame

    from app.runtime import ACTIVE_SESSIONS
    from app.services.pipecat_pipeline import _schedule_opening_greeting

    call_sid = "c_greet"
    ACTIVE_SESSIONS[call_sid] = {
        "call_sid": call_sid, "stream_sid": "s", "metrics": {}, "transcript": [],
        "first_latency": None, "config_snapshot": {}, "memory": {}, "language_state": {},
    }

    class _FakeTransport:
        def __init__(self):
            self.handlers = {}

        def event_handler(self, name):
            def deco(fn):
                self.handlers[name] = fn
                return fn
            return deco

    class _FakeTask:
        def __init__(self):
            self.frames = []

        async def queue_frame(self, frame):
            self.frames.append(frame)

    transport = _FakeTransport()
    task = _FakeTask()
    try:
        async def run():
            state = _schedule_opening_greeting(
                transport=transport, task=task, log=logger,
                call_sid=call_sid, greeting_text="Hi there!", settle_ms=0,
            )
            assert "on_client_connected" in transport.handlers
            # No caller speech — connecting alone triggers the greeting.
            await transport.handlers["on_client_connected"](transport, object())
            await state["task"]
            # A re-fired connect must NOT speak the greeting twice.
            await transport.handlers["on_client_connected"](transport, object())
            return state

        asyncio.run(run())
        assert len(task.frames) == 1
        assert isinstance(task.frames[0], TTSSpeakFrame)
        assert task.frames[0].text == "Hi there!"
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_user_muted_until_first_bot_complete_during_greeting():
    # Greeting-phase safety (makes deferred STT safe): the caller is muted until
    # the opening greeting completes, so early audio can't create a turn.
    from pipecat.turns.user_mute import (
        AlwaysUserMuteStrategy,
        MuteUntilFirstBotCompleteUserMuteStrategy,
    )
    from app.services.pipecat_pipeline import _user_mute_strategies

    types = {type(s) for s in _user_mute_strategies()}
    assert MuteUntilFirstBotCompleteUserMuteStrategy in types
    assert AlwaysUserMuteStrategy in types
