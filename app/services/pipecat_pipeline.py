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
import time
from typing import Any, Literal

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    TTSSpeakFrame,
    TranscriptionFrame,
)
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.cartesia.tts import CartesiaTTSService, CartesiaTTSSettings
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.tts_service import TextAggregationMode
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport
from pipecat.turns.user_mute import (
    AlwaysUserMuteStrategy,
    MuteUntilFirstBotCompleteUserMuteStrategy,
)
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies

from app.config import Settings, V1_SAMPLE_RATE
from app.models.config_models import FallbackPolicy, FlowDefinition
from app.models.conversation_models import (
    ConversationState,
    TurnCollectionResult,
    TurnEvent,
    TurnInput,
)
from app.runtime import (
    ConfigSnapshot,
    append_session_transcript_event,
    append_session_turn_metrics,
    build_config_snapshot,
    update_session_metric,
    update_session_conversation_state,
    update_session_first_latency,
)
from app.services.conversation_runtime import ConversationRuntime
from app.services.turn_collector import TurnCollector, utc_now
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
            eot_threshold=0.65,
            eot_timeout_ms=1000,
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
    """Build the context object required by Pipecat aggregators."""

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


def _normalize_transcript_text(text: str) -> str:
    """Collapse transcript whitespace into a clean single string."""

    return " ".join(text.split())


RUNTIME_ERROR_FALLBACK_TEXT = (
    "Sorry, something went wrong on my side. We can stop here for now."
)


TranscriptPhase = Literal["greeting", "readiness", "task", "closing", "unknown"]


def _transcript_phase(phase: str) -> TranscriptPhase:
    """Map Phase 3 runtime phases to the persisted legacy transcript phases."""

    if phase == "greeting":
        return "greeting"
    if phase == "readiness":
        return "readiness"
    if phase in {"collecting", "answering_question", "task"}:
        return "task"
    if phase in {"closing", "ended"}:
        return "closing"
    return "unknown"


def _fallback_runtime_snapshot(snapshot: ConfigSnapshot, settings: Settings) -> ConfigSnapshot:
    if snapshot.get("flow_definition"):
        return snapshot
    return build_config_snapshot(settings)


def _build_conversation_runtime(
    *,
    call_sid: str,
    stream_sid: str | None,
    snapshot: ConfigSnapshot,
    settings: Settings,
    opening_mode: Literal["outbound", "default"],
) -> ConversationRuntime:
    """Build one ConversationRuntime from the finalized call config snapshot."""

    runtime_snapshot = _fallback_runtime_snapshot(snapshot, settings)
    flow_definition = FlowDefinition.model_validate(runtime_snapshot["flow_definition"])
    fallback_policy = FallbackPolicy.model_validate(runtime_snapshot.get("fallback_policy") or {})
    state = ConversationState(
        call_sid=call_sid,
        stream_sid=stream_sid,
        phase="greeting" if opening_mode == "outbound" else "readiness",
    )
    return ConversationRuntime(
        state=state,
        flow_definition=flow_definition,
        fallback_policy=fallback_policy,
    )


class UserConversationProcessor(FrameProcessor):
    """Route committed user turns through the Phase 3 conversation runtime."""

    def __init__(
        self,
        *,
        call_sid: str,
        stream_sid: str | None = None,
        conversation_runtime: ConversationRuntime,
        greeting_audio_pending: bool = False,
        turn_collector: TurnCollector | None = None,
    ) -> None:
        super().__init__()
        self._call_sid = call_sid
        self._stream_sid = stream_sid
        self._conversation_runtime = conversation_runtime
        self._greeting_audio_pending = greeting_audio_pending
        self._closing_done = False
        self._turn_collector = turn_collector or TurnCollector(
            call_sid=call_sid,
            stream_sid=stream_sid,
        )
        self._turn_processing_lock = asyncio.Lock()
        self.last_turn_collection_result: TurnCollectionResult | None = None
        self._sync_conversation_snapshot()

    async def process_frame(
        self,
        frame: Any,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
    ) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStoppedSpeakingFrame) and self._greeting_audio_pending:
            self._greeting_audio_pending = False
            self._conversation_runtime.set_phase("readiness")
            self._sync_conversation_snapshot()

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TranscriptionFrame) and frame.finalized:
            result = self._observe_turn_event(
                TurnEvent(
                    event_type="final_transcript",
                    text=frame.text,
                    timestamp=utc_now(),
                    metadata={
                        "pipecat_frame": "TranscriptionFrame",
                        "result": getattr(frame, "result", None) or {},
                    },
                )
            )
            self._log_turn_collection_result(result)
            if self._greeting_audio_pending:
                return

        await self.push_frame(frame, direction)

    async def handle_end_of_turn(self, transcript: str | None) -> TurnCollectionResult:
        """Handle Deepgram Flux EndOfTurn through the same collector bridge."""

        result = self._observe_turn_event(
            TurnEvent(
                event_type="end_of_turn",
                text=transcript,
                timestamp=utc_now(),
                metadata={"event_handler": "on_end_of_turn"},
            )
        )
        self._log_turn_collection_result(result)
        if result.turn is not None:
            await self._handle_committed_turn(result.turn)
        return result

    async def handle_reference_turn_event(
        self,
        *,
        event_type: Literal["update", "eager_end_of_turn", "turn_resumed"],
        transcript: str | None = None,
    ) -> TurnCollectionResult:
        """Record non-committing Flux turn events for debug/reference only."""

        result = self._observe_turn_event(
            TurnEvent(
                event_type=event_type,
                text=transcript,
                timestamp=utc_now(),
                metadata={"event_handler": f"on_{event_type}"},
            )
        )
        self._log_turn_collection_result(result)
        return result

    def _observe_turn_event(self, event: TurnEvent) -> TurnCollectionResult:
        result = self._turn_collector.observe(
            event,
            phase=self._current_phase(),
            greeting_pending=self._greeting_audio_pending,
        )
        self.last_turn_collection_result = result
        return result

    async def _handle_committed_turn(self, turn: TurnInput) -> None:
        if self._turn_processing_lock.locked():
            self._suppress_committed_turn(turn=turn, ignored_reason="turn_processing")
            return

        async with self._turn_processing_lock:
            if self._closing_done:
                self._suppress_committed_turn(turn=turn, ignored_reason="already_closing")
                return

            pre_turn_phase = self._conversation_runtime.snapshot_state().phase
            try:
                result = self._conversation_runtime.handle_turn(turn)
            except Exception as exc:
                await self._handle_runtime_exception(
                    turn=turn,
                    pre_turn_phase=pre_turn_phase,
                    exc=exc,
                )
                return

            self._append_transcript_event(
                role="user",
                phase=pre_turn_phase,
                text=result.user_transcript_text or turn.text,
            )
            self._append_transcript_event(
                role="assistant",
                phase=result.state.phase,
                text=result.assistant_transcript_text or result.assistant_text,
            )
            self._sync_conversation_snapshot(result.state)
            await self.push_frame(
                TTSSpeakFrame(result.assistant_text, append_to_context=True),
                FrameDirection.DOWNSTREAM,
            )
            if result.should_end_call:
                self._closing_done = True

    def _current_phase(self) -> str:
        if self._greeting_audio_pending:
            return "greeting"
        return self._conversation_runtime.snapshot_state().phase

    def _sync_conversation_snapshot(self, state: ConversationState | None = None) -> None:
        snapshot = state or self._conversation_runtime.snapshot_state()
        update_session_conversation_state(
            self._call_sid,
            snapshot.model_dump(mode="json"),
        )

    def _append_transcript_event(
        self,
        *,
        role: Literal["assistant", "user"],
        phase: str,
        text: str,
    ) -> None:
        append_session_transcript_event(
            self._call_sid,
            role=role,
            phase=_transcript_phase(phase),
            text=_normalize_transcript_text(text),
        )

    def _suppress_committed_turn(self, *, turn: TurnInput, ignored_reason: str) -> None:
        self.last_turn_collection_result = TurnCollectionResult(
            ignored_reason=ignored_reason,
            commit_source=turn.source,
            commit_timestamp=turn.committed_at,
            provider_turn_index=turn.provider_metadata.get("provider_turn_index"),
        )
        logger.info(
            "Committed turn suppressed",
            call_sid=self._call_sid,
            turn_index=turn.turn_index,
            ignored_reason=ignored_reason,
        )

    async def _handle_runtime_exception(
        self,
        *,
        turn: TurnInput,
        pre_turn_phase: str,
        exc: Exception,
    ) -> None:
        logger.exception(
            "Conversation runtime failed; using safe fallback",
            call_sid=self._call_sid,
            turn_index=turn.turn_index,
            error=str(exc),
        )
        self._append_transcript_event(
            role="user",
            phase=pre_turn_phase,
            text=turn.text,
        )
        try:
            self._conversation_runtime.set_phase("closing")
            self._sync_conversation_snapshot()
        except Exception:
            logger.exception(
                "Failed to update runtime closing state after runtime error",
                call_sid=self._call_sid,
                turn_index=turn.turn_index,
            )
        self._append_transcript_event(
            role="assistant",
            phase="closing",
            text=RUNTIME_ERROR_FALLBACK_TEXT,
        )
        await self.push_frame(
            TTSSpeakFrame(RUNTIME_ERROR_FALLBACK_TEXT, append_to_context=True),
            FrameDirection.DOWNSTREAM,
        )
        self._closing_done = True

    def _log_turn_collection_result(self, result: TurnCollectionResult) -> None:
        if result.ignored_reason is None and result.turn is None:
            return
        logger.info(
            "Turn collection result",
            call_sid=self._call_sid,
            ignored_reason=result.ignored_reason,
            commit_source=result.commit_source,
            provider_turn_index=result.provider_turn_index,
            duplicate_suppressed=result.duplicate_suppressed,
            greeting_pending_suppressed=result.greeting_pending_suppressed,
        )


def _record_greeting(
    *,
    call_sid: str,
    greeting_text: str,
) -> None:
    append_session_transcript_event(
        call_sid,
        role="assistant",
        phase="greeting",
        text=greeting_text,
    )


def _build_pipeline_processors(
    *,
    transport: FastAPIWebsocketTransport,
    snapshot: ConfigSnapshot,
    settings: Settings,
    call_sid: str,
    stream_sid: str | None = None,
    opening_mode: Literal["outbound", "default"] = "default",
    conversation_runtime: ConversationRuntime | None = None,
) -> list[Any]:
    """Construct the full pipeline processor list for one call."""

    stt = _build_stt(settings)
    tts = _build_tts(snapshot, settings)
    context = _build_context(snapshot)
    aggregator_pair = _build_aggregator_pair(context)
    conversation_runtime = conversation_runtime or _build_conversation_runtime(
        call_sid=call_sid,
        stream_sid=stream_sid,
        snapshot=snapshot,
        settings=settings,
        opening_mode=opening_mode,
    )
    conversation_processor = UserConversationProcessor(
        call_sid=call_sid,
        stream_sid=stream_sid,
        conversation_runtime=conversation_runtime,
        greeting_audio_pending=opening_mode == "outbound",
    )

    if hasattr(stt, "event_handler"):
        @stt.event_handler("on_end_of_turn")
        async def _on_end_of_turn(service: Any, transcript: str) -> None:
            await conversation_processor.handle_end_of_turn(transcript)

        @stt.event_handler("on_eager_end_of_turn")
        async def _on_eager_end_of_turn(service: Any, transcript: str) -> None:
            await conversation_processor.handle_reference_turn_event(
                event_type="eager_end_of_turn",
                transcript=transcript,
            )

        @stt.event_handler("on_turn_resumed")
        async def _on_turn_resumed(service: Any) -> None:
            await conversation_processor.handle_reference_turn_event(event_type="turn_resumed")

        @stt.event_handler("on_update")
        async def _on_update(service: Any, transcript: str) -> None:
            await conversation_processor.handle_reference_turn_event(
                event_type="update",
                transcript=transcript,
            )

    return [
        transport.input(),
        stt,
        conversation_processor,
        aggregator_pair.user(),
        tts,
        aggregator_pair.assistant(),
        transport.output(),
    ]


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
    prepared_greeting_text: str | None = None,
    opening_mode: Literal["outbound", "default"] = "default",
    greeting_start_delay_ms: int = 0,
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

    # --- Pipeline assembly ---
    pipeline = Pipeline(
        _build_pipeline_processors(
            transport=transport,
            snapshot=snapshot,
            settings=settings,
            call_sid=call_sid,
            stream_sid=stream_sid,
            opening_mode=opening_mode,
        )
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
        update_session_first_latency(call_sid, latency_seconds, observed_at=time.time())

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
    if greeting_start_delay_ms > 0:
        await asyncio.sleep(greeting_start_delay_ms / 1000)

    if opening_mode == "outbound":
        cleaned_greeting = _normalize_transcript_text(
            prepared_greeting_text or snapshot["INITIAL_GREETING_FALLBACK_TEXT"]
        )
        _record_greeting(
            call_sid=call_sid,
            greeting_text=cleaned_greeting,
        )
        update_session_metric(call_sid, "first_tts_frame_queued_at", time.time())
        await task.queue_frame(TTSSpeakFrame(cleaned_greeting, append_to_context=True))
    else:
        cleaned_greeting = _normalize_transcript_text(snapshot["INITIAL_GREETING_FALLBACK_TEXT"])
        _record_greeting(
            call_sid=call_sid,
            greeting_text=cleaned_greeting,
        )
        update_session_metric(call_sid, "first_tts_frame_queued_at", time.time())
        await task.queue_frame(TTSSpeakFrame(cleaned_greeting, append_to_context=True))

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
