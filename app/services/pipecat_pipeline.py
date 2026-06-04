"""Pipecat pipeline service for the V3 open-ended voice agent.

Assembles and runs the multilingual STT -> LLM -> TTS pipeline over the Exotel
transport. Processor order:

    transport.input()
      -> Deepgram Flux STT (flux-general-multi; background connect, §deferred)
      -> SttLanguageHintProcessor (records weak detected-language hint only)
      -> MemoryProcessor (system block = persona + <en>/<hi> policy + memory)
      -> context_aggregator.user()
      -> Cerebras GPT-OSS LLM (reply starts with an <en>/<hi> tag)
      -> LanguagePrefixProcessor (strip tag, set Cartesia language, stream text)
      -> Cartesia TTS
      -> transport.output()
      -> context_aggregator.assistant()

Language is **LLM-led**: the model tags each reply ``<en>``/``<hi>`` and the
LanguagePrefixProcessor routes Cartesia's language from that tag while streaming
the spoken text — one LLM call per turn, no regex override engine, no JSON
buffering. Detected STT language is only a weak prompt hint.

The committed user turn is driven natively by Flux end-of-turn (finalized
TranscriptionFrame + UserStoppedSpeakingFrame), which flushes the user
aggregator into the LLM. TurnCollector + TurnMetricsObserver ride the
``on_end_of_turn`` side channel for commit/transcript/metrics; they do NOT
generate replies. The pre-dial greeting is the only manually queued speech.
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
    get_session_memory,
    get_session_transcript,
    set_session_language_state,
    set_session_memory,
    update_session_metric,
    update_session_first_latency,
)
from app.services import language as lang
from app.services import memory as mem
from app.services.call_metrics import TurnMetricsObserver
from app.services.deferred_flux_stt import DeferredConnectDeepgramFluxSTTService
from app.services.language_prefix import LanguagePrefixProcessor
from app.services.turn_collector import TurnCollector, utc_now

MULTILINGUAL_FLUX_MODEL = "flux-general-multi"


def _stt_cfg(snapshot: ConfigSnapshot) -> dict[str, Any]:
    return (snapshot.get("runtime_config") or {}).get("stt") or {}


def _llm_cfg(snapshot: ConfigSnapshot) -> dict[str, Any]:
    return (snapshot.get("runtime_config") or {}).get("llm") or {}


def _build_stt(snapshot: ConfigSnapshot, settings: Settings) -> DeepgramFluxSTTService:
    """Construct the Deepgram Flux STT service from the runtime config.

    Uses ``flux-general-multi`` with language hints for Hindi/English. Hints are
    only honored on the multilingual model, so they are only passed there. When
    ``stt.defer_connect_on_start`` is set (default), the deferred-connect subclass
    is used so the opening greeting isn't blocked behind the Flux websocket.
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

    stt_cls = (
        DeferredConnectDeepgramFluxSTTService
        if cfg.get("defer_connect_on_start", True)
        else DeepgramFluxSTTService
    )
    return stt_cls(
        api_key=settings.deepgram_api_key,
        sample_rate=V1_SAMPLE_RATE,
        should_interrupt=False,
        settings=stt_settings,
    )


def _build_tts(snapshot: ConfigSnapshot, settings: Settings) -> CartesiaTTSService:
    """Construct the Cartesia TTS service. Language is switched per turn via
    TTSUpdateSettingsFrame emitted by the LanguagePrefixProcessor (from the
    LLM's <en>/<hi> tag)."""

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


def _user_mute_strategies() -> list[Any]:
    """User-aggregator mute strategies (greeting-phase audio safety).

    ``MuteUntilFirstBotComplete`` keeps the caller muted until the opening
    greeting finishes, so early user audio cannot create a misleading turn and
    STT need not be connected during the greeting — this is precisely what makes
    the deferred Flux STT connect safe. ``AlwaysUserMute`` then mutes the caller
    whenever the bot is speaking.
    """
    return [
        MuteUntilFirstBotCompleteUserMuteStrategy(),
        AlwaysUserMuteStrategy(),
    ]


def _build_aggregator_pair(context: LLMContext) -> LLMContextAggregatorPair:
    user_params = LLMUserAggregatorParams(
        user_turn_strategies=ExternalUserTurnStrategies(),
        vad_analyzer=SileroVADAnalyzer(sample_rate=V1_SAMPLE_RATE),
        user_mute_strategies=_user_mute_strategies(),
    )
    return LLMContextAggregatorPair(context=context, user_params=user_params)


def _normalize_transcript_text(text: str) -> str:
    return " ".join(text.split())


class SttLanguageHintProcessor(FrameProcessor):
    """Record the weak STT-detected language as a per-call hint.

    It does NOT decide the response language and does NOT switch TTS — the LLM's
    ``<en>``/``<hi>`` tag is the authority (routed downstream by the
    LanguagePrefixProcessor). The hint feeds the prompt and the metrics
    detected-language field. Does not generate replies.
    """

    def __init__(self, *, call_sid: str) -> None:
        super().__init__()
        self._call_sid = call_sid

    async def process_frame(
        self, frame: Any, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        await super().process_frame(frame, direction)
        if (
            direction == FrameDirection.DOWNSTREAM
            and isinstance(frame, TranscriptionFrame)
            and getattr(frame, "finalized", False)
        ):
            detected = lang.detect_language_from_transcription(frame)
            state = get_session_language_state(self._call_sid) or lang.new_language_state()
            lang.record_stt_language_hint(state, detected)
            set_session_language_state(self._call_sid, state)
        await self.push_frame(frame, direction)


class MemoryProcessor(FrameProcessor):
    """Govern the LLM context for bounded in-call memory.

    On each finalized user turn (before the user aggregator appends the message)
    it rebuilds the system message from the per-call memory (language
    instruction + pinned facts + running summary + optional recall snippets) and
    trims history to the most recent N turn pairs. The current user message is
    appended by the downstream aggregator; the running summary is refreshed
    post-turn (never here), so this stays cheap and non-blocking.
    """

    def __init__(
        self,
        *,
        call_sid: str,
        context: LLMContext,
        base_system_prompt: str,
        memory_cfg: dict[str, Any],
    ) -> None:
        super().__init__()
        self._call_sid = call_sid
        self._context = context
        self._base_system = base_system_prompt
        self._cfg = memory_cfg

    async def process_frame(
        self, frame: Any, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        await super().process_frame(frame, direction)
        if (
            direction == FrameDirection.DOWNSTREAM
            and isinstance(frame, TranscriptionFrame)
            and getattr(frame, "finalized", False)
        ):
            self._govern_context(frame.text or "")
        await self.push_frame(frame, direction)

    def _govern_context(self, user_text: str) -> None:
        memory = get_session_memory(self._call_sid) or mem.new_call_memory()
        memory["pinned_facts"] = mem.extract_pinned_facts(
            user_text, memory.get("pinned_facts", []),
            max_pinned=int(self._cfg.get("max_pinned_facts", 12)),
        )
        set_session_memory(self._call_sid, memory)

        lstate = get_session_language_state(self._call_sid) or lang.new_language_state()
        hint = lang.language_hint_line(lstate)

        recall: list[str] = []
        if self._cfg.get("recall_only_when_user_asks", True) and mem.recall_triggered(user_text):
            recall = mem.select_recall_snippets(
                get_session_transcript(self._call_sid),
                max_snippets=int(self._cfg.get("max_recall_snippets", 2)),
            )

        system_block = mem.build_system_block(
            self._base_system,
            language_policy=lang.RESPONSE_LANGUAGE_POLICY,
            language_hint=hint,
            pinned_facts=memory["pinned_facts"],
            running_summary=memory.get("running_summary"),
            recall_snippets=recall,
        )
        trimmed = mem.trim_recent_messages(
            self._context.messages,
            recent_turn_pairs=int(self._cfg.get("recent_turn_pairs", 6)),
        )
        self._context.set_messages([{"role": "system", "content": system_block}] + trimmed)


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
    stt_language_hint = SttLanguageHintProcessor(call_sid=call_sid)
    memory_processor = MemoryProcessor(
        call_sid=call_sid,
        context=context,
        base_system_prompt=snapshot["SYSTEM_PROMPT"],
        memory_cfg=(snapshot.get("runtime_config") or {}).get("memory") or {},
    )
    language_prefix = LanguagePrefixProcessor(
        call_sid=call_sid, metrics_observer=metrics_observer
    )
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
            # Language is annotated by the LanguagePrefixProcessor (from the LLM
            # tag) and resolved at metric finalize, so it is not read here.

    return [
        transport.input(),
        stt,
        stt_language_hint,
        memory_processor,
        aggregator_pair.user(),
        llm,
        language_prefix,
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
    memory_cfg = (snapshot.get("runtime_config") or {}).get("memory") or {}

    def _on_turn_record(record: dict[str, Any]) -> None:
        append_turn_record(call_sid, record)
        assistant_text = record.get("assistant_text")
        if assistant_text:
            append_session_transcript_event(
                call_sid, role="assistant", phase="task",
                text=_normalize_transcript_text(assistant_text),
            )
        # Post-turn (never before the reply): refresh the running summary if due.
        memory = get_session_memory(call_sid) or mem.new_call_memory()
        mem.update_summary_if_due(
            memory,
            turn_count=int(record.get("turn_id") or 0),
            transcript=get_session_transcript(call_sid),
            memory_cfg=memory_cfg,
        )
        set_session_memory(call_sid, memory)

    def _language_provider() -> tuple[str | None, str | None]:
        state = get_session_language_state(call_sid) or {}
        return state.get("last_user_language"), state.get("response_language")

    metrics_observer = TurnMetricsObserver(
        on_record=_on_turn_record, language_provider=_language_provider
    )

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
