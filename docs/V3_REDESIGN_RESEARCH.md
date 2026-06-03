# Voicebot V3 Redesign — Technical Research Report

> Status: research only. No implementation performed. All claims are backed by exact
> file/function references read directly from the codebase on the `main` branch.
> Where evidence is missing, it is marked **UNKNOWN**.

---

## A. Executive Summary (blunt)

**What is working (transport + audio plumbing):**
- Exotel outbound dial works ([app/services/exotel_client.py](../app/services/exotel_client.py)).
- Exotel WebSocket transport + serializer works ([app/services/transport_factory.py](../app/services/transport_factory.py)).
- Deepgram Flux STT, Cartesia TTS, and the Pipecat pipeline run ([app/services/pipecat_pipeline.py:499-507](../app/services/pipecat_pipeline.py#L499-L507)).
- Greeting starts immediately on connect — the pipeline is bot-initiated, no "hello" needed ([app/services/pipecat_pipeline.py:633-657](../app/services/pipecat_pipeline.py#L633-L657)).

**What is fake / misleading:**
- **There is no LLM in the live conversation pipeline.** Cerebras does not appear in the processor list. The `LLMContext` and `LLMContextAggregatorPair` are present but no LLM service sits between them, so they generate nothing ([app/services/pipecat_pipeline.py:499-507](../app/services/pipecat_pipeline.py#L499-L507)).
- The dashboard shows **LLM Model: gpt-oss-120b** for every call ([dashboard.html:1304-1305](../app/templates/dashboard.html#L1304-L1305)) read straight from the config snapshot, even though no LLM runs during turns. **Per-turn LLM TTFB is always blank** because no LLM-category metric is ever emitted ([dashboard.html:1545-1556](../app/templates/dashboard.html#L1545-L1556)). This is misleading.
- Cerebras is only used to *pre-generate the greeting* in an isolated throwaway pipeline ([app/services/conversation_controller.py:80-100](../app/services/conversation_controller.py#L80-L100)), and it routinely falls back (see §D).

**What is hardcoded:**
- The entire "brain" is deterministic: refusal/question/readiness/slot extraction is regex-driven ([app/services/flow_runner.py:368-547](../app/services/flow_runner.py#L368-L547)), and all assistant speech is templated/canned ([app/services/speech_composer.py](../app/services/speech_composer.py)).
- The flow is fixed `readiness → name → location → role → closing` ([app/services/config_repository.py:89-151](../app/services/config_repository.py#L89-L151)).
- STT is hardcoded to `flux-general-en` (English-only) and ignores the snapshot's STT config ([app/services/pipecat_pipeline.py:71-86](../app/services/pipecat_pipeline.py#L71-L86)).
- Only one bot profile exists; `bot_profile_id` from the dashboard is silently ignored (see §E).

**What blocks open-ended conversation:** No LLM in the turn path; the response is produced by `SimpleFlowRunner` + `SpeechComposer`, which can only emit slot questions and fallback lines. Any utterance that is not refusal/question/readiness/name/location/role is met with a canned clarification.

**What blocks sub-1s latency:** Flux `eot_timeout_ms=1000` alone can add up to 1s before a turn even commits ([app/services/pipecat_pipeline.py:81-85](../app/services/pipecat_pipeline.py#L81-L85)); plus Cartesia `SENTENCE` aggregation waits for a sentence boundary before TTS ([app/services/pipecat_pipeline.py:89-115](../app/services/pipecat_pipeline.py#L89-L115)). There is no LLM, so adding one will *increase* latency unless streamed. "Turn latency" is measured from VAD-user-stop to bot-speech-start (see §G), so it already includes EOT wait.

**What blocks multilingual switching:** STT is English-only and hardcoded; no language is detected, tracked, or fed back to TTS/LLM anywhere in the codebase (see §F). `ConversationState` has no language field.

---

## B. Current Architecture Map (call graph)

```
Outbound dial (dashboard form POST)
  app/routers/dashboard.py:post_outbound (L110)
   ├─ build_config_snapshot(settings, profile_id)            runtime.py:76 → config_repository.get_bot_profile_snapshot:196
   ├─ generate_outbound_greeting_text(...)  [Cerebras, isolated pipeline, 2s timeout]
   │     conversation_controller.py:151  → _run_llm_text_generation:80 → validate_greeting_text:126
   │     (on timeout/validation-fail → greeting_text=None, greeting_text_source="fallback")
   ├─ create_pending_outbound_context(...)                   runtime.py:201
   └─ client.make_call(to_number)  → ExotelOutboundClient.make_call  exotel_client.py:106
         └─ attach_pending_call_sid(ctx, call_sid)           runtime.py:223  (stores ctx in PENDING_OUTBOUND_CALLS)

Exotel answers → Voicebot Applet → GET /telephony/webhook
  telephony.py:exotel_webhook (L34) → returns wss URL (sample-rate=16000)

Exotel opens WS → /telephony/exotel
  telephony.py:exotel_websocket (L81)
   ├─ resolve call_sid/stream_sid (query or parse_telephony_websocket)
   ├─ get_pending_outbound_context(call_sid)                 runtime.py:236
   ├─ create_session(...)  → ACTIVE_SESSIONS[call_sid]       runtime.py:88
   ├─ build_exotel_transport(...)                            transport_factory.py:26
   └─ run_pipeline(...)                                      pipecat_pipeline.py:522
        ├─ Pipeline(_build_pipeline_processors(...))         pipecat_pipeline.py:446
        │     processors: transport.input(), stt, conversation_processor,
        │                 aggregator_pair.user(), tts, aggregator_pair.assistant(),
        │                 transport.output()                 pipecat_pipeline.py:499-507
        ├─ register UserBotLatencyObserver                   pipecat_pipeline.py:575-621
        ├─ queue TTSSpeakFrame(greeting)  [bot speaks first] pipecat_pipeline.py:640-657
        └─ runner.run(task)

User speaks → Deepgram Flux on_end_of_turn
  stt event_handler _on_end_of_turn                          pipecat_pipeline.py:477
   └─ UserConversationProcessor.handle_end_of_turn:258
        └─ TurnCollector.observe → commits TurnInput         turn_collector.py:53
             └─ _handle_committed_turn:302
                  ├─ ConversationRuntime.handle_turn(turn)   conversation_runtime.py:51
                  │     ├─ SimpleFlowRunner.handle_turn       flow_runner.py:60   (regex NLU)
                  │     ├─ RepeatedQuestionGuard.guard_action repeated_question_guard.py:19
                  │     └─ SpeechComposer.compose             speech_composer.py:42 (canned text)
                  ├─ append_session_transcript_event(...)     runtime.py:270
                  └─ push_frame(TTSSpeakFrame(assistant_text))  pipecat_pipeline.py:334
                        → Cartesia TTS → transport.output() → Exotel audio out

Disconnect → transport on_client_disconnected → task.cancel()  pipecat_pipeline.py:627
  → runner unwinds → telephony.py finalize:
       finalize_call_log(...)  metrics_logger.py:278
       persist_call_log(...)   database.py:62  → MongoDB call_logs
       delete_session(call_sid)  runtime.py:118
```

**Note:** The committed-turn response path (`on_end_of_turn` → `_handle_committed_turn` → `TTSSpeakFrame`) is **out-of-band** from the Pipecat aggregator/LLM path. The `aggregator_pair.user()`/`assistant()` and `LLMContext` exist but never drive generation; the brain pushes a `TTSSpeakFrame` directly from the `conversation_processor` slot in the pipeline.

---

## C. Current Pipeline Processor List

From [app/services/pipecat_pipeline.py:499-507](../app/services/pipecat_pipeline.py#L499-L507), in order:

1. `transport.input()` — `FastAPIWebsocketTransport` (Exotel serializer)
2. `stt` — `DeepgramFluxSTTService(model="flux-general-en", should_interrupt=False, eot_threshold=0.65, eot_timeout_ms=1000)` ([L71-86](../app/services/pipecat_pipeline.py#L71-L86))
3. `conversation_processor` — `UserConversationProcessor` (the deterministic brain bridge) ([L202](../app/services/pipecat_pipeline.py#L202))
4. `aggregator_pair.user()` — `LLMContextAggregatorPair.user()` with `ExternalUserTurnStrategies`, `SileroVADAnalyzer`, mute strategies ([L128-140](../app/services/pipecat_pipeline.py#L128-L140))
5. `tts` — `CartesiaTTSService(text_aggregation_mode=SENTENCE, max_buffer_delay_ms=0)` ([L89-115](../app/services/pipecat_pipeline.py#L89-L115))
6. `aggregator_pair.assistant()`
7. `transport.output()`

**Is Cerebras in the processor list? NO.** There is no `CerebrasLLMService` import in this file and no LLM processor between the user and assistant aggregators. The only LLM-ish objects are `LLMContext` and the aggregator pair, which carry context but produce no tokens because nothing generates into them. The `import ... Cerebras ...` mention exists only in the module docstring ([L11](../app/services/pipecat_pipeline.py#L11)), not in code.

---

## D. Current LLM Usage Report

**Where the LLM is used:** Only in `app/services/conversation_controller.py` to generate the *outbound greeting* before dialing, in a fully isolated mini-pipeline `[_ContextSource, CerebrasLLMService, _TextCollector]` ([L80-100](../app/services/conversation_controller.py#L80-L100)), invoked from `dashboard.post_outbound` under a 2.0s timeout ([dashboard.py:179-216](../app/routers/dashboard.py#L179-L216)).

**Where the LLM is NOT used:** Every committed user turn. `_handle_committed_turn` calls `ConversationRuntime.handle_turn`, which never calls an LLM ([conversation_runtime.py:51-103](../app/services/conversation_runtime.py#L51-L103)). Response text comes from `SpeechComposer` templates.

**Sync or async / streaming:** `CerebrasLLMService` is Pipecat's async OpenAI-compatible service. In the greeting pipeline, tokens stream as `TextFrame`s but `_TextCollector` buffers them and only returns after `LLMFullResponseEndFrame` ([conversation_controller.py:44-63](../app/services/conversation_controller.py#L44-L63)) — so streaming frames flow but the caller waits for the full response. **No streaming is wired into the live turn pipeline at all** (no LLM there).

**Why the dashboard showed `Greeting Source: fallback` + `Greeting LLM Total: 2.011s`:** The greeting generation is wrapped in `asyncio.timeout(settings.outbound_greeting_prep_timeout_secs)` where the env default is `2.0` ([config.py:40-43](../app/config.py#L40-L43), [.env.example:14](../.env.example#L14)). `greeting_llm_total_latency` is recorded as `completed_at - started_at` on *every* path including timeout/exception ([dashboard.py:188-216](../app/routers/dashboard.py#L188-L216)). A value of **2.011s is the 2.0s timeout firing** → `TimeoutError` branch → `greeting_text_source="fallback"` ([dashboard.py:195-205](../app/routers/dashboard.py#L195-L205)). The Cerebras call simply did not finish (or finished but failed `validate_greeting_text`) within 2s. **Root cause: the greeting LLM budget (2.0s) is too small for an isolated cold pipeline spin-up, so it times out and falls back.**

**Where fallback is triggered:** Three places in `dashboard.post_outbound`: `mode=="static"` → `configured`; `TimeoutError` → `fallback`; generic `Exception` (incl. `validate_greeting_text` raising) → `fallback` ([dashboard.py:166-216](../app/routers/dashboard.py#L166-L216)). At speak time, when `greeting_text is None`, the pipeline uses `INITIAL_GREETING_FALLBACK_TEXT` ([pipecat_pipeline.py:640-643](../app/services/pipecat_pipeline.py#L640-L643)).

**Is the LLM failure reason logged?** Partially. `greeting_error` string is stored on the pending context and a `logger.warning` is emitted ([dashboard.py:205,216](../app/routers/dashboard.py#L205)), but `greeting_error` is **not** persisted into `CallMetrics` ([schemas.py:75-92](../app/models/schemas.py#L75-L92), [metrics_logger.py:310-322](../app/utils/metrics_logger.py#L310-L322)) — so the dashboard cannot show *why* it fell back.

**Is LLM output sanitized/rejected?** Yes, aggressively: `validate_greeting_text` rejects >320 chars, simulated-speaker lines, >1 question mark, consent-sounding phrases, and **requires a readiness cue or a `?`** ([conversation_controller.py:115-148](../app/services/conversation_controller.py#L115-L148)). This is a second, independent fallback source besides timeout.

**Is `greeting_llm_ttfb` ever set?** **No.** It is initialized to `None` ([dashboard.py:160](../app/routers/dashboard.py#L160)) and never written. It is always blank.

**Is the dashboard misleading?** Yes. It displays `LLM Model` from the config snapshot regardless of whether any LLM ran on turns ([dashboard.html:1114,1304-1305](../app/templates/dashboard.html#L1304-L1305)), and turn-level LLM TTFB is structurally always blank because no LLM emits a metric on the turn path.

---

## E. Current Bot Profile Report

- **Are profiles persisted?** No. `config_repository` is in-memory and rebuilds the single default profile from `Settings` on every call ([config_repository.py:62-168](../app/services/config_repository.py#L62-L168)). The docstring literally says "In-memory seeded behavior config repository for Phase 1" ([L1](../app/services/config_repository.py#L1)).
- **Can the dashboard create/edit profiles?** No. There is no create/edit endpoint; `list_bot_profiles` always returns exactly one summary ([config_repository.py:182-193](../app/services/config_repository.py#L182-L193)).
- **Is `bot_profile_id` respected?** **No.** `post_outbound` reads `bot_profile_id` from the form ([dashboard.py:119-121](../app/routers/dashboard.py#L119)) and passes it to `build_config_snapshot`, but `get_bot_profile_snapshot` always builds the default components and only records `used_fallback=True` in `profile_resolution` — it returns the default profile regardless ([config_repository.py:196-235](../app/services/config_repository.py#L196-L235)). **Unknown IDs silently fall back to default.**
- **Is `default_demo_bot` hardcoded?** Yes — `DEFAULT_BOT_PROFILE_ID = "default_demo_bot"` and the flow/slots are literal ([config_repository.py:24-151](../app/services/config_repository.py#L24-L151)).
- **Where are profiles seeded?** `_build_default_components` ([config_repository.py:62](../app/services/config_repository.py#L62)).
- **BotProfile fields:** `id, name, description, system_prompt, flow_id, stt_config_id, llm_config_id, tts_config_id, greeting_config, fallback_policy, is_active, created_at, updated_at` ([config_models.py:158-175](../app/models/config_models.py#L158-L175)). Rich typed models for STT/LLM/TTS/Flow/Slots already exist but are largely unused at runtime.
- **What's needed for open-ended assistant profiles:** real persistence (Mongo-backed CRUD), profile selection actually honored, an LLM-driven (non-slot) profile type, language config, and a system-prompt-centric design rather than slot/flow-centric.

---

## F. Current Multilingual Capability Report

- **STT language config:** Hardcoded `model="flux-general-en"` (English) in `_build_stt`, with no `language`/`language_hint` passed ([pipecat_pipeline.py:71-86](../app/services/pipecat_pipeline.py#L71-L86)). `STTConfig` *defines* `language_hint` ([config_models.py:23](../app/models/config_models.py#L23)) but it is never read by the pipeline builder. **No auto-detection. No Hindi/Hinglish/code-switching support.**
- **TTS language config:** Cartesia is built with `voice` + `model` from env (`TTS_MODEL="sonic-3.5"`, a fixed `voice_id`) and **no `language` parameter** ([pipecat_pipeline.py:89-115](../app/services/pipecat_pipeline.py#L89-L115), [.env.example:9-10](../.env.example#L9-L10)). Whether `sonic-3.5` + that voice renders Hindi acceptably is **UNKNOWN** (needs a Cartesia docs check + live test).
- **Is language tracked per call?** No. `ConversationState` has no language field ([conversation_models.py:96-114](../app/models/conversation_models.py#L96-L114)).
- **Is user language detected anywhere?** No.
- **Is response language controlled anywhere?** No — all responses are English templates.
- **Can the prompt instruct language mirroring?** Not currently — there is no LLM on the turn path to receive such a prompt. The `SYSTEM_PROMPT` is English-only ([.env.example:6](../.env.example#L6)).
- **Gaps to add:** `language_state` / `last_user_language` / `response_language` on `ConversationState`; multilingual STT (Deepgram model that supports Hindi/code-switching — Flux is English-only, so a different Deepgram model/pathway is required); per-turn language detection; passing `language` to Cartesia; an LLM with a language-mirroring system prompt; and parsing of explicit "continue in English" commands.

---

## G. Current Latency Report

**What is measured today** (via `UserBotLatencyObserver`, [observers/user_bot_latency_observer.py](../.venv/Lib/site-packages/pipecat/observers/user_bot_latency_observer.py)):
- **"First Bot Speech"** = `ClientConnectedFrame` → first `BotStartedSpeakingFrame` (greeting), once per call ([observer L281-289](../.venv/Lib/site-packages/pipecat/observers/user_bot_latency_observer.py#L281-L289)). Abandoned if the user speaks first (L243-244).
- **"Turn Total Latency"** = `VADUserStoppedSpeakingFrame` (timestamp − `stop_secs`) → `BotStartedSpeakingFrame` ([observer L245-296](../.venv/Lib/site-packages/pipecat/observers/user_bot_latency_observer.py#L245-L296)). So it is measured **from actual user-silence (VAD)**, NOT from STT-final or Flux EOT. Because Flux drives the commit, the EOT wait (`eot_timeout_ms=1000`) is *inside* this window.
- **TTFB breakdown:** per-processor TTFB from `MetricsFrame`s, mapped to stt/llm/tts by substring ([metrics_logger.py:31-39](../app/utils/metrics_logger.py#L31-L39)). STT and TTS TTFB are captured; **LLM TTFB is never captured on turns (no LLM).**

**What is missing:** None of the explicit stage timestamps you listed exist: `user_eot_at`, `committed_turn_at`, `llm_request_started_at`, `llm_first_token_at`, `first_tts_chunk_sent_at`, `first_audio_out_at`. The only call-level extras are `first_tts_frame_queued_at` and `first_bot_audio_started_at` for the greeting ([runtime.py:148-150](../app/runtime.py#L148-L150), [pipecat_pipeline.py:648](../app/services/pipecat_pipeline.py#L648)). The greeting's `greeting_llm_ttfb` is defined but never set.

**Is the pipeline full-response or streaming TTS?** Today the turn brain pushes a *single complete* `TTSSpeakFrame(assistant_text)` ([pipecat_pipeline.py:334-337](../app/services/pipecat_pipeline.py#L334-L337)). Cartesia is in `SENTENCE` aggregation mode, which would chunk an LLM token stream by sentence — but there is no token stream feeding it on turns, so effectively it is full-text-per-turn.

**Is sub-1s response start possible with the current path?** Marginally, today, *because there is no LLM* — but the brain is too dumb to be useful. The structural blockers for a real LLM agent under 1s are: (1) Flux `eot_timeout_ms=1000`; (2) sentence-level TTS aggregation delaying first audio; (3) adding an LLM with no streaming. **To hit sub-1s with open-ended LLM responses you must stream LLM tokens → stream into TTS, lower the EOT timeout, and possibly use word/partial TTS aggregation.**

---

## H. Keep / Replace / Redesign Recommendation

| Component | Keep | Replace | Redesign | Reason |
|-----------|:----:|:-------:|:--------:|--------|
| Exotel integration ([exotel_client.py](../app/services/exotel_client.py), [transport_factory.py](../app/services/transport_factory.py), [telephony.py](../app/routers/telephony.py)) | ✅ | | | Works; 16k sample-rate + serializer constraints are correct. Keep. |
| Pipecat pipeline assembly ([pipecat_pipeline.py](../app/services/pipecat_pipeline.py)) | | | ✅ | Keep transport/STT/TTS wiring; insert a streaming LLM and remove the out-of-band `TTSSpeakFrame` brain. |
| Deepgram STT ([_build_stt](../app/services/pipecat_pipeline.py#L71)) | | | ✅ | Keep Deepgram, but Flux is English-only. Need multilingual model + config-driven language. |
| Cartesia TTS ([_build_tts](../app/services/pipecat_pipeline.py#L89)) | ✅ | | ◻ | Keep; add per-turn `language` param and verify multilingual voice. |
| Cerebras client ([conversation_controller.py](../app/services/conversation_controller.py)) | | | ✅ | Keep the provider; move it from "greeting-only isolated pipeline" to the **live streaming turn LLM**. |
| TurnCollector ([turn_collector.py](../app/services/turn_collector.py)) | ✅ | | ◻ | Solid dedup/commit logic; reusable as EOT manager. Minor: add language passthrough. |
| ConversationRuntime ([conversation_runtime.py](../app/services/conversation_runtime.py)) | | ✅ | | Slot/guard/compose orchestration is the wrong brain for open-ended. Replace as the *default* path; keep available for structured task bots. |
| SimpleFlowRunner ([flow_runner.py](../app/services/flow_runner.py)) | | ✅ | | Regex slot NLU; not open-ended. Retire from default path. |
| SpeechComposer ([speech_composer.py](../app/services/speech_composer.py)) | | ✅ | | Canned templates; replace with LLM generation. |
| RepeatedQuestionGuard ([repeated_question_guard.py](../app/services/repeated_question_guard.py)) | | ✅ | | Only meaningful for slot flows; not needed for open-ended. |
| ConfigRepository ([config_repository.py](../app/services/config_repository.py)) | | | ✅ | In-memory, single hardcoded profile, ignores `bot_profile_id`. Needs real persistence + honored selection + language/LLM-mode fields. |
| Dashboard metrics ([dashboard.py](../app/routers/dashboard.py), [metrics_logger.py](../app/utils/metrics_logger.py), [dashboard.html](../app/templates/dashboard.html)) | | | ✅ | Keep the UI; fix truthfulness (don't show LLM model/TTFB when no LLM ran) and add real per-stage latency + language + greeting-error fields. |
| `ACTIVE_SESSIONS` design ([runtime.py:72](../app/runtime.py#L72)) | ✅ | | ◻ | Already stores **only JSON-safe dicts** (no live Pipecat/provider objects) — good. Keep the discipline; add language/memory fields as plain data. |

(✅ primary recommendation, ◻ minor adjustment)

---

## I. V3 Redesign Options

### Option 1 — Minimal LLM-in-turn patch
Insert a `CerebrasLLMService` into the existing pipeline between the user and assistant aggregators, keep the deterministic brain as an optional pre-filter, and let the aggregator/LLM/TTS path generate replies.
- **Pros:** Smallest change; reuses existing aggregator pair and context; immediate open-ended capability.
- **Cons:** The current out-of-band `TTSSpeakFrame` brain conflicts with an LLM-driven path and must be removed/gated; non-streaming LLM would blow latency; still English-only STT.
- **Expected latency:** ~1.3–2.0s+ first audio (EOT 1s + LLM full response + sentence TTS) unless streaming added.
- **Risk:** Medium — two response mechanisms fighting; regression risk in turn handling.
- **Files changed:** `pipecat_pipeline.py` (processor list + remove `_handle_committed_turn` TTS push), `conversation_runtime` gating.

### Option 2 — Streaming LLM open-ended agent (recommended core)
Replace the deterministic brain with a streaming LLM turn path: STT(multilingual) → EOT manager → `CerebrasLLMService` (streamed) → Cartesia (streamed, word/partial aggregation), driven by `LLMContext` memory and a language-mirroring system prompt.
- **Pros:** True open-ended + multilingual + low latency via streaming; aligns with Gemini-like feel; uses Pipecat's native LLM→TTS streaming.
- **Cons:** Larger rewrite of the turn path; needs multilingual STT solution (Flux is English-only); needs new metrics.
- **Expected latency:** First audio achievable ~0.6–1.0s after EOT if EOT timeout is lowered and TTS streams on first sentence/clause.
- **Risk:** Medium-high — STT language detection accuracy, streaming correctness, barge-in re-enable.
- **Files changed:** `pipecat_pipeline.py`, `conversation_controller.py` (promote Cerebras to live), `config_repository.py`/`config_models.py` (language + LLM-mode), `conversation_models.py` (language state), `metrics_logger.py`/`dashboard.html` (truthful metrics). Retire `flow_runner`, `speech_composer`, `repeated_question_guard` from default path.

### Option 3 — Hybrid task/open-ended mode
Profile-driven: an `open_ended` profile uses the Option 2 streaming LLM path; a `structured` profile keeps `ConversationRuntime`/slots for forms. A mode flag on `BotProfile` selects the brain at pipeline-build time.
- **Pros:** Preserves the working slot machinery for genuine form-filling bots while making open-ended the default; clean separation.
- **Cons:** Two maintained brains; more config surface; the slot path keeps its English/regex limits.
- **Expected latency:** Same as Option 2 for open-ended; slot path unchanged.
- **Risk:** Medium — mostly additive; complexity in pipeline branching.
- **Files changed:** Option 2 set + a `brain_mode` field and a branch in `_build_pipeline_processors`.

---

## J. Recommended V3 Architecture

**Recommendation: Option 3 (Hybrid) with Option 2's streaming LLM as the default/open-ended brain.**

**Data flow (open-ended mode):**
```
Exotel WS → transport.input()
  → Deepgram STT (multilingual, config-driven language/detection)
  → EOT manager (reuse TurnCollector for commit/dedupe; lower eot_timeout)
  → LLMContext (system prompt with language-mirroring rule + rolling history)
  → CerebrasLLMService  (STREAMED tokens)
  → Cartesia TTS (stream first clause/sentence; per-turn language)
  → transport.output() → Exotel audio out
```

**New components needed:**
- Multilingual STT configuration path (Deepgram model that supports Hindi/Hinglish/code-switching; **Flux English-only is insufficient — confirm model choice against Deepgram docs**).
- Language detection + `language_state`/`last_user_language`/`response_language` on `ConversationState`, plus explicit-switch command parsing ("continue in English").
- Per-stage latency timestamps (`user_eot_at`, `committed_turn_at`, `llm_request_started_at`, `llm_first_token_at`, `first_tts_chunk_sent_at`, `first_audio_out_at`).
- Mongo-backed `ConfigRepository` with honored `bot_profile_id` and a `brain_mode` field.
- Truthful dashboard metrics (only show LLM model/TTFB when an LLM actually ran; surface greeting fallback reason).

**Old components to keep:** Exotel client/transport/router, `TurnCollector`, `ACTIVE_SESSIONS` JSON-only discipline, MongoDB persistence, dashboard UI shell, the typed `STTConfig/LLMConfig/TTSConfig/BotProfile` models.

**Old components to retire (from default path):** `SimpleFlowRunner`, `SpeechComposer`, `RepeatedQuestionGuard`, and the out-of-band `TTSSpeakFrame` brain in `UserConversationProcessor._handle_committed_turn`. Keep `ConversationRuntime` available only for `structured` profiles.

**Greeting design:** Move greeting to either a static configured line (instant, zero fallback) or a streamed first LLM turn on connect; raise/remove the 2.0s isolated-pipeline timeout that currently forces fallback. Persist `greeting_error` for observability.

**Failure handling:** On LLM error/timeout, stream a safe canned line (reuse `RUNTIME_ERROR_FALLBACK_TEXT` pattern) and log a structured reason into metrics; keep call alive rather than closing where possible.

**Language switching design:** detect language per committed turn → store `last_user_language` → inject "respond in {language}; mirror the user; honor explicit switch requests" into the LLM system prompt → pass `language` to Cartesia. Explicit commands ("continue in English") override detection.

**Metrics design:** capture the six stage timestamps above per turn; compute STT/LLM/TTS TTFB and end-to-end; show per-turn language; never display LLM stats for turns that ran no LLM.

---

## K. Risks

- **Latency:** Flux `eot_timeout_ms=1000` + sentence TTS + LLM TTFB can exceed 1s; mitigation requires streaming + lower EOT + clause-level TTS.
- **LLM rate limits:** Cerebras `gpt-oss-120b` under per-turn live load (vs. one greeting call today); needs retry/backoff and a streaming-safe fallback.
- **Cerebras streaming support:** Pipecat `CerebrasLLMService` is OpenAI-compatible and streams (greeting pipeline already receives token `TextFrame`s), but **live streaming-into-TTS under interruption has not been exercised in this repo** — verify.
- **STT language detection accuracy:** Deepgram Flux is English-only; the multilingual replacement's Hindi/Hinglish/code-switch accuracy is **UNKNOWN** and must be validated against Deepgram docs + live calls.
- **Cartesia multilingual voice:** whether `sonic-3.5` + the configured `voice_id` renders natural Hindi is **UNKNOWN**; needs a docs check and live test, and a per-turn `language` parameter.
- **Exotel audio constraints:** 16 kHz mono + `ExotelFrameSerializer` are fixed contracts ([transport_factory.py](../app/services/transport_factory.py), [ADR_001](decisions/ADR_001_SAMPLE_RATE_16000.md)); keep unless evidence forces change.
- **Interruption handling:** Barge-in is fully disabled today (`should_interrupt=False` + `AlwaysUserMuteStrategy` + `MuteUntilFirstBotCompleteUserMuteStrategy`, [pipecat_pipeline.py:71-136](../app/services/pipecat_pipeline.py#L71-L136)); re-enabling it for open-ended conversation risks false interruptions on noisy phone lines.
- **Dashboard truthfulness:** current LLM model/TTFB display is misleading; fixing it is required so V3 metrics are trustworthy.
- **State corruption:** `ACTIVE_SESSIONS`/`PENDING_OUTBOUND_CALLS` are process-global in-memory dicts (single-process prototype, [runtime.py:72-73](../app/runtime.py#L72-L73)); fine for one box, unsafe for multi-worker scale.
- **Fallback behavior:** multiple silent fallbacks (greeting timeout, profile-id ignored, validation rejection) currently hide failure; V3 must surface them.

---

## L. Questions For You (only design-blocking)

1. **Multilingual STT:** Deepgram **Flux is English-only**. For Hindi/Hinglish/code-switching, do you accept moving off Flux to a Deepgram multilingual model/pathway (with its own EOT behavior), or must we keep Flux's turn-taking? This changes the STT layer and EOT design materially.
2. **Greeting strategy:** For "bot speaks immediately on connect," do you prefer a **static configured greeting** (zero latency, zero fallback) or a **streamed LLM greeting** on connect? The current 2.0s pre-dial LLM greeting is the thing that keeps falling back.
3. **Barge-in:** Do you want true interruption (user can cut off the bot) in V3? It's currently disabled by design; enabling it affects mute strategy, STT `should_interrupt`, and TTS cancellation.
4. **Brain modes:** Do you want to retain the deterministic slot flow at all (Hybrid Option 3), or fully retire it and ship open-ended only? This decides whether `ConversationRuntime` and the typed flow/slot models stay.
5. **Cerebras `gpt-oss-120b` for live turns:** confirm this model/provider is the intended live conversational LLM (it's currently only used for greeting), so we size latency/rate-limit handling around it.

*(End of report.)*
