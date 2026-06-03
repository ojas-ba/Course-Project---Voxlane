# Voicebot V3 — Phase 1 Implementation Plan

> Plan only. No code written. Baseline = `main` after Phase 0 merge (`232cce4`).
> All package facts verified against installed Pipecat 1.2.1 in `.venv`. Unverified items: **UNKNOWN**.
> Decisions of record: [V3_CLEANUP_PLAN.md](V3_CLEANUP_PLAN.md). Architecture evidence: [V3_REDESIGN_RESEARCH.md](V3_REDESIGN_RESEARCH.md).

---

## 1. Current Codebase Findings

**App structure** (`app/`): `config.py` (env `Settings` + `V1_SAMPLE_RATE`), `database.py` (Motor singleton, `call_logs`), `runtime.py` (`ACTIVE_SESSIONS`/`PENDING_OUTBOUND_CALLS` JSON-only registries + helpers), `routers/{telephony,dashboard}.py`, `services/{pipecat_pipeline,conversation_controller,config_repository,turn_collector,transport_factory,exotel_client}.py`, `utils/metrics_logger.py`, `models/{config_models,conversation_models,schemas}.py`, `templates/dashboard.html`.

**Pipeline shell** ([pipecat_pipeline.py](../app/services/pipecat_pipeline.py)): `run_pipeline()` (L≈170) builds `_build_pipeline_processors()` → `[transport.input(), stt, aggregator_pair.user(), tts, aggregator_pair.assistant(), transport.output()]`. `_build_stt` hardcodes `flux-general-en`, `eot_threshold=0.65`, `eot_timeout_ms=1000`. `_build_tts` reads `snapshot["TTS_VOICE_ID"/"TTS_MODEL"]`, SENTENCE aggregation, **no language**. `_build_context`/`_build_aggregator_pair` create the `LLMContext` + `LLMContextAggregatorPair` (user mute strategies + Silero VAD + `ExternalUserTurnStrategies`). **No LLM in the list.** Greeting queued in `run_pipeline` via `task.queue_frame(TTSSpeakFrame(...))`; three latency observer callbacks wired.

**Greeting** ([conversation_controller.py](../app/services/conversation_controller.py)): `generate_outbound_greeting_text()` runs an isolated `[_ContextSource, CerebrasLLMService, _TextCollector]` pipeline; `_build_cerebras_llm()` exists; `validate_greeting_text()` enforces a readiness gate (to be removed).

**Config/runtime**: [config_repository.py](../app/services/config_repository.py) is in-memory, rebuilds one hardcoded `default_demo_bot` with a slot flow, **ignores `bot_profile_id`**. [runtime.py](../app/runtime.py) `build_config_snapshot()` wraps it. Session = `{call_sid, stream_sid, metrics{}, transcript[], first_latency, config_snapshot, conversation_state?}`.

**DB** ([database.py](../app/database.py)): `init_database()` pings Mongo; `main.py` lifespan currently **tolerates Mongo being down** (warns, continues). One collection `call_logs`.

**Greeting/dial path** ([dashboard.py](../app/routers/dashboard.py) `post_outbound`, L110): builds snapshot → tries greeting under `asyncio.timeout(2.0)` → **on timeout/exception sets source=fallback and dials anyway** → `create_pending_outbound_context` → `client.make_call`. This is the broken pattern to remove.

**Telephony** ([telephony.py](../app/routers/telephony.py)): webhook returns wss URL; `exotel_websocket` resolves ids, `create_session`, `build_exotel_transport`, `run_pipeline(...)`, then `finalize_call_log` + `persist_call_log`. **Stable — do not touch.**

**Remaining tests (15):** keep-infra (`test_config`, `test_transport`, `test_turn_collector`, `conftest`); transition tests still green on still-present modules (`test_conversation_models`, `test_config_repository`, `test_dashboard`, `test_logging`, `test_models`, `test_conversation_controller`, `test_outbound`, `test_runtime`); live (`test_live_llm/stt/tts`).

**Verified package facts (Pipecat 1.2.1):**
- Flux multilingual: `DeepgramFluxSTTSettings(model="flux-general-multi", language_hints=[Language.EN, Language.HI], eot_threshold=…, eot_timeout_ms=…)`; `language_hints` is `list[Language]` and **only honored on `flux-general-multi`** ([flux/base.py:112-135,270-277](../.venv/Lib/site-packages/pipecat/services/deepgram/flux/base.py#L112-L135)).
- On EndOfTurn, Flux pushes a **finalized `TranscriptionFrame`** (with detected `language`) **+ `UserStoppedSpeakingFrame`** and fires `on_end_of_turn` ([flux/base.py:676-694](../.venv/Lib/site-packages/pipecat/services/deepgram/flux/base.py#L676-L694)). → the native user-aggregator→LLM→TTS chain is triggered automatically.
- `CerebrasLLMService(OpenAILLMService)` with `Settings(model, temperature, max_tokens, …)` ([cerebras/llm.py:19-68](../.venv/Lib/site-packages/pipecat/services/cerebras/llm.py#L19-L68)) — consumes `LLMContextFrame`, emits `TextFrame`/`LLMFullResponseEndFrame`.
- Cartesia `language` supported incl. `hi`; switchable mid-call via `TTSUpdateSettingsFrame(delta=TTSSettings(language=...))` ([tts_service.py:747-763](../.venv/Lib/site-packages/pipecat/services/tts_service.py#L747-L763)).
- `LLMContext` exposes `messages`, `get_messages()`, `set_messages()`, `add_message()` ([llm_context.py:220-394](../.venv/Lib/site-packages/pipecat/processors/aggregators/llm_context.py#L220-L394)) → bounded-memory trimming is feasible.
- Canonical placement (Pipecat example): `…user() → llm → tts → transport.output() → …assistant()` ([voicemail_detector.py:533-543](../.venv/Lib/site-packages/pipecat/extensions/voicemail/voicemail_detector.py#L533-L543)).

---

## 2. Phase 1 Target Architecture

**Outbound config/greeting flow:**
```
Config page → POST /calls/outbound
  → load global RuntimeConfig from Mongo (fail → 503-style error, no dial)
  → build effective config snapshot
  → generate greeting via Cerebras GPT-OSS (hard timeout = no call on failure)
  → success: store greeting_text + snapshot in PENDING_OUTBOUND_CALLS
  → ExotelOutboundClient.make_call  (unchanged)
  → failure: render error fragment; DO NOT dial; persist failure reason
```

**WebSocket pipeline flow** (per call):
```
transport.input()
  → Deepgram Flux STT (flux-general-multi, language_hints from config)
  → context_aggregator.user()
  → MemoryProcessor        (rebuild system block from CallMemory; trim to N pairs; emit per-turn language + TTSUpdateSettingsFrame)
  → Cerebras GPT-OSS LLM
  → Cartesia TTS (language per turn)
  → MetricsProcessor       (timestamps: TTS start, BotStartedSpeaking=first_audio_sent)
  → transport.output()
  → context_aggregator.assistant()
Side channels (no response generation):
  stt.on_end_of_turn → TurnCollector.commit + language detect + per-turn metrics record open
  greeting: task.queue_frame(TTSSpeakFrame(greeting_text)) on connect  (unchanged mechanism)
```

**Turn/memory/metrics flow:** Flux EOT → finalized TranscriptionFrame + UserStoppedSpeaking → (a) user aggregator flushes context → MemoryProcessor governs context → LLM → TTS → first audio; (b) side channel records `user_text`, `detected_language`, opens a turn-metric record keyed by `turn_id` (= `TurnCollector` index), closed at `BotStartedSpeakingFrame`. Running summary updated **after** the reply, in a background task.

**Frontend flow:** `/config` (view/edit RuntimeConfig + dial) · `/calls` (list) · `/calls/{call_sid}` (detail: snapshot, greeting, transcript, turn table, latency, errors).

---

## 3. Implementation Slices

### Slice 1 — Runtime config foundation
- **Goal:** one global Mongo-backed `RuntimeConfig`; Mongo required; seed-default; per-call snapshot from it.
- **Files:** CREATE `app/models/runtime_config_models.py`, `app/services/runtime_config.py`; MODIFY `database.py` (collection + accessor), `main.py` (Mongo-required startup + seed), `runtime.py` (`build_config_snapshot` → from RuntimeConfig), `config.py` (reduce to secrets/infra).
- **Changes:** `RuntimeConfig` Pydantic (see §5); `load_runtime_config()` (read `runtime_config` doc `_id="global"`, seed default if missing), `save_runtime_config()`, `effective_snapshot(config)`. Startup: ping Mongo, raise on failure (no serving); ensure default seeded.
- **Tests:** CREATE `tests/test_runtime_config.py` (seed-default, load/save round-trip, snapshot shape, missing-config seeds). UPDATE/replace `test_config_repository.py`.
- **Risks:** other code imports `build_config_snapshot`/`config_repository` symbols → update call sites. **Rollback:** revert slice commit; `config_repository` still present until cut over.
- **DoD:** app starts only with Mongo up; default config seeded; `import main` OK; new tests pass.

### Slice 2 — LLM pre-dial greeting (no fallback)
- **Goal:** greeting generated before dial; failure = no call.
- **Files:** MODIFY `conversation_controller.py` (drop `validate_greeting_text` readiness gate; keep generation; raise on empty/error), `dashboard.py` (`post_outbound`: on greeting success store + dial; on failure render error, **no dial**, persist reason), `runtime.py` (`PendingOutboundContext` already stores greeting fields), `schemas.py` (persist `greeting_error`/`greeting_source`).
- **Changes:** remove the `except TimeoutError/Exception → source=fallback → dial` branch; timeout still wraps generation but resolves to **error → no dial**. `greeting_text_source` becomes `llm` or the call is not placed.
- **Tests:** UPDATE `test_outbound.py`/`test_conversation_controller.py`: greeting success → pending context + dial; greeting failure → no `make_call`, error shown, reason persisted.
- **Risks:** ensure `make_call` is not reached on failure path. **Rollback:** revert slice.
- **DoD:** failure path proven (mock LLM raise) to skip `make_call`; success path dials with greeting stored.

### Slice 3 — Real Pipecat LLM pipeline
- **Goal:** insert Cerebras LLM; multilingual STT from config; keep greeting/observer/`run_pipeline` signature.
- **Files:** MODIFY `pipecat_pipeline.py` (`_build_stt` reads config: `flux-general-multi` + `language_hints` + eot from snapshot; ADD `_build_llm(snapshot)`; `_build_pipeline_processors` → `[input, stt, user(), MemoryProcessor, llm, tts, MetricsProcessor, output(), assistant()]`; attach `stt.on_end_of_turn` side channel). CREATE memory/metrics/language processors (Slices 4-6) — Slice 3 lands the LLM with a pass-through MemoryProcessor/MetricsProcessor stub.
- **Changes:** LLM triggered natively by Flux finalized TranscriptionFrame + UserStoppedSpeaking. Remove no Exotel code. `run_pipeline` signature unchanged → `telephony.py` untouched.
- **Tests:** CREATE `test_pipeline_assembly.py` (processor order; LLM present; STT model/hints/eot from snapshot; greeting still queued). `test_live_*` updated for new build fns.
- **Risks (highest):** native turn→LLM triggering with `ExternalUserTurnStrategies`+Flux; mute strategy currently `AlwaysUserMuteStrategy` (keeps non-interruptible — fine for P1). **Rollback:** revert to shell.
- **DoD:** assembly unit test passes; `import main` OK; **live call: English turn → spoken reply** (real proof).

### Slice 4 — Language state + Cartesia language control
- **Goal:** detect user language, mirror it, honor explicit overrides, switch Cartesia per turn.
- **Files:** CREATE `app/services/language.py` (read `TranscriptionFrame.language`; `parse_language_override(text)`; `resolve_response_language(state)`). MODIFY `MemoryProcessor` to inject language instruction + push `TTSUpdateSettingsFrame`; `runtime.py` session `language_state`.
- **Changes:** override regex set (en/hi commands listed in §6/prompt); `response_language = override or last_user_language or default`.
- **Tests:** CREATE `test_language.py` (override parsing incl. "Hindi mein bolo"/"continue in English"; resolution precedence; default fallback).
- **Risks:** Flux `language` field population on real audio (**UNKNOWN** accuracy); Cartesia voice Hindi quality (**UNKNOWN**). **Rollback:** revert; pipeline still English-capable.
- **DoD:** unit tests pass; **live: Hinglish turn answered in kind; "continue in English" switches.**

### Slice 5 — Bounded in-call memory
- **Goal:** stable context for 20-30 turns without latency spikes.
- **Files:** CREATE `app/services/memory.py` (`CallMemory` dataclass/TypedDict, `MemoryManager`, rule-based `extract_pinned_facts`, summary scheduler). MODIFY `MemoryProcessor` to call it; `runtime.py` session `memory`.
- **Changes:** each turn, MemoryProcessor rebuilds `context.set_messages([system_block] + last 2*recent_turn_pairs msgs)`; `system_block` = base prompt + language instruction + pinned facts + running summary. Pinned extraction = deterministic rules. Summary = background `asyncio` task after turn ≥ threshold, every K turns; previous summary used until ready.
- **Tests:** CREATE `test_memory.py` (trim keeps ≤N pairs; pinned cap; pinned rules accept/reject examples from prompt; summary scheduling marker; JSON-safe).
- **Risks:** trimming vs aggregator append ordering; summary task must never block hot path. **Rollback:** revert to recent-window-only.
- **DoD:** unit tests pass; **live: 25-turn call, bot recalls earlier facts, no latency spike at summary turns.**

### Slice 6 — Metrics/log persistence
- **Goal:** per-turn timestamps + primary latency persisted; truthful fields.
- **Files:** CREATE `app/services/call_metrics.py` (`MetricsProcessor` + `TurnRecord` builder). MODIFY `metrics_logger.py`/`schemas.py` (V3 `CallLog`/`TurnRecord`, drop slot summary), `runtime.py` (append turn records), `telephony.py` finalize call stays (no signature change).
- **Changes:** capture timestamps (§6); `primary_latency_ms = first_audio_frame_sent_at − user_stopped_speaking_at`; `first_audio_frame_sent_at` = `BotStartedSpeakingFrame`.
- **Tests:** UPDATE `test_logging.py`/`test_models.py` for V3 shapes; CREATE `test_metrics_capture.py` (record assembly from synthetic frame timestamps).
- **Risks:** exact `user_stopped_speaking_at` source (Flux `UserStoppedSpeakingFrame` vs VAD) — see §10. **Rollback:** revert; keep old metrics.
- **DoD:** unit tests pass; **live: Call Detail shows per-turn latency breakdown.**

### Slice 7 — Minimal frontend (3 pages)
- **Goal:** Config / Calls List / Call Detail; outbound = greeting-first.
- **Files:** MODIFY `dashboard.py` (routes `/config`, `/calls`, `/calls/{sid}`, `POST /calls/outbound`, `POST /config`); CREATE `templates/{config,calls_list,call_detail}.html` (+ shared base); retire `templates/dashboard.html` sessions/dialer/timeline parts.
- **Changes:** Config form binds RuntimeConfig; Calls List from `call_logs`; Call Detail renders snapshot + greeting + transcript + turn table + errors. Remove `/dashboard/sessions` live panel.
- **Tests:** UPDATE/replace `test_dashboard.py` (render config/list/detail; outbound greeting-first; config save).
- **Risks:** template churn only; no pipeline risk. **Rollback:** revert templates/routes.
- **DoD:** pages render with seeded/mock data; outbound triggers greeting before dial.

### Slice 8 — Live Exotel validation
- **Goal:** end-to-end proof (see §9). No code unless a defect is found.
- **DoD:** §9 checklist passes on a real call to the test number.

---

## 4. File-by-file Change Plan

| File | Action | Reason / What changes | Must NOT change |
|------|--------|-----------------------|-----------------|
| `app/routers/telephony.py` | **KEEP** | `run_pipeline` signature preserved; no edits expected | Exotel webhook/WS/serializer/finalize |
| `app/services/transport_factory.py` | **KEEP** | 16 kHz Exotel transport contract | everything |
| `app/services/exotel_client.py` | **KEEP** | outbound dial works | everything |
| `app/services/turn_collector.py` | **KEEP** | reused as commit/dedup side channel | commit/dedup logic |
| `app/services/pipecat_pipeline.py` | **MODIFY** | config-driven multilingual STT; add `_build_llm`; new processor list with Memory/Metrics processors; LLM inserted | `run_pipeline` signature; greeting queue; observer wiring |
| `app/services/conversation_controller.py` | **MODIFY** | keep generation + `_build_cerebras_llm`; remove `validate_greeting_text` gate + fallback semantics | provider call mechanics |
| `app/services/config_repository.py` | **MODIFY→retire** | replaced by `runtime_config.py`; remove slot/default-profile builder | — |
| `app/services/runtime_config.py` | **CREATE** | load/seed/save global RuntimeConfig; effective snapshot | — |
| `app/services/memory.py` | **CREATE** | CallMemory + MemoryManager + pinned rules + summary scheduler | — |
| `app/services/language.py` | **CREATE** | detection read + override parse + resolution | — |
| `app/services/call_metrics.py` | **CREATE** | MetricsProcessor + TurnRecord | — |
| `app/database.py` | **MODIFY** | add `runtime_config` collection accessor; keep `call_logs`; (optional) index on `call_sid` | client singleton pattern |
| `app/runtime.py` | **MODIFY** | session gains `memory`, `language_state`, `turn_records`; `build_config_snapshot` from RuntimeConfig; keep JSON-only discipline | `ACTIVE_SESSIONS` no-live-objects rule |
| `app/models/config_models.py` | **MODIFY** | add nothing slot-related; **remove inert Flow/Slot/Node models** (Phase 1 cut) | — |
| `app/models/runtime_config_models.py` | **CREATE** | `RuntimeConfig` + sub-models | — |
| `app/models/conversation_models.py` | **MODIFY** | keep `TurnEvent`/`TurnInput`/`TurnCollectionResult` (used by TurnCollector); **remove inert slot models** (`SlotState`,`ConversationState`,`RuntimeAction`,`RuntimeResult`,`normalize_conversation_phase`) | turn types used by TurnCollector |
| `app/models/schemas.py` | **MODIFY** | V3 `CallLog` + `TurnRecord`; drop slot fields/summary | — |
| `app/utils/metrics_logger.py` | **MODIFY** | rewrite around TurnRecord + primary latency; drop slot conversation summary | — |
| `app/routers/dashboard.py` | **MODIFY** | 3-page routes; greeting-first outbound; remove sessions/fake-profile | — |
| `app/templates/dashboard.html` | **DELETE** (split) | replaced by `config/calls_list/call_detail.html` | — |
| `app/templates/{config,calls_list,call_detail}.html` | **CREATE** | minimal HTMX/Jinja pages | — |
| `main.py` | **MODIFY** | Mongo-required startup + seed default config | router include order |
| `.env.example` | **MODIFY** | secrets/infra only; remove tuning keys (now in Mongo) | secret keys present |
| `tests/*` | **MODIFY/CREATE/DELETE** | see §8 | keep-infra tests |

---

## 5. Data Models and Mongo Collections

**Collection `runtime_config`** — single doc `_id="global"`:
```jsonc
{ "_id": "global", "version": 1, "updated_at": "<iso>",
  "stt": {"model":"flux-general-multi","language_hints":["en","hi"],"eot_threshold":0.65,"eot_timeout_ms":700},
  "llm": {"provider":"cerebras","model":"gpt-oss-120b","temperature":0.7,"max_tokens":160,"request_timeout_seconds":10},
  "tts": {"provider":"cartesia","model":"sonic-3.5","voice_id":"...","language_mode":"per_turn"},
  "prompt": {"system_prompt":"...","greeting_prompt":"..."},
  "memory": {"recent_turn_pairs":6,"enable_running_summary_after_turn":10,"summary_update_every_turns":5,
             "max_summary_tokens":250,"max_pinned_facts":12,"max_recall_snippets":2,"recall_only_when_user_asks":true},
  "ops": {"debug_log_level":"INFO"} }
```
Pydantic `RuntimeConfig` mirrors this (sub-models `STTCfg/LLMCfg/TTSCfg/PromptCfg/MemoryCfg/OpsCfg`); `language_hints` stored as strings, mapped to `Language` enum at build time.

**Effective config snapshot** (per call, immutable; stored in session + `call_logs`): the resolved `RuntimeConfig` doc at dial time + `voice/model/system_prompt` flattened for `_build_stt/_build_tts` compatibility.

**`PendingOutboundContext`** (in-memory, unchanged shape): `{call_sid, to_number, config_snapshot, opening_state, greeting_text, greeting_error, greeting_metrics, created_at}`.

**`memory` (session, JSON-safe):**
```jsonc
{ "pinned_facts": ["..."], "running_summary": null, "summary_last_updated_turn": 0,
  "language_state": {"last_user_language":"en","response_language":"en","explicit_language_override":null} }
```

**`TurnRecord`** (per turn, persisted in `call_logs.turns`):
```jsonc
{ "turn_id":1,"user_text":"...","assistant_text":"...","detected_language":"hi","response_language":"hi",
  "user_stopped_speaking_at":..., "stt_eot_received_at":..., "turn_committed_at":...,
  "llm_request_started_at":..., "llm_first_token_or_text_at":..., "llm_completed_at":...,
  "tts_request_started_at":..., "first_audio_frame_sent_at":...,
  "primary_latency_ms":..., "eot_delay_ms":..., "commit_delay_ms":..., "llm_ttfb_ms":..., "llm_total_ms":..., "tts_first_audio_ms":...,
  "error_stage":null, "error_message":null }
```

**`call_logs`** (V3): `{call_sid, stream_sid, started_at, status, duration_secs, config_snapshot, greeting_text, greeting_error, transcript[], turns[TurnRecord], turn_count, avg_primary_latency_ms, max_primary_latency_ms, error}`.

---

## 6. Memory Plan (bounded, 20-30 turns)

- **Recent window:** keep last `recent_turn_pairs` (default 6) user/assistant pairs in `LLMContext` by `set_messages([system_block] + last 12 msgs)` each turn (verified `LLMContext.set_messages`).
- **Pinned facts:** deterministic rule-based extraction (Phase 1) — capture durable instructions/decisions (e.g. patterns like "I want…", "don't…", "use … not …", name/identity statements, explicit preferences); reject filler ("haan", "ok", "wait", < N tokens). Cap `max_pinned_facts` (12), dedupe. Stored in session; injected into `system_block`.
- **Running summary:** only after `enable_running_summary_after_turn` (10), refreshed every `summary_update_every_turns` (5). Generated by a **background `asyncio` task** (separate short Cerebras call) summarizing turns older than the recent window into ≤`max_summary_tokens` (250). Hot path uses the **previous** summary until the new one lands.
- **Recall snippets:** only when the user explicitly references earlier context (`recall_only_when_user_asks=true`); inject ≤`max_recall_snippets` (2) transcript snippets. Phase 1 trigger = simple keyword/reference heuristic; no vector DB.
- **Prompt per turn:** `system_prompt + language_instruction + small_call_state + pinned_facts + running_summary(if enabled) + ≤2 recall_snippets(if triggered) + last 6 pairs + current user message`.
- **Full transcript:** persisted to `call_logs` for Call Detail/debug only — **never** injected wholesale.

---

## 7. Latency Protection Plan

- **No summarization before reply** — summary runs after the reply, in a background task; previous summary used meanwhile.
- **Bounded prompt** — system block + ≤6 pairs + current message; older turns trimmed each turn; pinned/summary are size-capped.
- **`max_tokens`** from config (160 default) bounds generation time.
- **No DB in hot path** — RuntimeConfig is loaded **before** the call and snapshotted; turn-record persistence happens at finalize, not mid-turn.
- **Metric** — `BotStartedSpeakingFrame` = `first_audio_frame_sent_at` proxy; `primary_latency_ms` measured every turn (labelled "User Stop → First Audio Sent").
- **Full-response-then-TTS accepted for Phase 1** but measured; streaming deferred to Phase 2 if numbers demand it. Config `eot_timeout_ms=700` (down from 1000) trims EOT wait; tune in Phase 4.

---

## 8. Test Plan

- **KEEP:** `test_config.py`, `test_transport.py`, `test_turn_collector.py`, `conftest.py`.
- **DELETE (with their module rewrites):** `test_conversation_models.py` (slot models removed), `test_config_repository.py` (replaced by runtime_config), and the inert slot-model tests; retire `test_pipeline`-style slot assertions (already gone).
- **MODIFY:** `test_outbound.py` (greeting-first, no-dial-on-failure), `test_conversation_controller.py` (no fallback/no readiness gate), `test_logging.py` + `test_models.py` (V3 `CallLog`/`TurnRecord`), `test_dashboard.py` (3 pages), `test_live_stt/tts/llm.py` (new build fns + multilingual STT).
- **CREATE:** `test_runtime_config.py`, `test_language.py`, `test_memory.py`, `test_metrics_capture.py`, `test_pipeline_assembly.py`, `test_greeting_no_fallback.py`.
- **Policy:** unit + assembly tests are necessary but **not** sufficient — §9 live call is the real proof. Live tests stay `-m live`/`RUN_LIVE_TESTS=1` gated.

---

## 9. Live Exotel Validation Plan (manual checklist)

1. Call connects (Exotel WS bound; pipeline starts).
2. **LLM greeting speaks first** (no "hello" needed; greeting matches `greeting_prompt`).
3. English turn → coherent open-ended English reply.
4. Hindi/Hinglish turn ("haan mai abhi ghar ja raha hu") → understood, replied in kind.
5. Language switch ("continue in English") → next reply in English; ("Hindi mein bolo") → back to Hindi.
6. Memory: across 20-30 turns / 3-4 min, bot recalls an earlier-stated fact; no latency spike on summary turns.
7. Latency metrics captured per turn; "User Stop → First Audio Sent" present.
8. Call Detail page shows config snapshot, greeting, transcript, turn table, latency.
9. **Failure path:** force greeting-generation failure → **no call placed**, frontend error, reason persisted.

---

## 10. Risks and Unknowns

- **Cartesia Hindi voice quality** with the selected `voice_id` — API accepts `hi`, switching verified; *naturalness* **UNKNOWN** until live.
- **Flux Hinglish/code-switch accuracy** + reliable `TranscriptionFrame.language` on real 16 kHz phone audio — **UNKNOWN**; validate live.
- **Cerebras GPT-OSS live per-turn latency / rate limits** under real call load (today one greeting call only) — **UNKNOWN**.
- **`user_stopped_speaking_at` source** — Flux broadcasts `UserStoppedSpeakingFrame` (non-VAD); `UserBotLatencyObserver` keys on `VADUserStoppedSpeakingFrame`. Phase 1 will timestamp from Flux's `UserStoppedSpeakingFrame`/`on_end_of_turn` directly in `MetricsProcessor`; whether a VAD stop frame also fires here is **UNKNOWN** — confirm in Slice 6.
- **Byte-on-wire timing before `BotStartedSpeakingFrame`** — not exposed by the Exotel transport as far as verified; **UNKNOWN**, not needed for P1 (proxy accepted).
- **Native turn→LLM triggering** with `ExternalUserTurnStrategies` + Flux + current mute strategy — high-confidence from frame evidence but **must be proven by the first live reply** (Slice 3).

---

## 11. Exact Questions for Human Approval (blocking)

1. **Cartesia Hindi voice:** keep the current `.env` `TTS_VOICE_ID` (`8262a63b-…`) for both English and Hindi, or provide/confirm a Hindi-capable Cartesia voice? (Affects voice quality in Slice 4; mechanism works either way.)
2. **Default greeting language:** the greeting is pre-dial (no user input yet) — should it default to English, or be driven entirely by `greeting_prompt` wording? (Sets the first-utterance language.)
3. **Phase 1 may now delete the 6 retained transition tests + inert slot model classes** (deferred in Phase 0) as their modules are rewritten — confirm this is approved, since you protected them during Phase 0.

(Already-decided items not re-asked: LLM greeting, open-ended only, Mongo required, HTMX/Jinja, no long-term memory, no streaming/EOT/barge-in in P1.)

---

## 12. Final Implementation Order (recommended commits)

1. `feat(config): global Mongo runtime config + Mongo-required startup + default seed` (Slice 1)
2. `feat(greeting): LLM pre-dial greeting; remove fallback/dial-anyway` (Slice 2)
3. `feat(pipeline): insert Cerebras LLM + multilingual Flux STT (pass-through Memory/Metrics stubs)` (Slice 3) → **live smoke: English reply**
4. `feat(lang): per-call language state + Cartesia per-turn language` (Slice 4)
5. `feat(memory): bounded in-call memory (recent window + pinned + background summary)` (Slice 5)
6. `feat(metrics): per-turn timestamps + primary latency persistence` (Slice 6)
7. `feat(frontend): config/calls-list/call-detail pages; greeting-first outbound` (Slice 7)
8. `chore(cleanup): remove inert slot models + retired transition tests` (folded into 1/3/6 where each module is rewritten)
9. **Live Exotel validation** (Slice 8) — fixes only if defects found.

Each commit: app imports, targeted tests green, Exotel/transport untouched (verified via `git diff` against the Exotel files), no slot-flow reintroduced.
