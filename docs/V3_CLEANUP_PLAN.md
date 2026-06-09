# Voicebot V3 — Cleanup & Rebuild Plan

> Planning document. Nothing here is implemented. All package/code claims were verified
> against the repo and the installed `.venv` Pipecat. Unverifiable items are marked **UNKNOWN**.
> Architecture evidence behind these decisions: [V3_REDESIGN_RESEARCH.md](V3_REDESIGN_RESEARCH.md).

---

## 1. Final V3 Decisions

V3 is a **pure open-ended, multilingual voice assistant**. Pipeline:

```
Exotel WebSocket → Pipecat transport → Deepgram Flux multilingual STT
  → committed user turn → Cerebras GPT-OSS LLM → Cartesia multilingual TTS → Exotel audio out
```

- **Open-ended only.** No slot-flow, no structured-form bot, no mixed-mode brain.
- **Hindi + English from day one.** Flux `model="flux-general-multi"`, `language_hints=["en","hi"]`.
- **Cerebras GPT-OSS** (`gpt-oss-120b`) is the live turn LLM.
- **Cartesia multilingual TTS** with per-turn language control.
- **LLM-generated greeting before dialing.** No static/canned greeting. If greeting generation fails, the call is not placed.
- **In-call rolling memory only** (last N turns via Pipecat `LLMContext`). No long-term/vector/caller memory, no post-call summary.
- **Single global runtime config in MongoDB**, frontend-editable, snapshotted per call. Only secrets in `.env`. MongoDB is required.
- **Frontend:** HTMX + Jinja, three post-call pages (Configuration, Calls List, Call Detail). No live dashboard. No React.
- **Primary metric:** `User Stop → First Audio Sent` (`first_audio_frame_sent_at − user_stopped_speaking_at`).
- **Phase 1 uses full LLM response then TTS.** Streaming, speculative/EagerEndOfTurn, and barge-in are later phases.
- **Exotel + ngrok + WebSocket path is stable** and must not be modified without evidence of breakage.
- **"Working" is only claimed after a real Exotel live call** to the test number — never from isolated/unit tests.

---

## 2. Cleanup Scope

Three things are removed or replaced:

1. **Dead documentation** — ~30 markdown files of V1/V2 phase history, old specs, and slot-flow docs.
2. **The slot-flow brain** — `ConversationRuntime`, `SimpleFlowRunner`, `SpeechComposer`, `RepeatedQuestionGuard`, the fake single-profile config repository, the slot-based conversation/persistence models, and the out-of-band `TTSSpeakFrame` response in `UserConversationProcessor`.
3. **Slot-flow tests** — every test that validates the above (see §10).

Recovery path for deleted code is **git history**, not in-repo archives. Do not leave unused slot-flow code in the active tree.

The transport/audio layer, `TurnCollector`, MongoDB persistence, settings/sample-rate, and the `ACTIVE_SESSIONS` JSON-only discipline are kept (see §3).

---

## 3. Files / Modules to Preserve

| File / Module | Note |
|---|---|
| [routers/telephony.py](../app/routers/telephony.py) | Exotel webhook + WebSocket bootstrap + finalize. Only the `run_pipeline(...)` call shape changes for V3. |
| [services/exotel_client.py](../app/services/exotel_client.py) | Outbound dial; no slot/LLM assumptions. |
| [services/transport_factory.py](../app/services/transport_factory.py) | Exotel serializer + 16 kHz transport (fixed contract). |
| [services/turn_collector.py](../app/services/turn_collector.py) | Commit/dedup/late-final logic; reused as the EOT/commit manager (add `language` passthrough). |
| [database.py](../app/database.py) | Motor singleton + `call_logs`; extend with one `runtime_config` collection and stricter startup (see §5). |
| [config.py](../app/config.py) | `Settings` env loader + `V1_SAMPLE_RATE`. Reduce to secrets/infra only (§5). |
| [logging_config.py](../app/logging_config.py) | Loguru setup; make level runtime-configurable. |
| [main.py](../main.py) | Lifespan + router include; add config-load + Mongo-required guardrails. |
| [runtime.py](../app/runtime.py) | Keep session/pending-context helpers and the **JSON-safe-only** rule (never store live Pipecat/provider objects). Drop slot conversation-state specifics; add language + latency fields. |
| `.env.example` | Config contract; edit to secrets-only, do not delete. |
| ADRs 001 (16 kHz), 002 (.env secrets), 004 (turn-based), 005 (single-call) | Still valid. |

---

## 4. Files / Modules to Delete or Rewrite

### Delete (active path)

| File / Module | Reason |
|---|---|
| [services/conversation_runtime.py](../app/services/conversation_runtime.py) | Slot/guard/compose orchestration. |
| [services/flow_runner.py](../app/services/flow_runner.py) | Regex slot NLU (`readiness→name→location→role→closing`). |
| [services/speech_composer.py](../app/services/speech_composer.py) | Canned templated speech. |
| [services/repeated_question_guard.py](../app/services/repeated_question_guard.py) | Slot-repetition guard. |
| `UserConversationProcessor._handle_committed_turn` push of a finished `TTSSpeakFrame` ([pipecat_pipeline.py:302-417](../app/services/pipecat_pipeline.py#L302-L417)) | Out-of-band brain; replaced by the in-pipeline LLM. |
| Flow/Slot/Node models in [models/config_models.py](../app/models/config_models.py#L82-L156) | Slot-flow only. |
| Slot/phase/action models in [models/conversation_models.py](../app/models/conversation_models.py) | Replaced by language state + turn record. |
| scratch/ dev scripts; tracked `__pycache__/`, `.pytest_cache/`, `*.egg-info/`, `logs/` | Not app code; add to `.gitignore`. |

### Rewrite

| File / Module | Target |
|---|---|
| [services/pipecat_pipeline.py](../app/services/pipecat_pipeline.py) | Keep the runner/greeting/disconnect shell ([L522-672](../app/services/pipecat_pipeline.py#L522-L672)). New processor list (§7): `transport.input() → stt → context_aggregator.user() → llm → tts → transport.output() → context_aggregator.assistant()`. STT becomes `flux-general-multi` with config-driven `language_hints`/EOT. Remove slot wiring and the `conversation_runtime`/`flow_runner` imports ([L57-66](../app/services/pipecat_pipeline.py#L57-L66)). |
| [services/conversation_controller.py](../app/services/conversation_controller.py) | Keep `_build_cerebras_llm` ([L70-77](../app/services/conversation_controller.py#L70-L77)) as the live LLM seed. Greeting generation stays (pre-dial, §6) but the timeout→fallback→dial-anyway pattern and `validate_greeting_text` readiness gate are removed. |
| [services/config_repository.py](../app/services/config_repository.py) | Replace with a loader for the **one global Mongo runtime config** (§5): no profiles, no `bot_profile_id`; seed-default-if-missing; build the per-call snapshot from it. |
| [utils/metrics_logger.py](../app/utils/metrics_logger.py) | Replace slot/latency summary with the primary metric + per-turn timestamps (§9). |
| [routers/dashboard.py](../app/routers/dashboard.py) | Split into the three honest pages (§8); persist greeting/LLM failure reasons; remove fake profile list and live sessions endpoint. |
| [models/schemas.py](../app/models/schemas.py) | Redesign `CallLog`/turn records around V3 turns, language, latency, and errors. Remove slot fields. |
| [app/templates/dashboard.html](../app/templates/dashboard.html) | Split into `config.html`, `calls_list.html`, `call_detail.html`; remove misleading per-turn LLM fields and the timeline/sessions panels. |

---

## 5. Runtime Config Design

**Secrets only in `.env`:** `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, `CEREBRAS_API_KEY`, `EXOTEL_*`, `MONGO_URI`, `MONGO_DB_NAME`, `PUBLIC_NGROK_URL`, optional app/session secret.

**One global `runtime_config` document in MongoDB** (frontend-editable, loaded before every call, copied into each call's snapshot):

```
STT:    model, language_hints, eot_threshold, eot_timeout_ms
LLM:    model, temperature, max_tokens, request timeout/guard behavior
TTS:    model, voice, language_mode
Prompt: system_prompt, greeting_prompt/instruction
Memory: last_turns_in_context, max_context_tokens, max_response_tokens
Ops:    debug_log_level
```

No tuning value lives in code or `.env`. `V1_SAMPLE_RATE = 16000` and serializer params stay as code constants (Exotel contract).

**MongoDB guardrails:**
- Mongo unavailable at startup → fail clearly (do not serve calls as if healthy).
- `runtime_config` missing → seed one default document.
- Config cannot load before a call → do not place the call; show a clear frontend error.
- Never fall back to `.env` for behavior config. (Logging-only degradation when Mongo is down may remain.)

---

## 6. LLM Greeting Design

The bot speaks first, using an LLM greeting generated **before dialing**:

1. Load global config from Mongo → build effective snapshot.
2. Generate greeting via Cerebras GPT-OSS.
3. On success: store greeting text in the pending-outbound context + snapshot, then trigger the Exotel call.
4. On WebSocket connect: greeting text already present → `task.queue_frame(TTSSpeakFrame(greeting_text))` → Cartesia speaks immediately.

**Failure handling:** greeting generation error/timeout is a **hard failure** → do not place the call → clear frontend error → persist the failure reason. Any client-side timeout exists only to avoid an infinite hang and must resolve to *no call*, never to *fallback greeting + call*. There is no static/canned greeting.

---

## 7. In-call Memory Design

Memory means: **within the same live call, the bot does not forget what was just said.** Nothing persists into prompts beyond the call.

Each LLM call includes only:

```
system prompt + language instruction + small call state + last 6–8 turns + current user message
```

- Rolling window via Pipecat `LLMContext` / `LLMContextAggregatorPair`. `last_turns_in_context` default 6–8; `max_context_tokens` and `max_response_tokens` are runtime-configurable.
- Small JSON-safe call state (current call only):
  ```json
  { "turn_count": 5, "last_user_language": "hi", "response_language": "hi",
    "explicit_language_override": null, "current_topic": "..." }
  ```
- Language fields drive switching/mirroring and explicit commands ("continue in English", "Hindi mein bolo", …).
- The **full transcript is persisted for the Call Detail page only**, never injected wholesale into the prompt.

**Verified aggregator placement** (Pipecat example [voicemail_detector.py:533-543](../.venv/Lib/site-packages/pipecat/extensions/voicemail/voicemail_detector.py#L533-L543)):

```
transport.input() → stt → context_aggregator.user() → llm → tts
    → transport.output() → context_aggregator.assistant()
```

The assistant aggregator sits **after** `transport.output()`, so stored assistant context reflects what was actually spoken (interruption-correct). This is also why, when streaming arrives in Phase 3, the assistant turn is committed to memory only after it is spoken.

---

## 8. Frontend Design

HTMX + Jinja, three pages, post-call only (no live tracking):

**Call Configuration** — view/edit the global runtime config (STT model, language hints, EOT threshold, EOT timeout; LLM model, temperature, max tokens, request timeout/guard; TTS model, voice, language mode; greeting prompt/instruction; system prompt; memory window size; debug log level). Includes the outbound trigger, which **generates the LLM greeting first and dials only on success** (§6).

**Calls List** — table: `call_id`, phone number, `started_at`, status, duration, turn count, avg primary latency, max primary latency, error yes/no.

**Call Detail** — effective config snapshot, LLM-generated greeting text, full transcript, per-turn table, latency breakdown, errors/failure reasons. Turn table columns:

```
Turn | User said | Bot said | Detected language | Response language |
User Stop → First Audio Sent | EOT delay | LLM TTFB | LLM total | TTS first audio | Error stage/message
```

---

## 9. Metrics Design

**Primary:** `primary_latency_ms = first_audio_frame_sent_at − user_stopped_speaking_at`, labelled **"User Stop → First Audio Sent"** (never "audible/heard/playback" — the server cannot know phone-speaker timing).

**Per-turn timestamps:** `turn_id`, `user_stopped_speaking_at`, `stt_eot_received_at`, `turn_committed_at`, `llm_response_started_at`, `llm_first_token_or_text_at`, `llm_completed_at`, `tts_request_started_at`, `first_audio_frame_sent_at`.

**Breakdown:** `eot_delay_ms`, `commit_delay_ms`, `llm_ttfb_ms`, `llm_total_ms`, `tts_first_audio_ms`.

**Capture points:**
- `user_stopped_speaking_at` → reuse `UserBotLatencyObserver` (`VADUserStoppedSpeakingFrame.timestamp − stop_secs`, [user_bot_latency_observer.py:245-250](../.venv/Lib/site-packages/pipecat/observers/user_bot_latency_observer.py#L245-L250)).
- `stt_eot_received_at` / `turn_committed_at` → Flux `on_end_of_turn` handler + `TurnCollector.observe` commit ([turn_collector.py:129-151](../app/services/turn_collector.py#L129-L151)).
- `llm_response_started_at` / `llm_first_token_or_text_at` / `llm_completed_at` → around the live LLM service (first `TextFrame` = TTFB; `LLMFullResponseEndFrame` = completed).
- `first_audio_frame_sent_at` → `BotStartedSpeakingFrame` is the accepted server-side proxy ([user_bot_latency_observer.py:278-296](../.venv/Lib/site-packages/pipecat/observers/user_bot_latency_observer.py#L278-L296)).

Per-turn debug fields only: `turn_id`, transcript, detected language, response language, LLM response text, latency breakdown, error stage/message. No frame-by-frame logging.

---

## 10. Test Cleanup

Classify **KEEP / DELETE / UNKNOWN** only. No rewrites now — V3 tests are written fresh when V3 coding starts. No test (live or not) proves production behavior; the real proof is a live Exotel call. Live tests stay skipped unless `RUN_LIVE_TESTS=1` / `-m live` ([conftest.py:5-40](../tests/conftest.py#L5-L40)).

| Test File | Action | Reason |
|---|---|---|
| [test_flow_runner.py](../tests/test_flow_runner.py) | DELETE | `SimpleFlowRunner` slot logic. |
| [test_speech_composer.py](../tests/test_speech_composer.py) | DELETE | `SpeechComposer` canned output. |
| [test_repeated_question_guard.py](../tests/test_repeated_question_guard.py) | DELETE | `RepeatedQuestionGuard`. |
| [test_conversation_runtime.py](../tests/test_conversation_runtime.py) | DELETE | Slot/readiness/guard orchestration. |
| [test_conversation_models.py](../tests/test_conversation_models.py) | DELETE | Slot/phase/action models. |
| [test_conversation_controller.py](../tests/test_conversation_controller.py) | DELETE | Greeting validation + legacy turn decision. |
| [test_config_repository.py](../tests/test_config_repository.py) | DELETE | In-memory default profile + slot flow. |
| [test_dashboard.py](../tests/test_dashboard.py) | DELETE | Old dashboard fragments + v2 snapshot fields. |
| [test_pipeline.py](../tests/test_pipeline.py) | DELETE | Asserts current processor list + `ConversationRuntime` wiring. |
| [test_logging.py](../tests/test_logging.py) | DELETE | Slot conversation summary + old latency model. |
| [test_models.py](../tests/test_models.py) | DELETE | Old `CallLog`/metrics shapes. |
| [test_outbound.py](../tests/test_outbound.py) | UNKNOWN | Webhook test reusable; profile/greeting-fallback assertions are not. |
| [test_runtime.py](../tests/test_runtime.py) | UNKNOWN | Pending-outbound + JSON-safe helpers reusable; slot snapshot test not. |
| [test_live_stt.py](../tests/test_live_stt.py) | UNKNOWN | Live Deepgram but non-Flux service; update for `flux-general-multi`. |
| [test_live_tts.py](../tests/test_live_tts.py) | UNKNOWN | Live Cartesia via `_build_tts`, which changes for language support. |
| [test_live_llm.py](../tests/test_live_llm.py) | UNKNOWN | Imports `_build_cerebras_llm`, which is being rewritten. |
| [test_config.py](../tests/test_config.py) | KEEP | `Settings` + `V1_SAMPLE_RATE`; V3-neutral. |
| [test_transport.py](../tests/test_transport.py) | KEEP | Exotel serializer wiring. |
| [test_turn_collector.py](../tests/test_turn_collector.py) | KEEP | Commit/dedup logic reused. |
| [conftest.py](../tests/conftest.py) | KEEP | Live-gating + env fixtures. |

**Counts:** DELETE 11, KEEP 4, UNKNOWN 5.

---

## 11. Phase 0 Cleanup Execution Order

1. **Branch + tag.** Create `v3-cleanup` off `main`; commit/stash the current working-tree changes; tag the pre-cleanup state for recovery.
2. **Delete dead docs** — V1/V2 phase docs, old specs, slot-flow design doc, V2 "current" docs; keep ADRs 001/002/004/005 and the V3 research/cleanup docs. One commit.
3. **Delete slot-flow tests** (the 11 DELETE rows). One commit; suite stays green minus those.
4. **Delete slot-flow modules** (`conversation_runtime`, `flow_runner`, `speech_composer`, `repeated_question_guard`) **and remove their imports** in [pipecat_pipeline.py:57-66](../app/services/pipecat_pipeline.py#L57-L66) in the same commit so the app still imports.
5. **Drop slot models** from `config_models.py` / `conversation_models.py` and any remaining references.
6. **Add `.gitignore`** for `__pycache__/`, `.pytest_cache/`, `*.egg-info/`, `logs/`, `scratch/`; remove them from tracking.
7. **Verify after each step:** app imports (`python -c "import main"`), and the reduced test set passes.

Do not delete: `.env.example`, `database.py`/schemas, Exotel/ngrok/webhook code, `transport_factory.py`, `turn_collector.py`. Get explicit sign-off on the concrete delete list before removing code modules.

---

## 12. Remaining True Unknowns

Need real code/package or live verification (not answerable from the plan):

1. **Hindi voice quality** of the configured Cartesia voice + `sonic-3.5`. The API accepts `language="hi"` (`Language.HI` mapped, [cartesia/tts.py:91](../.venv/Lib/site-packages/pipecat/services/cartesia/tts.py#L91)) and per-turn switching works via `TTSUpdateSettingsFrame(delta=TTSSettings(language=...))` ([tts_service.py:747-763](../.venv/Lib/site-packages/pipecat/services/tts_service.py#L747-L763)) — but whether this specific voice sounds natural in Hindi is **UNKNOWN** until a live test.
2. **Flux Hinglish / code-switch accuracy** on real 16 kHz phone audio. Mechanism verified ([flux/stt.py:52-56](../.venv/Lib/site-packages/pipecat/services/deepgram/flux/stt.py#L52-L56)); accuracy needs live calls.
3. **Cerebras GPT-OSS live per-turn latency and rate limits** under real call load (today it serves only a single greeting call).
4. **Frame-accurate "bytes on the wire"** earlier than `BotStartedSpeakingFrame` — whether the Exotel transport output exposes such a hook is **UNKNOWN**; not needed for Phase 1/2.
