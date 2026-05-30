"""Pipecat pipeline service for voice agent call orchestration.

Assembles and runs the STT -> LLM -> TTS pipeline using the Exotel
transport created by the router. This module owns pipeline assembly,
execution, and observer-based metrics capture. It does not own route
bootstrap, session registry lifecycle, or persistence.

Service construction patterns verified against Pipecat 1.2.1:
- DeepgramSTTService: sample_rate= as direct constructor kwarg,
  STT options via settings=DeepgramSTTSettings(...)
- CerebrasLLMService: model/temp via settings=CerebrasLLMSettings(...)
- CartesiaTTSService: sample_rate= as direct constructor kwarg,
  voice/model via settings=CartesiaTTSSettings(voice=..., model=...)
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMMessagesAppendFrame
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.cartesia.tts import CartesiaTTSService, CartesiaTTSSettings
from pipecat.services.cerebras.llm import CerebrasLLMService, CerebrasLLMSettings
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.tts_service import TextAggregationMode
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport
from pipecat.turns.user_mute import (
    AlwaysUserMuteStrategy,
    MuteUntilFirstBotCompleteUserMuteStrategy,
)
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies

from app.config import Settings, V1_SAMPLE_RATE
from app.runtime import ConfigSnapshot, append_session_turn_metrics, update_session_first_latency
from app.utils.metrics_logger import build_turn_metrics


def _build_stt(settings: Settings) -> DeepgramFluxSTTService:
    """Construct the Deepgram Flux STT service.
    - flux-general-en: correct Flux model name for English.
    - should_interrupt=False: preserves non-interruptible dialog behavior.
    """

    return DeepgramFluxSTTService(
        api_key=settings.deepgram_api_key,
        sample_rate=V1_SAMPLE_RATE,
        should_interrupt=False,
        settings=DeepgramFluxSTTService.Settings(
            model="flux-general-en",
        ),
    )


def _build_llm(snapshot: ConfigSnapshot, settings: Settings) -> CerebrasLLMService:
    """Construct the Cerebras LLM service using typed Settings.

    Verified Pipecat 1.2.1 pattern:
    - model and temperature via settings=CerebrasLLMSettings(...)
    """

    return CerebrasLLMService(
        api_key=settings.cerebras_api_key,
        settings=CerebrasLLMSettings(
            model=snapshot["LLM_MODEL"],
            temperature=snapshot["LLM_TEMPERATURE"],
        ),
    )


def _build_tts(snapshot: ConfigSnapshot, settings: Settings) -> CartesiaTTSService:
    """Construct the Cartesia TTS service with SENTENCE aggregation.

    Verified Pipecat 1.2.1 pattern:
    - sample_rate passed as direct constructor kwarg
    - voice and model via settings=CartesiaTTSSettings(voice=..., model=...)
    - voice_id= and model= as direct constructor kwargs are deprecated in 1.2.1
    - text_aggregation_mode and max_buffer_delay_ms are direct constructor kwargs

    SENTENCE mode buffers LLM tokens until a sentence boundary, then sends
    the full sentence to Cartesia for premium prosody. max_buffer_delay_ms=0
    tells the Cartesia server to synthesize immediately (no server-side
    buffering window). This is the implicit default for SENTENCE mode but
    set explicitly per spec for clarity.
    """

    return CartesiaTTSService(
        api_key=settings.cartesia_api_key,
        sample_rate=V1_SAMPLE_RATE,
        cartesia_version="2026-03-01",
        text_aggregation_mode=TextAggregationMode.SENTENCE,
        max_buffer_delay_ms=0,
        settings=CartesiaTTSSettings(
            voice=snapshot["TTS_VOICE_ID"],
            model=snapshot["TTS_MODEL"],
        ),
    )


def _build_context(snapshot: ConfigSnapshot) -> LLMContext:
    """Build the initial LLM context with the system prompt."""

    return LLMContext(
        messages=[
            {"role": "system", "content": snapshot["SYSTEM_PROMPT"]},
        ],
    )


def _build_aggregator_pair(context: LLMContext) -> LLMContextAggregatorPair:
    user_params = LLMUserAggregatorParams(
        user_turn_strategies=ExternalUserTurnStrategies(),
        vad_analyzer=SileroVADAnalyzer(sample_rate=V1_SAMPLE_RATE),
        user_mute_strategies=[
            MuteUntilFirstBotCompleteUserMuteStrategy(),
            AlwaysUserMuteStrategy(),
        ],
    )
    return LLMContextAggregatorPair(
        context=context,
        user_params=user_params,
    )


class PipelineExecutionResult:
    """Serializable result returned after pipeline execution completes.

    The route uses this to understand pipeline outcome without
    receiving live Pipecat objects.
    """

    def __init__(self, *, completed: bool, error: str | None = None) -> None:
        self.completed = completed
        self.error = error


async def run_pipeline(
    *,
    transport: FastAPIWebsocketTransport,
    call_sid: str,
    stream_sid: str,
    snapshot: ConfigSnapshot,
    settings: Settings,
) -> PipelineExecutionResult:
    """Assemble and run the Pipecat pipeline for one call.

    This is the route-facing pipeline boundary. It receives the already-
    constructed transport and serializable call-scoped inputs, assembles
    the Pipecat graph, runs it to completion, and returns a serializable
    result.

    The caller (telephony router) is responsible for session creation,
    cleanup, and the later finalize_call_log() boundary.

    Disconnect handling: when the Exotel WebSocket client disconnects, the
    transport fires the on_client_disconnected event. The registered callback
    calls task.cancel(), which causes the runner to unwind gracefully and this
    coroutine to return. The router's finally block then handles session cleanup.

    Args:
        transport: The FastAPI WebSocket transport built by the router.
        call_sid: The Exotel call identifier.
        stream_sid: The Exotel stream identifier.
        snapshot: The immutable per-call configuration snapshot.
        settings: Application settings for provider credentials.

    Returns:
        A PipelineExecutionResult indicating whether the pipeline
        completed normally or encountered an error.
    """

    log = logger.bind(call_sid=call_sid)

    # --- Service construction (verified Pipecat 1.2.1 .Settings() pattern) ---
    stt = _build_stt(settings)
    llm = _build_llm(snapshot, settings)
    tts = _build_tts(snapshot, settings)

    context = _build_context(snapshot)
    aggregator_pair = _build_aggregator_pair(context)

    # --- Pipeline assembly ---
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregator_pair.user(),
            llm,
            tts,
            aggregator_pair.assistant(),
            transport.output(),
        ]
    )

    # --- Observer registration (on PipelineTask, not PipelineParams) ---
    latency_observer = UserBotLatencyObserver()

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            audio_in_sample_rate=V1_SAMPLE_RATE,
            audio_out_sample_rate=V1_SAMPLE_RATE,
        ),
        observers=[latency_observer],
    )

    # --- Phase 3: Observer event callbacks for metrics capture ---
    # Mutable container for pairing on_latency_measured with on_latency_breakdown.
    # Both events fire sequentially from _handle_bot_started_speaking() as
    # separate asyncio tasks. In CPython asyncio, tasks created in sequence
    # are drained in FIFO order, so the measured value is available when
    # the breakdown handler runs.
    _pending_latency: dict[str, float] = {}

    @latency_observer.event_handler("on_latency_measured")
    async def _on_latency_measured(observer: Any, latency_seconds: float) -> None:
        """Capture per-turn user-to-bot latency."""
        log.info("Turn latency measured", latency_secs=f"{latency_seconds:.3f}")
        _pending_latency["value"] = latency_seconds

    @latency_observer.event_handler("on_latency_breakdown")
    async def _on_latency_breakdown(observer: Any, breakdown: Any) -> None:
        """Normalize latency breakdown and append turn metrics to session."""
        latency_secs = _pending_latency.pop("value", None)
        if latency_secs is None:
            log.info("Skipping latency breakdown without matching user turn")
            return
        turn = build_turn_metrics(latency_secs=latency_secs, breakdown=breakdown)
        append_session_turn_metrics(call_sid, turn.model_dump())
        if turn.latency_summary is not None:
            log.info("Turn latency summary", summary=turn.latency_summary.model_dump())
        log.info(
            "Turn breakdown captured",
            events_count=len(turn.latency_events),
        )

    @latency_observer.event_handler("on_first_bot_speech_latency")
    async def _on_first_bot_speech(observer: Any, latency_seconds: float) -> None:
        """Capture time from client connection to first bot speech."""
        log.info("First bot speech latency", latency_secs=f"{latency_seconds:.3f}")
        update_session_first_latency(call_sid, latency_seconds)

    # --- Transport disconnect callback ---
    # Disconnect during active pipeline surfaces through this transport lifecycle
    # hook rather than propagating WebSocketDisconnect to the router. The callback
    # cancels the task so the runner unwinds gracefully.
    @transport.event_handler("on_client_disconnected")
    async def _on_client_disconnected(transport_ref: Any, client: Any) -> None:
        """Handle Exotel WebSocket disconnect through transport lifecycle hook."""
        log.info("Transport client disconnected, cancelling pipeline task")
        await task.cancel()

    # --- Initial greeting ---
    # queue_frame() uses asyncio.Queue internally (initialized in PipelineTask.__init__),
    # so frames queued here are safely buffered and delivered once runner.run() starts
    # draining the queue. This achieves bot-initiated opening without polling.
    greeting_frame = LLMMessagesAppendFrame(
        messages=[
            {"role": "user", "content": snapshot["INITIAL_GREETING_PROMPT"]},
        ],
        run_llm=True,
    )
    await task.queue_frame(greeting_frame)

    # --- Runner execution ---
    runner = PipelineRunner(handle_sigint=False)
    log.info("Pipeline runner starting", stream_sid=stream_sid)

    try:
        await runner.run(task)
        log.info("Pipeline runner completed normally")
        return PipelineExecutionResult(completed=True)
    except asyncio.CancelledError:
        log.info("Pipeline runner cancelled (user disconnected)")
        return PipelineExecutionResult(completed=True, error="User disconnected")
    except Exception as exc:
        log.error("Pipeline runner failed: {error}", error=str(exc))
        return PipelineExecutionResult(completed=False, error=str(exc))
