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
    InterimTranscriptionFrame,
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
from pipecat.turns.user_start import ExternalUserTurnStartStrategy
from pipecat.turns.user_stop import ExternalUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

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
    # request_timeout_seconds is the real per-request timeout: OpenAI/Cerebras
    # base uses it as ``retry_timeout_secs`` for ``chat.completions.create``.
    # Passing it here makes the config field truthful for the in-call LLM (it was
    # previously unwired, so the 5.0s base default silently applied). LLM-2.
    return CerebrasLLMService(
        api_key=settings.cerebras_api_key,
        settings=CerebrasLLMSettings(
            model=cfg.get("model", "gpt-oss-120b"),
            temperature=cfg.get("temperature", 0.7),
            max_tokens=cfg.get("max_tokens", 160),
        ),
        retry_timeout_secs=float(cfg.get("request_timeout_seconds", 10.0)),
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


def _build_user_turn_strategies(*, turn_stop_timeout_ms: int) -> UserTurnStrategies:
    """External turn strategies with a configurable post-STT debounce.

    The user turn is driven externally by Flux EOT (UserStoppedSpeakingFrame +
    finalized TranscriptionFrame). Pipecat's ``ExternalUserTurnStopStrategy``
    default debounce is 500ms; because ``UserStoppedSpeakingFrame`` (a SystemFrame)
    overtakes the queued finalized ``TranscriptionFrame``, the strategy's immediate
    trigger misses and that timeout — not the immediate path — is what fires the
    LLM. So it is a fixed per-turn pre-LLM delay. Exposing it via
    ``stt.turn_stop_timeout_ms`` lets us tune and live-validate it without a
    redeploy (LAT-2). NOTE: this is NOT Deepgram endpointing (``eot_timeout_ms``);
    it is the post-STT user-turn debounce.
    """
    return UserTurnStrategies(
        start=[ExternalUserTurnStartStrategy()],
        stop=[ExternalUserTurnStopStrategy(timeout=turn_stop_timeout_ms / 1000.0)],
    )


def _build_aggregator_pair(
    context: LLMContext, *, turn_stop_timeout_ms: int = 500
) -> LLMContextAggregatorPair:
    user_params = LLMUserAggregatorParams(
        user_turn_strategies=_build_user_turn_strategies(turn_stop_timeout_ms=turn_stop_timeout_ms),
        vad_analyzer=SileroVADAnalyzer(sample_rate=V1_SAMPLE_RATE),
        user_mute_strategies=_user_mute_strategies(),
    )
    return LLMContextAggregatorPair(context=context, user_params=user_params)


def _normalize_transcript_text(text: str) -> str:
    return " ".join(text.split())


def _stt_ready_state(service: Any, turn_time: float) -> bool | None:
    """Per-turn STT readiness (tri-state), from the actual connect time.

    Replaces the old one-way "early audio dropped at startup" latch, which once
    tripped marked EVERY later turn not-ready even though the EOT proves STT was
    connected (OBS-1). The deferred STT exposes ``connected_at``: ready iff it
    connected before this turn. A non-deferred service has no ``connected_at`` and
    connects (blocking) in ``start()``, so it is ready by the first turn -> True.
    A missing/not-yet-observed connect time is unknown (None), never False.
    """
    if not hasattr(service, "connected_at"):
        return True
    connected_at = service.connected_at
    if connected_at is None:
        return None
    return connected_at <= turn_time


class SttLanguageHintProcessor(FrameProcessor):
    """Record the weak STT-detected language as a per-call hint + log transcripts.

    It does NOT decide the response language and does NOT switch TTS — the LLM's
    ``<en>``/``<hi>`` tag is the authority (routed downstream by the
    LanguagePrefixProcessor). The hint feeds the prompt and the metrics
    detected-language field. Does not generate replies.

    It also logs the raw STT view at DEBUG — interim and finalized transcripts
    with their detected language — so STT-quality issues (truncation, fragmented
    or mis-recognized Hinglish/proper nouns) can be measured directly against the
    audio rather than inferred from the LLM's reply (STT-1). Logging only carries
    call_sid + (clipped) transcript text + language; no secrets.
    """

    def __init__(self, *, call_sid: str) -> None:
        super().__init__()
        self._call_sid = call_sid

    async def process_frame(
        self, frame: Any, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            self._observe_transcript(frame)
        await self.push_frame(frame, direction)

    def _observe_transcript(self, frame: Any) -> None:
        """Log the raw STT view (interim + final) and record the weak final hint.

        Logging carries only call_sid + clipped transcript text + detected
        language — never settings or credentials (STT-1). Tolerant of frames that
        lack a ``language``/``text`` attribute so a malformed frame can't crash STT.
        """
        if isinstance(frame, InterimTranscriptionFrame):
            logger.debug(
                "STT interim transcript",
                call_sid=self._call_sid,
                language=lang.detect_language_from_transcription(frame),
                text=_normalize_transcript_text(getattr(frame, "text", "") or "")[:160],
            )
        elif isinstance(frame, TranscriptionFrame) and getattr(frame, "finalized", False):
            detected = lang.detect_language_from_transcription(frame)
            logger.debug(
                "STT final transcript",
                call_sid=self._call_sid,
                language=detected,
                finalized=True,
                text=_normalize_transcript_text(getattr(frame, "text", "") or "")[:160],
            )
            state = get_session_language_state(self._call_sid) or lang.new_language_state()
            lang.record_stt_language_hint(state, detected)
            set_session_language_state(self._call_sid, state)


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
        metrics_observer: Any | None = None,
    ) -> None:
        super().__init__()
        self._call_sid = call_sid
        self._context = context
        self._base_system = base_system_prompt
        self._cfg = memory_cfg
        self._metrics = metrics_observer

    async def process_frame(
        self, frame: Any, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        await super().process_frame(frame, direction)
        if (
            direction == FrameDirection.DOWNSTREAM
            and isinstance(frame, TranscriptionFrame)
            and getattr(frame, "finalized", False)
        ):
            started = time.perf_counter()
            self._govern_context(frame.text or "")
            duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
            if self._metrics is not None:
                self._metrics.note_memory_timing(
                    duration_ms=duration_ms,
                    context_message_count=len(self._context.messages),
                )
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

        # Always lexical-search; inject only what scores above the bar (never
        # regex-gated). An explicit back-reference ("you told me…", "pehle…")
        # relaxes the threshold so an explicit ask reliably surfaces something.
        recent_pairs = int(self._cfg.get("recent_turn_pairs", 6))
        min_overlap = int(self._cfg.get("recall_min_overlap", 2))
        if mem.recall_triggered(user_text):
            min_overlap = 1
        recall = mem.search_recall_snippets(
            get_session_transcript(self._call_sid),
            query=user_text,
            max_snippets=int(self._cfg.get("max_recall_snippets", 2)),
            min_overlap=min_overlap,
            exclude_recent=recent_pairs * 2,
        )

        system_block = mem.build_system_block(
            self._base_system,
            language_policy=lang.RESPONSE_LANGUAGE_POLICY,
            language_hint=hint,
            pinned_facts=memory["pinned_facts"],
            running_summary=memory.get("running_summary"),
            recall_snippets=recall,
            max_dynamic_tokens=int(self._cfg.get("max_memory_block_tokens", 600)),
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


def _schedule_opening_greeting(
    *,
    transport: Any,
    task: Any,
    log: Any,
    call_sid: str,
    greeting_text: str,
    settle_ms: int,
) -> dict[str, Any]:
    """Speak the opening greeting once the carrier client is connected.

    Registers an ``on_client_connected`` handler — which the transport fires
    unconditionally when its input starts — that, after a short settle delay,
    records and queues the greeting. The greeting fires **autonomously**: it never
    waits for the caller to speak. Speaking before the client is connected lets the
    carrier (Exotel) drop the first audio (it is synthesized on our side, so
    BotStartedSpeaking fires, but the caller never hears it). The settle delay runs
    in a background task so it does not block transport startup. Returns a shared
    state dict so the caller's disconnect handler can cancel a pending greeting.
    """
    state: dict[str, Any] = {"queued": False, "task": None}

    async def _speak_greeting() -> None:
        try:
            if settle_ms > 0:
                await asyncio.sleep(settle_ms / 1000)
            _record_greeting(call_sid=call_sid, greeting_text=greeting_text)
            update_session_metric(call_sid, "first_tts_frame_queued_at", time.time())
            log.info("Speaking opening greeting", settle_ms=settle_ms, chars=len(greeting_text))
            await task.queue_frame(TTSSpeakFrame(greeting_text, append_to_context=True))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # greeting scheduling must never crash the call
            log.warning("Failed to queue opening greeting: {err}", err=str(exc))

    @transport.event_handler("on_client_connected")
    async def _on_client_connected(transport_ref: Any, client: Any) -> None:
        if state["queued"]:
            return
        state["queued"] = True
        log.info("Exotel client connected; scheduling opening greeting", settle_ms=settle_ms)
        state["task"] = asyncio.create_task(_speak_greeting())

    return state


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
    aggregator_pair = _build_aggregator_pair(
        context,
        turn_stop_timeout_ms=int(_stt_cfg(snapshot).get("turn_stop_timeout_ms", 500)),
    )
    stt_language_hint = SttLanguageHintProcessor(call_sid=call_sid)
    memory_processor = MemoryProcessor(
        call_sid=call_sid,
        context=context,
        base_system_prompt=snapshot["SYSTEM_PROMPT"],
        memory_cfg=(snapshot.get("runtime_config") or {}).get("memory") or {},
        metrics_observer=metrics_observer,
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
            lstate = get_session_language_state(call_sid) or {}
            # Per-turn readiness from the actual connect time (tri-state), not the
            # startup early-audio latch which poisoned every later turn (OBS-1).
            stt_ready = _stt_ready_state(service, now)
            logger.info(
                "Flux end_of_turn",
                call_sid=call_sid,
                turn_id=turn.turn_index,
                detected_language=lstate.get("last_user_language"),
                stt_ready=stt_ready,
                transcript=_normalize_transcript_text(turn.text)[:160],
            )
            append_session_transcript_event(
                call_sid, role="user", phase="task",
                text=_normalize_transcript_text(turn.text),
            )
            metrics_observer.note_user_turn(
                turn_id=turn.turn_index,
                user_text=turn.text,
                stt_eot_received_at=now,
                turn_committed_at=time.time(),
                stt_ready=stt_ready,
            )
            # Language is annotated by the LanguagePrefixProcessor (from the LLM
            # tag) and resolved at metric finalize, so it is not read here.

        # Lightweight visibility into Flux's turn-boundary decisions. These are
        # diagnostic only (DEBUG) — no behavior change, no EOT tuning. They help
        # explain incomplete-utterance turns caused by eot_timeout firing mid-speech.
        @stt.event_handler("on_start_of_turn")
        async def _on_start_of_turn(service: Any, *args: Any) -> None:
            logger.debug("Flux start_of_turn", call_sid=call_sid)

        @stt.event_handler("on_eager_end_of_turn")
        async def _on_eager_end_of_turn(service: Any, *args: Any) -> None:
            preview = _normalize_transcript_text(str(args[0]))[:80] if args else ""
            logger.debug("Flux eager_end_of_turn", call_sid=call_sid, transcript=preview)

        @stt.event_handler("on_turn_resumed")
        async def _on_turn_resumed(service: Any, *args: Any) -> None:
            logger.debug("Flux turn_resumed", call_sid=call_sid)

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

    # --- Initial greeting (bot speaks first) ---
    # The greeting is spoken from on_client_connected (see _schedule_opening_greeting)
    # so the carrier media path is up before we send audio, then a short settle
    # delay. It fires autonomously — it never waits for the caller to speak.
    greeting_source = prepared_greeting_text if opening_mode == "outbound" else None
    # Defensive: the greeting is spoken via TTSSpeakFrame and never passes through
    # LanguagePrefixProcessor, so strip any leaked <en>/<hi> tag before it is
    # spoken or stored (the outbound path also strips in validate_greeting_text) —
    # LANG-1.
    cleaned_greeting = lang.strip_language_prefix_tag(
        _normalize_transcript_text(greeting_source or snapshot["INITIAL_GREETING_FALLBACK_TEXT"])
    )
    settle_ms = (
        greeting_start_delay_ms
        if greeting_start_delay_ms > 0
        else settings.greeting_settle_delay_ms
    )
    greeting_state = _schedule_opening_greeting(
        transport=transport, task=task, log=log, call_sid=call_sid,
        greeting_text=cleaned_greeting, settle_ms=settle_ms,
    )

    @transport.event_handler("on_client_disconnected")
    async def _on_client_disconnected(transport_ref: Any, client: Any) -> None:
        log.info("Transport client disconnected, cancelling pipeline task")
        greeting_task = greeting_state.get("task")
        if greeting_task is not None and not greeting_task.done():
            greeting_task.cancel()
        await task.cancel()

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
