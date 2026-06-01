# NOTES.md - Working Notes

## Purpose

Use this file for durable working memory across implementation sessions.

## Open Questions

- ~~Confirm the final persistence target shape once schema work begins.~~ — Resolved in Phase 3: `CallLog`, `CallMetrics`, `TurnMetrics`, `LatencyEvent`.

## Decisions

- The project is voice-agent-specific, not a generic scaffold.
- V2 Phase 2 TurnCollector gates assistant responses on committed Deepgram Flux EndOfTurn events. Raw finalized `TranscriptionFrame` text may buffer transcript data but must not directly trigger `decide_next_turn()`.
- V2 Phase 2 uses a temporary bridge from committed `TurnInput` to the existing `conversation_controller`; `ConversationRuntime`, `FlowRunner`, `LLMAdapter`, and `SpeechComposer` remain later-phase work.
- Manual Exotel and voice validation remains human-owned.
- AI-owned verification is limited to normal code-structure and flow validation.
- Full phase documents are created up front so implementation can proceed without redefining process docs later.
- v1 transport and pipeline are hardcoded to 16000 Hz.
- 16000 Hz is fixed and non-configurable in v1 unless the human explicitly changes the requirement.
- Active calls use an immutable `config_snapshot`. Dashboard configuration changes apply only to future calls.
- Observer registration must use `PipelineTask(..., observers=[latency_observer])`.
- `SileroVADAnalyzer()` remains required for latency timing even with no barge-in.
- `SileroVADAnalyzer()` is passed inside `LLMUserAggregatorParams`, not as a separate pipeline stage.
- `app/runtime.py` owns the `ACTIVE_SESSIONS` runtime registry keyed by `call_sid`.
- The Exotel WebSocket route for v1 is fixed as `WS /telephony/exotel`.
- Latency events must be normalized into JSON-friendly dictionaries before persistence.
- Latency event persistence shape is fixed to `{"time": float, "event": str}` dictionaries.
- Latency event dictionaries use the exact shape `{"time": float, "event": str}`.
- Graceful teardown order is cancel task, let runner unwind, then finalize call log.
- WebSocket teardown strictly follows: catch disconnect -> cancel task -> unwind pipeline -> finalize call log -> remove session from runtime registry.
- `app/runtime.py` is the single source of truth for runtime session registry/state helpers and owns `ACTIVE_SESSIONS`.
- The runtime session snapshot lives at `session["config_snapshot"]` and contains `SYSTEM_PROMPT`, `LLM_MODEL`, `LLM_TEMPERATURE`, `TTS_VOICE_ID`, `TTS_MODEL`, `INITIAL_GREETING_PROMPT`, and `SAMPLE_RATE`.
- `PHASE_0_SETUP.md` setup verification passed locally after dependency installation, `.env` creation, app import checks, and settings/database sanity checks.
- `PHASE_1_TRANSPORT.md` uses `pipecat.transports.websocket.fastapi` as the expected Pipecat FastAPI transport API surface in the repo venv.
- Pipecat-related code must be cross-checked against official Pipecat docs and the installed repo venv API surface before implementation.
- Phase 1 transport bootstrap closes with exact WebSocket codes and reasons: `1008/unsupported_provider`, `1008/missing_exotel_identity`, `1008/duplicate_call_sid`, and `1000` after successful Phase 1 bootstrap.
- Phase 1 keeps runtime session state serializable-only and does not store the transport object inside `ACTIVE_SESSIONS`.
- `PHASE_1_TRANSPORT.md` verification passed locally for imports, route registration, Exotel serializer `16000` Hz wiring, and bootstrap close-path sanity.
- Phase 2 now follows a documentation-first execution style before pipeline implementation begins.
- Phase 2 uses `PHASE_2_PIPELINE_FLOW.md`, `PHASE_2_STATE_MACHINE.md`, and `PHASE_2_BREAKDOWN.md` to lock lifecycle, boundaries, and internal sequencing before code changes.
- Internal Phase 2 chunks are planning aids only and do not create new official repository phases or new `PHASE_PROGRESS.md` entries.
- Phase 2 implementation verified all Pipecat 1.2.1 imports before coding: `SileroVADAnalyzer`, `LLMMessagesAppendFrame`, `UserBotLatencyObserver`, `Pipeline`, `PipelineRunner`, `PipelineTask`, `PipelineParams`, `LLMContext`, `LLMContextAggregatorPair`, `LLMUserAggregatorParams`, `CartesiaTTSService`, `CerebrasLLMService`, `DeepgramSTTService`, `AlwaysUserMuteStrategy`, `MuteUntilFirstBotCompleteUserMuteStrategy`.
- `INITIAL_GREETING_PROMPT` added to `.env.example`, `app/config.py`, `app/runtime.py` (ConfigSnapshot and build_config_snapshot). Active calls snapshot it at creation.
- Pipeline service (`app/services/pipecat_pipeline.py`) uses `run_pipeline()` as the single route-facing entry point. It returns a serializable `PipelineExecutionResult` — no live Pipecat objects leak to the router.
- Transport factory extended with `audio_in_enabled=True`, `audio_out_enabled=True`, and sample rate flags for the active pipeline to carry call audio.
- Observer registered as `PipelineTask(..., observers=[latency_observer])` — never inside `PipelineParams`.
- `SileroVADAnalyzer(sample_rate=16000)` is inside `LLMUserAggregatorParams`, not a standalone pipeline stage.
- Mute strategies: `MuteUntilFirstBotCompleteUserMuteStrategy()` + `AlwaysUserMuteStrategy()` for no-barge-in with reserved first bot turn.
- Initial greeting driven via `LLMMessagesAppendFrame` with `INITIAL_GREETING_PROMPT` from config snapshot, treated as a user-role instruction to the LLM.
- Transport disconnect handled via `@transport.event_handler("on_client_disconnected")` which calls `await task.cancel()`.
- `PipelineRunner(handle_sigint=False)` used for per-call execution inside FastAPI.
- `PipelineParams(enable_metrics=True, audio_in_sample_rate=16000, audio_out_sample_rate=16000)` applied.
- Phase 1 successful bootstrap `1000` close replaced with `run_pipeline()` call. All existing rejection semantics preserved.
- Phase 3 `finalize_call_log()` boundary is explicitly marked in the telephony router with placeholder comments.
- `ACTIVE_SESSIONS` cleanup guaranteed in `finally` block of the telephony route.
- No live Exotel, telephony, or voice behavior was claimed or validated.
- Verified Pipecat 1.2.1 service construction patterns (confirmed against installed venv):
  - `DeepgramSTTService`: `sample_rate=16000` as a direct constructor kwarg; STT recognition options via `settings=DeepgramSTTSettings(language="en")`. `DeepgramSTTService.InputParams` does NOT exist — using it raises `AttributeError` at runtime.
  - `CerebrasLLMService`: model and temperature via `settings=CerebrasLLMSettings(model=..., temperature=...)`.
  - `CartesiaTTSService`: `sample_rate=16000` as a direct constructor kwarg; `voice` and `model` via `settings=CartesiaTTSSettings(voice=..., model=...)`. The deprecated `voice_id=` and `model=` direct kwargs emit `DeprecationWarning` in 1.2.1.
- `PipelineTask.queue_frame()` uses an `asyncio.Queue` initialized in `__init__`, so frames queued before `runner.run()` are safely buffered and delivered once the runner drains the queue. Greeting frame pre-queuing is safe.
- `WebSocketDisconnect` in the telephony router is only reachable for disconnects during identity resolution or session creation (before pipeline starts). Disconnects during active pipeline execution are absorbed by the transport `on_client_disconnected` callback, which calls `task.cancel()` and lets the runner unwind before `run_pipeline()` returns.

## Assumptions

- Local development is the only target for the first implementation cycle.
- Later coding sessions will use these Markdown files as the primary operating contract.
- Variable naming should stay consistent with `call_sid` and `stream_sid`.

## Next Steps

1. ~~Phase 4 (integration) must wire `finalize_call_log()` and `persist_call_log()` into the telephony router at the documented Phase 3 finalization boundaries.~~ — Resolved in Phase 4.
2. Transcript capture is deferred — `UserBotLatencyObserver` does not emit text, so a separate mechanism is needed.
3. The user should set `MONGO_DB_NAME=voice_agent_dev` (or similar Atlas database) in their local `.env`.
4. Later dashboard/config mutation work must not mutate a running session's `config_snapshot`.
5. Normal tests may verify pipeline assembly, cleanup, wiring shapes, and Phase 3 schema/logging shapes.
6. Motor 3.7.1 is technically deprecated (May 2026). Post-v1 migration to PyMongo async is a known future item.
7. Update this file whenever a meaningful architectural choice is made.

## Phase 3 Decisions

- Persistence schemas use Pydantic: `LatencyEvent`, `TurnMetrics`, `CallMetrics`, `CallLog`.
- `LatencyEvent` enforces `{"time": float, "event": str}` exactly.
- `CallLog` is the top-level MongoDB document shape in the `call_logs` collection.
- `normalize_latency_breakdown()` extracts timing data directly from `LatencyBreakdown` fields (`.ttfb`, `.text_aggregation`, `.user_turn_start_time`, `.user_turn_secs`, `.function_calls`) instead of calling `chronological_events()`, because that method returns `list[str]` and discards the `start_time` floats.
- Observer event callbacks are registered on the existing `UserBotLatencyObserver` using `@latency_observer.event_handler(...)` — the Pipecat `BaseObject` decorator API.
- `on_latency_measured` and `on_latency_breakdown` are paired via a mutable `_pending_latency` dict. Both events fire sequentially from `_handle_bot_started_speaking()` as separate `asyncio.create_task` calls. CPython asyncio drains tasks in FIFO order.
- `on_first_bot_speech_latency` fires once (client connect → first bot speech) and writes to `session["first_latency"]` via `update_session_first_latency()`.
- Runtime session helpers (`update_session_first_latency`, `append_session_turn_metrics`) guard against missing sessions and accept only serializable data — no Pipecat types.
- `finalize_call_log()` converts the runtime session's accumulated `metrics` dict into a typed `CallLog` ready for MongoDB insertion.
- `app/database.py` uses Motor `AsyncIOMotorClient` as a singleton, initialized in the FastAPI lifespan. MongoDB auto-creates DB and collection on first insert.
- Loguru dual-sink: colored console (stderr) + JSON file (`logs/voice_agent.log`) with `50 MB` rotation, `7 days` retention.
- `enqueue=True` on both sinks for async safety under Uvicorn.
- `diagnose=False` to prevent secret leaks in tracebacks.
- `{extra}` in the console format renders `call_sid` and other bound context.
- ~~Telephony router Phase 3 placeholder comments are preserved. Actual `finalize_call_log()` + `persist_call_log()` wiring into the route is Phase 4 integration scope.~~ — Resolved in Phase 4: finalization wired at normal completion, pre-pipeline disconnect, and unexpected error boundaries.
- No transcript storage in Phase 3. The `RuntimeSession.transcript` list exists but is not populated.
- Phase 3 verification used mock breakdown objects to test normalization without live Pipecat or Exotel.

## Latency and Outbound Dashboard Decisions

- **Deepgram Flux Integration:** To drive turn-detection latency down to ~200ms, Phase 4 will use the Deepgram `"flux"` model. Eager turn-detection settings (`eot_threshold: 0.65`, `eager_eot_threshold: 0.50`) will be passed via the `settings=DeepgramSTTSettings(extra=...)` block, which Pipecat automatically maps to its WebSocket handshake.
- **GPT OSS 120B Pipelined Streaming:** High intelligence is required, meaning GPT OSS 120B is non-negotiable. To keep TTFB sub-1-second, the system prompt must instruct the model to start responses with a short, punchy opening sentence (ending with a period). Because Cerebras processes prompt prefill and generation at 1800+ tokens/sec, the first sentence is generated in <10ms and synthesized instantly (while the lengthy rich-persona text streams afterward).
- **Cartesia Buffering Safeguard:** Cartesia is kept on `TextAggregationMode.SENTENCE` paired with `max_buffer_delay_ms=0` for premium prosody and instant synthesis. If `TOKEN` mode is used, `max_buffer_delay_ms` must be explicitly overridden to a low value (e.g. `100` ms) to bypass Cartesia's 3-second server-side buffer.
- **Native 16 kHz Exotel Sample Rate:** The Exotel Applet will be configured with a 16 kHz sample rate parameter. Combined with the 16 kHz pipeline, the `ExotelFrameSerializer` performs 1:1 copy operations with completely zero resampling overhead.
- **Observer Telemetry VAD Safeguard:** `SileroVADAnalyzer` must remain active in the context aggregator. The `UserBotLatencyObserver` strictly depends on VAD timing frames downstream to measure turn-by-turn latency. If VAD is entirely disabled, the observer will fail to compute or log conversational metrics.
- **Outbound Phase Restructuring:** The roadmap has been restructured into:
  - **Phase 4:** Core Integration and Performance Tuning.
  - **Sub-Phase 5.1:** Exotel Outbound Client, Webhook endpoint (`GET /telephony/webhook`), and `.env` credentials parsing.
  - **Sub-Phase 5.2:** Jinja2 templates, HTMX live sessions polling (every 2s), and completed calls latency visualizer.
  - **Phase 6:** Verification Tests.
- **New Outbound Env Credentials:** Outbound triggering relies on the new parameters `EXOTEL_ACCOUNT_SID`, `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN`, `EXOTEL_VIRTUAL_NUMBER`, `EXOTEL_FLOW_ID`, and `PUBLIC_NGROK_URL`. The ngrok URL serves as the public webhook root callback when the caller answers.

## Phase 4 Decisions

- **Finalization wiring:** `finalize_call_log()` + `persist_call_log()` wired in `telephony.py` at two boundaries:
  1. Normal pipeline completion (after `run_pipeline()` returns) — uses `result.completed` and `result.error`.
  2. Pre-pipeline `WebSocketDisconnect` — marks `completed=False` with descriptive error string.
  Both paths guard persistence failures with `try/except` to avoid masking the original disconnect/error.
- **Finalization ordering:** `finalize_call_log()` runs while the session is still in `ACTIVE_SESSIONS`. `delete_session()` in the `finally` block runs after.
- **Deepgram Flux configuration:** `model="flux"` set via `DeepgramSTTSettings(model=...)`. Eager turn-detection thresholds (`eot_threshold: 0.65`, `eager_eot_threshold: 0.50`) passed via `extra` dict, which Pipecat maps to Deepgram WebSocket query params. `interim_results=False` to keep frame queue lightweight.
- **Cartesia SENTENCE aggregation:** `TextAggregationMode.SENTENCE` and `max_buffer_delay_ms=0` are **direct constructor kwargs** on `CartesiaTTSService` (not inside `CartesiaTTSSettings`). Verified against Pipecat 1.2.1 source. `CartesiaTTSSettings` only holds `voice`, `model`, `generation_config`, `pronunciation_dict_id`.
- **TextAggregationMode import path:** `from pipecat.services.tts_service import TextAggregationMode`.
- **System prompt optimized:** `.env` and `.env.example` updated to enforce "start with a short sentence ending with a period" pattern. Cerebras generates a 10-word opening sentence in <10ms; SENTENCE mode sends it to Cartesia immediately while the LLM streams the rest.
- **No changes needed** to `config.py`, `runtime.py`, `main.py`, or `metrics_logger.py`. All Phase 3 code was already correct.
- **Safety audit confirmed:** VAD analyzer, observer registration, mute strategies, disconnect handler, runner config, and config_snapshot immutability all preserved without modification.

## Phase 5.1 Decisions

- **Webhook format correction:** The original spec called for ExoML XML `<Connect><Stream>` response, but Exotel's **Voicebot Applet** (bidirectional) uses a different pattern. Exotel sends a `GET` request to the webhook URL and expects a JSON response `{"url": "wss://..."}`. The ExoML XML pattern is for the **Stream Applet** (unidirectional only). Corrected based on official Exotel docs and the Pipecat exotel-chatbot example.
- **Mumbai cluster:** Hardcoded default `EXOTEL_SUBDOMAIN=api.in.exotel.com` for India-based calls. Configurable via env var for users on the Singapore cluster (`api.exotel.com`).
- **Exotel v1 number formatting:** The v1 Connect API requires `0`-prefix format (e.g., `09876543210`), **not** E.164 (`+919876543210`). `_format_number_for_v1()` strips `+91` prefix and adds leading `0`.
- **Form-encoded POST:** Exotel v1 API expects `application/x-www-form-urlencoded` payloads, not JSON. httpx `data=` parameter handles this.
- **HTTP Basic Auth:** `httpx.BasicAuth(api_key, api_token)` matches Exotel's authentication scheme. The Pipecat reference example confirms this pattern.
- **XML response parsing:** `xml.etree.ElementTree` (stdlib) extracts `<Sid>` — no new dependency needed.
- **Option B lifecycle:** `ExotelOutboundClient` created at startup in FastAPI lifespan, stored on `app.state.exotel_client`, closed at shutdown. Wrapped in `try/except ValueError` so the server starts in inbound-only mode if Exotel credentials are missing.
- **7 config fields (not 6):** Added `EXOTEL_SUBDOMAIN` as a 7th field beyond the original 6 in the spec. Mumbai vs Singapore cluster needs to be configurable per deployment.
- **Flow URL construction:** `http://my.exotel.com/{ACCOUNT_SID}/exoml/start_voice/{FLOW_ID}` — the App Bazaar flow ID references the Voicebot Applet that triggers the webhook callback.
- **Dynamic Webhook WebSocket URL query parameter:** Appended `?sample-rate=16000` to the returned dynamic WebSocket URL in `GET /telephony/webhook` to ensure Exotel streams at 16 kHz instead of falling back to its 8 kHz default when using dynamic URLs.

## Phase 6 Decisions & Discoveries

- **Prompt tuning for conversational screening:** Updated the active prompt source in `.env` so the assistant behaves more like a natural phone screening agent instead of a rigid form-filler. The prompt now emphasizes brief direct answers, one question at a time, natural acknowledgements, not re-asking captured details, asking only for missing pieces, answering small screening-related clarifications briefly, and not declaring completion before the call is actually over or asking new questions after closing begins.
- **Post-call transcript capture:** Completed user-to-assistant transcript turns are now persisted at `CallLog.transcript` as `{"turn_index": int, "user_text": str, "assistant_text": str}`. The runtime session remains serializable-only and accumulates transcript turns in `session["transcript"]`.
- **Transcript capture points:** Finalized user speech is captured from `TranscriptionFrame` before the user aggregator. Assistant transcript text is buffered from `LLMTextFrame` chunks between `LLMFullResponseStartFrame` and `LLMFullResponseEndFrame`, then paired into one completed turn.
- **Greeting exclusion rule:** The synthetic initial greeting queued through `LLMMessagesAppendFrame` does not create a transcript row because transcript turns are only persisted when a real finalized user utterance exists.
- **Dashboard transcript rendering:** The dashboard now renders transcript only in the per-call detail view as clean paired turn cards above the latency breakdown. Historical logs with no transcript render the empty state `No transcript captured for this call.`.
- **Verification:** Non-live pytest suite passed after transcript implementation: `31 passed, 3 deselected`.

- **Pytest Collection Warnings:** Renamed `TestSourceNode` -> `MockSourceNode` in all three live test modules to prevent pytest from attempting to collect it as a test class.
- **Pipeline Frame Propagation:** Ensured all custom `FrameProcessor` overrides end with `await self.push_frame(frame, direction)`. This prevents control frames from being silently swallowed, resolving pipeline runner hangs/deadlocks.
- **Settings Cache Resolution:** Cleared the `@lru_cache` of `get_settings()` inside `conftest.py` using `get_settings.cache_clear()` on every environment shift. This prevents unit tests' mock credentials from leaking into the live integration tests.
- **Deepgram STT Audio Format Alignment:** Checked and identified `tests/assets/test_speech.wav` as a 48kHz stereo WAV file. Updated both `DeepgramSTTService` and the `MockSourceNode` in `tests/test_live_stt.py` to stream and parse at 48000 Hz with 2 channels, allowing successful transcription in ~7 seconds without audio distortion or connection drops.


## Phase 5.2 Decisions

- **Jinja2 & python-multipart added:** Declared `jinja2` and `python-multipart` explicitly as project dependencies in `pyproject.toml` to support HTML rendering and form data decoding natively in FastAPI.
- **FastAPI 0.110+ TemplateResponse Signatures:** Used the modern keyword-argument signature `templates.TemplateResponse(request=request, name=..., context=...)` to ensure proper request context propagation and compliance with current FastAPI guidelines.
- **MongoDB connection resiliency:** Wrapped MongoDB cursor list queries and document finds inside `try-except` blocks. If MongoDB is down or unconfigured, the dashboard continues to function normally (showing a "DB Offline" indicator and clean empty state in the completed logs panel) instead of throwing an HTTP 500 error.
- **BSON ObjectId type safety:** Enforced type verification using `isinstance(log.get("_id"), ObjectId)` before extracting generation time, preventing exceptions if mock/test logs are loaded with string IDs.
- **Strict consolidation of CSS:** Moved all inline style attributes from HTML tags to class definitions inside the `<style>` block to adhere to the "Never use ad-hoc CSS utilities" rule.
- **HTMX status 200 error delivery:** Returned lookups or dialer errors as HTMX HTML snippets with an HTTP 200 status code, enabling HTMX to perform target swaps cleanly on the frontend.
- **Local scoping of include variables:** Wrapped all HTML includes using `{% with part = '...' %}` blocks to avoid global namespace leakage.
- **Color-coded timeline events:** Mapped specific color cues to different service latencies (TTS = Green, LLM = Purple, STT = Yellow, User Turn = Blue) to make turn-by-turn latency events highly readable.
- **Absolute template path resolution:** Derived the Jinja2 template directory dynamically relative to the router's file path (`app/routers/dashboard.py`), avoiding path errors if FastAPI is started from subdirectories.
- **Verification separation:** Left the `tests/test_dashboard.py` file out of the git repository during this phase to strictly respect the allowed files constraint, using a local verification scratch script instead and detailing the test mocks under the Verification Plan in `implementation_plan.md` for Phase 6.

## Post-Phase 6 Deepgram Flux Import Resolution

- **Deepgram Flux Import Path Fix:** Fixed a startup crash caused by importing `DeepgramFluxSTTService` from `pipecat.services.deepgram.flux`. In Pipecat 1.2.1, `pipecat/services/deepgram/flux/__init__.py` is empty, so the service must be imported from the submodule `pipecat.services.deepgram.flux.stt` directly.
- **Verification:** Verified that `main.py` imports successfully and ran the `pytest` suite, resulting in 21/21 passing tests.

## Warm Outbound Opening Decisions

- **Pending outbound context:** Outbound calls now pre-stage a serializable context with immutable `config_snapshot`, generated greeting text, and opening state before Exotel's WebSocket connects. The live transport and pipeline task are still created only after `call_sid` and `stream_sid` arrive.
- **LLM-generated greeting path:** The dashboard outbound route attempts a short Cerebras greeting generation before dialing. If generation times out or fails, the call still proceeds and the WebSocket path falls back to the existing live LLM greeting prompt.
- **Prepared greeting injection:** Prepared greetings are sent into the live Pipecat pipeline with `TTSSpeakFrame`, preserving the normal TTS/output path without requiring a synthetic user prompt.
- **Readiness gate:** Outbound finalized user text is classified as `ready`, `not_now`, `question`, or `unclear` before it can enter task mode. Simple greetings like "hello" remain readiness-phase input and do not start the task.
- **Transcript event model:** Persisted transcripts now use chronological role-based events (`assistant`/`user` + `greeting`/`readiness`/`task`/`closing` phase) so assistant-only greetings and early readiness exchanges are visible in call logs.
- **Verification:** Full pytest suite passed after implementation: `37 passed, 3 warnings`.

## Warm Outbound Opening Stability Fix

- **Observed failure:** When pre-call greeting generation failed, outbound fallback queued `INITIAL_GREETING_PROMPT` as a user message to the LLM. If `INITIAL_GREETING_PROMPT` was already actual spoken greeting text, the LLM could answer it as the callee (e.g. "Yes, this works for me. Go ahead."), creating a bad greeting transcript.
- **Direct greeting fallback:** Outbound calls now always speak either the validated pre-generated greeting or the configured `INITIAL_GREETING_PROMPT` directly through `TTSSpeakFrame`. The outbound fallback no longer uses `LLMMessagesAppendFrame`.
- **Greeting completion gate:** Early finalized user speech is ignored while the outbound greeting audio is still pending. Readiness handling starts only after `BotStoppedSpeakingFrame` marks the greeting complete.
- **Greeting validation:** Pre-generated greetings are rejected if they look like readiness/consent responses instead of openings, causing safe fallback to the configured initial greeting.
- **Verification:** Full pytest suite passed after the stability fix: `38 passed, 3 warnings`.

## Assistant Self-Talk Containment Fix

- **Observed failure:** After task mode began, the LLM could produce a long simulated interview transcript in one completion, including fake repeated user answers such as "Bogus Ranch" and multiple follow-up questions. The pipeline then spoke that entire completion.
- **Runtime guardrails:** `_build_context()` now appends non-negotiable runtime voice-output rules to the configured system prompt: speak only as assistant, never simulate the user, produce one assistant move, and ask at most one question.
- **Response boundary guard:** Added `AssistantResponseGuardProcessor` between the LLM and TTS/transcript capture. It truncates any LLM response at the first question mark, speaker-label marker, or safe maximum length, then emits a single `LLMFullResponseEndFrame` and swallows the rest of the runaway completion.
- **Provider cap avoided:** A provider-level `max_completion_tokens` cap was tested but removed because the live Cerebras smoke test returned no text with that setting. The pipeline-level response guard is provider-safe and keeps live LLM output working.
- **Verification:** Full pytest suite passed after the self-talk containment fix: `39 passed, 3 warnings`.

## Structured Conversation Controller Refactor

- **Raw live LLM path removed from main pipeline:** The live Pipecat call pipeline no longer forwards finalized user transcripts into a raw LLM processor. Finalized user text is intercepted by `UserConversationProcessor`, routed through `conversation_controller.decide_next_turn()`, and only the validated `TurnDecision.assistant_text` is spoken via `TTSSpeakFrame`.
- **Hard truncation guard removed:** `AssistantResponseGuardProcessor` and assistant text chunk capture were removed from the live pipeline. Stability now comes from structured internal LLM output and validation, not cutting raw prose at a question mark.
- **Controller-owned LLM decisions:** Added `app/services/conversation_controller.py` with `TurnDecision`, `ConversationRuntimeState`, greeting validation, structured turn validation, safe fallback text, and an isolated Pipecat/Cerebras text-generation path. This avoids the previous raw `httpx` Cerebras helper.
- **Greeting config split:** Added `INITIAL_GREETING_INSTRUCTIONS` and `INITIAL_GREETING_FALLBACK_TEXT` while preserving `INITIAL_GREETING_PROMPT` compatibility. Legacy prompt text is treated as fallback speech only if it looks like a complete spoken greeting; otherwise it remains instructions.
- **Latency tuning:** Reduced default `OUTBOUND_GREETING_START_DELAY_MS` from `500` to `100`, preserved direct greeting playback through `TTSSpeakFrame`, and set Deepgram Flux `eot_threshold=0.65` plus `eot_timeout_ms=1000`.
- **Pending context cleanup:** Added TTL cleanup for `PENDING_OUTBOUND_CALLS` with default `PENDING_OUTBOUND_TTL_SECS=600`, invoked during outbound trigger and WebSocket binding.
- **Verification:** Full pytest suite passed after controller refactor: `46 passed, 3 warnings`.
