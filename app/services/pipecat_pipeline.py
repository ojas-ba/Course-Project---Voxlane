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
from app.models.conversation_models import TurnCollectionResult, TurnEvent, TurnInput
from app.runtime import (
    ConfigSnapshot,
    append_session_transcript_event,
    append_session_turn_metrics,
    update_session_first_latency,
)
from app.services.conversation_controller import (
    ConversationRuntimeState,
    apply_turn_decision,
    decide_next_turn,
)
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


class UserConversationProcessor(FrameProcessor):
    """Route committed user turns through the temporary legacy controller bridge."""

    def __init__(
        self,
        *,
        call_sid: str,
        stream_sid: str | None = None,
        state: ConversationRuntimeState,
        snapshot: ConfigSnapshot,
        settings: Settings,
        opening_mode: Literal["outbound", "default"] = "default",
        turn_collector: TurnCollector | None = None,
    ) -> None:
        super().__init__()
        self._call_sid = call_sid
        self._stream_sid = stream_sid
        self._state = state
        self._snapshot = snapshot
        self._settings = settings
        self._opening_mode = opening_mode
        self._turn_collector = turn_collector or TurnCollector(
            call_sid=call_sid,
            stream_sid=stream_sid,
        )
        self._turn_processing_lock = asyncio.Lock()
        self.last_turn_collection_result: TurnCollectionResult | None = None

    async def process_frame(
        self,
        frame: Any,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
    ) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStoppedSpeakingFrame) and self._state.greeting_audio_pending:
            self._state.greeting_audio_pending = False
            self._state.phase = "awaiting_readiness" if self._opening_mode == "outbound" else "task"

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
            if self._state.greeting_audio_pending:
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
        phase = "readiness" if self._state.phase == "awaiting_readiness" else self._state.phase
        result = self._turn_collector.observe(
            event,
            phase=phase,
            greeting_pending=self._state.greeting_audio_pending,
        )
        self.last_turn_collection_result = result
        return result

    async def _handle_committed_turn(self, turn: TurnInput) -> None:
        if self._turn_processing_lock.locked():
            self.last_turn_collection_result = TurnCollectionResult(
                ignored_reason="turn_processing",
                commit_source=turn.source,
                commit_timestamp=turn.committed_at,
                provider_turn_index=turn.provider_metadata.get("provider_turn_index"),
            )
            logger.info(
                "Committed turn suppressed while previous turn is processing",
                call_sid=self._call_sid,
                turn_index=turn.turn_index,
                ignored_reason="turn_processing",
            )
            return

        async with self._turn_processing_lock:
            self._state.last_user_text = turn.normalized_text
            user_phase = "readiness" if self._state.phase == "awaiting_readiness" else self._state.phase
            append_session_transcript_event(
                self._call_sid,
                role="user",
                phase=user_phase if user_phase in {"readiness", "task", "closing"} else "task",
                text=turn.normalized_text,
            )
            self._state.recent_events.append(
                {"role": "user", "phase": user_phase, "text": turn.normalized_text}
            )
            decision = await decide_next_turn(
                user_text=turn.normalized_text,
                state=self._state,
                snapshot=self._snapshot,
                settings=self._settings,
            )
            apply_turn_decision(self._state, decision)
            assistant_phase = "closing" if self._state.phase == "closing" else decision.phase
            self._state.recent_events.append(
                {
                    "role": "assistant",
                    "phase": assistant_phase,
                    "text": decision.assistant_text,
                }
            )
            append_session_transcript_event(
                self._call_sid,
                role="assistant",
                phase=assistant_phase,
                text=decision.assistant_text,
            )
            await self.push_frame(
                TTSSpeakFrame(decision.assistant_text, append_to_context=True),
                FrameDirection.DOWNSTREAM,
            )

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
    state: ConversationRuntimeState,
    greeting_text: str,
) -> None:
    state.recent_events.append(
        {"role": "assistant", "phase": "greeting", "text": greeting_text}
    )
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
    conversation_state: ConversationRuntimeState | None = None,
) -> list[Any]:
    """Construct the full pipeline processor list for one call."""

    stt = _build_stt(settings)
    tts = _build_tts(snapshot, settings)
    context = _build_context(snapshot)
    aggregator_pair = _build_aggregator_pair(context)
    conversation_state = conversation_state or ConversationRuntimeState(
        phase="greeting" if opening_mode == "outbound" else "task"
    )
    conversation_processor = UserConversationProcessor(
        call_sid=call_sid,
        stream_sid=stream_sid,
        state=conversation_state,
        snapshot=snapshot,
        settings=settings,
        opening_mode=opening_mode,
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
    greeting_start_delay_ms: int = 100,
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
    conversation_state = ConversationRuntimeState(
        phase="greeting" if opening_mode == "outbound" else "task"
    )

    pipeline = Pipeline(
        _build_pipeline_processors(
            transport=transport,
            snapshot=snapshot,
            settings=settings,
            call_sid=call_sid,
            stream_sid=stream_sid,
            opening_mode=opening_mode,
            conversation_state=conversation_state,
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
    if greeting_start_delay_ms > 0:
        await asyncio.sleep(greeting_start_delay_ms / 1000)

    if opening_mode == "outbound":
        cleaned_greeting = _normalize_transcript_text(
            prepared_greeting_text or snapshot["INITIAL_GREETING_FALLBACK_TEXT"]
        )
        _record_greeting(
            call_sid=call_sid,
            state=conversation_state,
            greeting_text=cleaned_greeting,
        )
        conversation_state.phase = "greeting"
        conversation_state.greeting_audio_pending = True
        await task.queue_frame(TTSSpeakFrame(cleaned_greeting, append_to_context=True))
    else:
        cleaned_greeting = _normalize_transcript_text(snapshot["INITIAL_GREETING_FALLBACK_TEXT"])
        _record_greeting(
            call_sid=call_sid,
            state=conversation_state,
            greeting_text=cleaned_greeting,
        )
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
