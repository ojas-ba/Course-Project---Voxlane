from unittest.mock import MagicMock

from pipecat.services.cerebras.llm import CerebrasLLMService
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService

from app.config import get_settings
from app.runtime import build_config_snapshot
from app.services.call_metrics import TurnMetricsObserver
from app.services.pipecat_pipeline import (
    LanguageProcessor,
    _build_llm,
    _build_pipeline_processors,
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


def test_pipeline_order_has_llm_between_user_and_tts():
    snap = build_config_snapshot(get_settings())
    transport = MagicMock()
    obs = TurnMetricsObserver(on_record=lambda r: None)
    procs = _build_pipeline_processors(
        transport=transport,
        snapshot=snap,
        settings=get_settings(),
        call_sid="c1",
        stream_sid="s1",
        metrics_observer=obs,
    )
    assert len(procs) == 8
    assert isinstance(procs[1], DeepgramFluxSTTService)   # stt
    assert isinstance(procs[2], LanguageProcessor)        # language
    assert isinstance(procs[4], CerebrasLLMService)       # llm (after user aggregator at [3])
    # user aggregator [3] and assistant aggregator [7] are distinct instances,
    # with the LLM and TTS between them and transport.output() at [6].
    assert procs[3] is not procs[7]
    assert procs[6] is transport.output.return_value
