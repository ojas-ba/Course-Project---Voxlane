import copy

from unittest.mock import MagicMock

from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.cerebras.llm import CerebrasLLMService
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService

from app.config import get_settings
from app.runtime import build_config_snapshot
from app.services.call_metrics import TurnMetricsObserver
from app.services.deferred_flux_stt import DeferredConnectDeepgramFluxSTTService
from app.services.language_prefix import LanguagePrefixProcessor
from app.services.pipecat_pipeline import (
    MemoryProcessor,
    SttLanguageHintProcessor,
    _build_llm,
    _build_stt,
    _llm_cfg,
    _stt_cfg,
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
