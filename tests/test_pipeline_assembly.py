import copy

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.cerebras.llm import CerebrasLLMService
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService

from app.config import get_settings
from app.models.runtime_config_models import LLMCfg, RuntimeConfig, STTCfg
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
    assert _llm_cfg(snap)["model"] == "qwen-3.8-27b"


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
    # input, input_gate, stt, stt_hint, memory, user(), llm, prefix, tts, output(), assistant()
    assert len(procs) == 11
    assert isinstance(procs[2], DeepgramFluxSTTService)
    assert isinstance(procs[3], SttLanguageHintProcessor)
    assert isinstance(procs[4], MemoryProcessor)
    assert isinstance(procs[6], CerebrasLLMService)
    assert isinstance(procs[7], LanguagePrefixProcessor)
    assert isinstance(procs[8], CartesiaTTSService)
    # user aggregator [4] distinct from assistant aggregator [9]; assistant is
    # AFTER transport.output() [8].
    assert procs[5] is not procs[10]
    assert procs[9] is transport.output.return_value


def test_turn_stop_timeout_default_is_500_and_range_enforced():
    # LAT-2: default preserves current behavior; out-of-range is rejected so the
    # /config save fails loudly rather than silently clamping.
    assert STTCfg().turn_stop_timeout_ms == 500
    with pytest.raises(ValidationError):
        STTCfg(turn_stop_timeout_ms=50)     # below minimum (100)
    with pytest.raises(ValidationError):
        STTCfg(turn_stop_timeout_ms=1500)   # above maximum (1000)


def test_eager_eot_disabled_by_default_and_threshold_validated():
    # Stage-1: off by default; the repo's first cross-field validator enforces
    # eager_eot_threshold <= eot_threshold ONLY when eager is enabled.
    assert STTCfg().enable_eager_eot is False
    assert STTCfg().eager_eot_threshold is None
    with pytest.raises(ValidationError):
        STTCfg(enable_eager_eot=True, eager_eot_threshold=0.9, eot_threshold=0.7)  # eager > eot
    with pytest.raises(ValidationError):
        STTCfg(enable_eager_eot=True)  # enabled but no threshold -> required (would be inert)
    STTCfg(enable_eager_eot=True, eager_eot_threshold=0.6, eot_threshold=0.7)   # eager <= eot ok
    # Disabled -> validator is a no-op even with an out-of-order threshold.
    STTCfg(enable_eager_eot=False, eager_eot_threshold=0.9, eot_threshold=0.7)


def test_build_stt_passes_eager_threshold_only_when_enabled():
    settings = get_settings()
    base = copy.deepcopy(build_config_snapshot(settings))
    # Off by default -> Flux Settings.eager_eot_threshold left unset (NOT_GIVEN sentinel).
    assert not isinstance(_build_stt(base, settings)._settings.eager_eot_threshold, float)
    # Enabled with a threshold -> passed through.
    snap_on = copy.deepcopy(base)
    snap_on["runtime_config"]["stt"]["enable_eager_eot"] = True
    snap_on["runtime_config"]["stt"]["eager_eot_threshold"] = 0.6
    assert _build_stt(snap_on, settings)._settings.eager_eot_threshold == 0.6
    # Enabled but no threshold value -> still NOT passed (requires a value).
    snap_missing = copy.deepcopy(base)
    snap_missing["runtime_config"]["stt"]["enable_eager_eot"] = True
    assert not isinstance(_build_stt(snap_missing, settings)._settings.eager_eot_threshold, float)


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
    user_agg = procs[5]
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


# --- Stage-2 speculative LLM: config surface + disabled-path proof (Slice 1) ---


def test_enable_speculative_llm_defaults_false():
    # Stage-2 OFF by default: the flag lives in LLMCfg and defaults False so the
    # normal path is unchanged until explicitly enabled.
    assert LLMCfg().enable_speculative_llm is False


def test_browser_allows_speculation_with_eager():
    assert RuntimeConfig(stt={"enable_eager_eot": True, "eager_eot_threshold": 0.6},
                         llm={"enable_speculative_llm": True})
    with pytest.raises(ValidationError, match="requires enable_eager_eot"):
        RuntimeConfig(llm={"enable_speculative_llm": True})


def test_pipeline_byte_identical_when_speculation_disabled():
    # With the flag off, NO speculative processor is inserted: the processor list
    # is the exact 10-element baseline with the LLM at index 5 and prefix at 6.
    settings = get_settings()
    snap = copy.deepcopy(build_config_snapshot(settings))
    snap["runtime_config"]["llm"]["enable_speculative_llm"] = False
    transport = MagicMock()
    obs = TurnMetricsObserver(on_record=lambda r: None)
    from app.services.pipecat_pipeline import _build_pipeline_processors

    procs = _build_pipeline_processors(
        transport=transport,
        snapshot=snap,
        settings=settings,
        call_sid="c1",
        stream_sid="s1",
        metrics_observer=obs,
    )
    assert len(procs) == 11
    assert isinstance(procs[6], CerebrasLLMService)
    assert isinstance(procs[7], LanguagePrefixProcessor)


def test_pipeline_inserts_gate_between_user_agg_and_llm_when_enabled():
    # With speculation enabled (and eager on, so the config is internally valid), the
    # SpecGateProcessor is inserted at index 5, shifting llm->6, prefix->7, tts->8.
    from app.services.speculation_controller import SpecGateProcessor

    settings = get_settings()
    snap = copy.deepcopy(build_config_snapshot(settings))
    snap["runtime_config"]["llm"]["enable_speculative_llm"] = True
    snap["runtime_config"]["stt"]["enable_eager_eot"] = True
    snap["runtime_config"]["stt"]["eager_eot_threshold"] = 0.6
    transport = MagicMock()
    obs = TurnMetricsObserver(on_record=lambda r: None)
    from app.services.pipecat_pipeline import _build_pipeline_processors

    procs = _build_pipeline_processors(
        transport=transport,
        snapshot=snap,
        settings=settings,
        call_sid="c1",
        stream_sid="s1",
        metrics_observer=obs,
    )
    assert len(procs) == 12
    assert isinstance(procs[6], SpecGateProcessor)
    assert isinstance(procs[7], CerebrasLLMService)
    assert isinstance(procs[8], LanguagePrefixProcessor)
    assert isinstance(procs[9], CartesiaTTSService)
    assert procs[10] is transport.output.return_value
    assert procs[5] is not procs[11]
    # the gate is wired to a real controller
    from app.services.speculation_controller import SpeculationController
    assert isinstance(procs[6]._controller, SpeculationController)


def test_speculation_not_enabled_without_eager_even_if_llm_flag_set():
    # Defensive belt-and-suspenders: enable_speculative_llm alone (eager off) must NOT
    # insert the gate (the cross-field validator would normally reject this combo, but a
    # raw snapshot can carry it; the pipeline guard makes speculation truly inert).
    settings = get_settings()
    snap = copy.deepcopy(build_config_snapshot(settings))
    snap["runtime_config"]["llm"]["enable_speculative_llm"] = True
    snap["runtime_config"]["stt"]["enable_eager_eot"] = False
    transport = MagicMock()
    obs = TurnMetricsObserver(on_record=lambda r: None)
    from app.services.pipecat_pipeline import _build_pipeline_processors

    procs = _build_pipeline_processors(
        transport=transport, snapshot=snap, settings=settings,
        call_sid="c1", stream_sid="s1", metrics_observer=obs,
    )
    assert len(procs) == 11
    assert isinstance(procs[6], CerebrasLLMService)
