"""Pipecat pipeline service for the V3 open-ended voice agent.

Assembles and runs the multilingual STT -> LLM -> TTS pipeline over the Exotel
transport. Processor order:

    transport.input()
      -> Deepgram Flux STT (flux-general-multi, language hints from config)
      -> LanguageProcessor (detect language, switch Cartesia language per turn)
      -> context_aggregator.user()
      -> Cerebras GPT-OSS LLM
      -> Cartesia TTS
      -> transport.output()
      -> context_aggregator.assistant()

The committed user turn is driven natively by Flux end-of-turn (finalized
TranscriptionFrame + UserStoppedSpeakingFrame), which flushes the user
aggregator into the LLM. TurnCollector + TurnMetricsObserver ride the
``on_end_of_turn`` side channel for commit/transcript/metrics; they do NOT
generate replies. The pre-dial greeting is the only manually queued speech.

Bounded in-call memory (recent window + pinned facts + running summary) is
added in a later batch via a MemoryProcessor between ``user()`` and the LLM.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSUpdateSettingsFrame,
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
from app.models.conversation_models import TurnEvent
from app.runtime import (
    ConfigSnapshot,
    append_session_transcript_event,
    append_turn_record,
    get_session_language_state,
    set_session_language_state,
    update_session_metric,
    update_session_first_latency,
)
from app.services import language as lang
from app.services.call_metrics import TurnMetricsObserver
from app.services.turn_collector import TurnCollector, utc_now

MULTILINGUAL_FLUX_MODEL = "flux-general-multi"


def _stt_cfg(snapshot: ConfigSnapshot) -> dict[str, Any]:
    return (snapshot.get("runtime_config") or {}).get("stt") or {}


def _llm_cfg(snapshot: ConfigSnapshot) -> dict[str, Any]:
    return (snapshot.get("runtime_config") or {}).get("llm") or {}


def _build_stt(snapshot: ConfigSnapshot, settings: Settings) -> DeepgramFluxSTTService:
    """Construct the Deepgram Flux STT service from the runtime config.

    Uses ``flux-general-multi`` with language hints for Hindi/English. Hints are
    only honored on the multilingual model, so they are only passed there.
    """

    cfg = _stt_cfg(snapshot)
    model = cfg.get("model", MULTILINGUAL_FLUX_MODEL)
    hints = lang.language_hints_to_enums(cfg.get("language_hints", ["en", "hi"]))
    stt_settings = DeepgramFluxSTTService.Settings(
        model=model,
        eot_threshold=cfg.get("eot_threshold", 0.65),
        eot_timeout_ms=cfg.get("eot_timeout_ms", 700),
    )
    if hints and model == MULTILINGUAL_FLUX_MODEL:
        stt_settings.language_hints = hints

    return DeepgramFluxSTTService(
        api_key=settings.deepgram_api_key,
        sample_rate=V1_SAMPLE_RATE,
        should_interrupt=False,
        settings=stt_settings,
    )


def _build_tts(snapshot: ConfigSnapshot, settings: Settings) -> CartesiaTTSService:
    """Construct the Cartesia TTS service. Language is switched per turn via
    TTSUpdateSettingsFrame from the LanguageProcessor."""

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


def _build_llm(snapshot: ConfigSnapshot, settings: Settings) -> CerebrasLLMService:
    """Construct the Cerebras GPT-OSS LLM service from the runtime config."""

    cfg = _llm_cfg(snapshot)
    return CerebrasLLMService(
        api_key=settings.cerebras_api_key,
        settings=CerebrasLLMSettings(
            model=cfg.get("model", "gpt-oss-120b"),
            temperature=cfg.get("temperature", 0.7),
            max_tokens=cfg.get("max_tokens", 160),
        ),
    )


def _build_context(snapshot: ConfigSnapshot) -> LLMContext:
    """Build the LLM context seeded with the system prompt."""

    return LLMContext(
        messages=[{"role": "system", "content": snapshot["SYSTEM_PROMPT"]}],
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
    return LLMContextAggregatorPair(context=context, user_params=user_params)


def _normalize_transcript_text(text: str) -> str:
    return " ".join(text.split())


class LanguageProcessor(FrameProcessor):
    """Detect the user's language from finalized STT and switch Cartesia's
    output language per turn. Updates the per-call language state; the EOT side
    channel reads that state for metrics. Does not generate replies."""

    def __init__(self, *, call_sid: str) -> None:
        super().__init__()
        self._call_sid = call_sid
        self._last_tts_language: str | None = None

    async def process_frame(
        self, frame: Any, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        await super().process_frame(frame, direction)
        if (
            direction == FrameDirection.DOWNSTREAM
            and isinstance(frame, TranscriptionFrame)
            and getattr(frame, "finalized", False)
        ):
            await self._handle_user_language(frame)
        await self.push_frame(frame, direction)

    async def _handle_user_language(self, frame: TranscriptionFrame) -> None:
        detected = lang.detect_language_from_transcription(frame)
        state = get_session_language_state(self._call_sid) or lang.new_language_state()
        lang.update_language_state(state, user_text=frame.text or "", detected_language=detected)
        set_session_language_state(self._call_sid, state)

        response_language = state["response_language"]
        if response_language != self._last_tts_language:
            self._last_tts_language = response_language
            enum = lang.to_language_enum(response_language)
            if enum is not None:
                await self.push_frame(
                    TTSUpdateSettingsFrame(delta=CartesiaTTSSettings(language=enum)),
                    FrameDirection.DOWNSTREAM,
                )


def _record_greeting(*, call_sid: str, greeting_text: str) -> None:
    append_session_transcript_event(
        call_sid, role="assistant", phase="greeting", text=greeting_text
    )


def _build_pipeline_processors(
    *,
    transport: FastAPIWebsocketTransport,
    snapshot: ConfigSnapshot,
    settings: Settings,
    call_sid: str,
    stream_sid: str | None,
    metrics_observer: TurnMetricsObserver,
) -> list[Any]:
    """Construct the full V3 processor list and wire the EOT side channel."""

    stt = _build_stt(snapshot, settings)
    tts = _build_tts(snapshot, settings)
    llm = _build_llm(snapshot, settings)
    context = _build_context(snapshot)
    aggregator_pair = _build_aggregator_pair(context)
    language_processor = LanguageProcessor(call_sid=call_sid)
    turn_collector = TurnCollector(call_sid=call_sid, stream_sid=stream_sid)

    if hasattr(stt, "event_handler"):
        @stt.event_handler("on_end_of_turn")
        async def _on_end_of_turn(service: Any, transcript: str) -> None:
            now = time.time()
            result = turn_collector.observe(
                TurnEvent(event_type="end_of_turn", text=transcript, timestamp=utc_now()),
                phase="task",
                greeting_pending=False,
            )
            turn = result.turn
            if turn is None:
                return
            append_session_transcript_event(
                call_sid, role="user", phase="task",
                text=_normalize_transcript_text(turn.text),
            )
            metrics_observer.note_user_turn(
                turn_id=turn.turn_index,
                user_text=turn.text,
                stt_eot_received_at=now,
                turn_committed_at=time.time(),
            )
            state = get_session_language_state(call_sid) or lang.new_language_state()
            metrics_observer.note_language(
                detected_language=state.get("last_user_language"),
                response_language=state.get("response_language"),
            )

    return [
        transport.input(),
        stt,
        language_processor,
        aggregator_pair.user(),
        llm,
        tts,
        transport.output(),
        aggregator_pair.assistant(),
    ]


class PipelineExecutionResult:
    """Serializable pipeline outcome returned to the route."""

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

    Receives the already-constructed transport and serializable inputs, runs the
    graph to completion, and returns a serializable result. The router owns
    session creation/cleanup and the finalize boundary.
    """

    log = logger.bind(call_sid=call_sid)

    def _on_turn_record(record: dict[str, Any]) -> None:
        append_turn_record(call_sid, record)
        assistant_text = record.get("assistant_text")
        if assistant_text:
            append_session_transcript_event(
                call_sid, role="assistant", phase="task",
                text=_normalize_transcript_text(assistant_text),
            )

    metrics_observer = TurnMetricsObserver(on_record=_on_turn_record)

    pipeline = Pipeline(
        _build_pipeline_processors(
            transport=transport,
            snapshot=snapshot,
            settings=settings,
            call_sid=call_sid,
            stream_sid=stream_sid,
            metrics_observer=metrics_observer,
        )
    )

    latency_observer = UserBotLatencyObserver()

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            audio_in_sample_rate=V1_SAMPLE_RATE,
            audio_out_sample_rate=V1_SAMPLE_RATE,
        ),
        observers=[latency_observer, metrics_observer],
    )

    @latency_observer.event_handler("on_first_bot_speech_latency")
    async def _on_first_bot_speech(observer: Any, latency_seconds: float) -> None:
        log.info("First bot speech latency", latency_secs=f"{latency_seconds:.3f}")
        update_session_first_latency(call_sid, latency_seconds, observed_at=time.time())

    @transport.event_handler("on_client_disconnected")
    async def _on_client_disconnected(transport_ref: Any, client: Any) -> None:
        log.info("Transport client disconnected, cancelling pipeline task")
        await task.cancel()

    # --- Initial greeting (bot speaks first) ---
    if greeting_start_delay_ms > 0:
        await asyncio.sleep(greeting_start_delay_ms / 1000)

    greeting_source = prepared_greeting_text if opening_mode == "outbound" else None
    cleaned_greeting = _normalize_transcript_text(
        greeting_source or snapshot["INITIAL_GREETING_FALLBACK_TEXT"]
    )
    _record_greeting(call_sid=call_sid, greeting_text=cleaned_greeting)
    update_session_metric(call_sid, "first_tts_frame_queued_at", time.time())
    await task.queue_frame(TTSSpeakFrame(cleaned_greeting, append_to_context=True))

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
