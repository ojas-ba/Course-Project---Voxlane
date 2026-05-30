# PHASE_4_INTEGRATION.md - Integration Glue and Performance Tuning

## Phase Gate

`PHASE_3_LOGGING.md` must be marked `DONE` in `PHASE_PROGRESS.md` before this phase starts.

## Objective

Integrate transport, pipeline, runtime session handling, and persistence logging so that the voice agent has a type-safe, fully optimized conversational response flow. This phase implements the specific low-latency configurations (Deepgram Flux, Cerebras GPT OSS 120B prompt guidance, and Cartesia sentence aggregation) to achieve sub-1-second conversational turns.

## Allowed Files

- `app/routers/telephony.py`
- `app/services/pipecat_pipeline.py`
- `app/utils/metrics_logger.py`
- `app/config.py`
- `app/runtime.py`
- `main.py`

## In Scope

- **MongoDB Metrics Finalization:** Connect `finalize_call_log()` and `persist_call_log()` into the Exotel route inside `/telephony/exotel` at the Normal and Pre-pipeline finalization boundaries.
- **Deepgram Flux Setup:** Configure `DeepgramSTTService` settings with `model="flux"` and pass eager turn-detection parameters (`eot_threshold: 0.65`, `eager_eot_threshold: 0.50`) in the settings `extra` dictionary for WebSocket query string serialization.
- **Cerebras Prompt Tuning:** Update the `.env` `SYSTEM_PROMPT` to enforce short opening sentence punctuation (ending the first clause with a period) to enable instant synthesis, while allowing a rich conversational persona to stream afterwards.
- **Cartesia Aggregation:** Set the TTS service to `TextAggregationMode.SENTENCE` paired with `max_buffer_delay_ms=0` for premium natural prosody.
- **Observer Safety:** Retain `SileroVADAnalyzer(sample_rate=16000)` inside `LLMUserAggregatorParams` so that `VADUserStoppedSpeakingFrame` continues to be emitted; the latency observer strictly depends on this frame to calculate metrics.
- **Graceful Cancel & Unwind:** Handle `WebSocketDisconnect` during active pipeline execution by canceling the task, letting the runner unwind, and then calling `finalize_call_log()`.

## Out of Scope

- Exotel REST API outbound call triggering (Sub-Phase 5.1).
- Telephony connect webhook XML mapping (Sub-Phase 5.2).
- HTMX browser console pages (Sub-Phase 5.2).
- Unit and integration tests (Phase 6).

## Acceptance Criteria

- Telephony finalization is triggered from the normal and pre-pipeline integration paths, successfully persisting call logs in MongoDB.
- `WebSocketDisconnect` teardown order remains: await cancel -> unwind -> finalize -> cleanup runtime session.
- Deepgram Flux is initialized with the customized end-of-turn (EOT) sensitivity params.
- Cartesia operates with `SENTENCE` aggregation and `max_buffer_delay_ms=0`.
- At call start, `session["config_snapshot"]` is created with the fixed v1 shape (`SYSTEM_PROMPT`, `LLM_MODEL`, `LLM_TEMPERATURE`, `TTS_VOICE_ID`, `TTS_MODEL`, `SAMPLE_RATE`) and remains immutable.

## Self-Verification

Run normal integration-level checks only:
- App startup and module loading sanity.
- Telephony route bootstrap closure sanity.
- Latency events and final logs persistence mock verification.

Do not claim real live-call lifecycle correctness or voice-quality correctness.

## Never

- Mutate active call states from in-flight settings.
- Disable VAD in the pipeline (which breaks latency observer timing).
- Invent mock outbound dials or claims of voice integration tests.

## Completion Checklist

- [ ] Call log finalization is wired at the normal pipeline completion boundary.
- [ ] Call log finalization is wired at the pre-pipeline disconnect boundary.
- [ ] Deepgram STT model is configured for Flux with eager turn-detection parameters.
- [ ] Cartesia TTS service is configured for Sentence mode and zero server buffering delay.
- [ ] VAD analyzer remains enabled in the pipeline context aggregator for metrics logging.
- [ ] Graceful cancel, unwind, and cleanup progression is fully respected on disconnect.
- [ ] Codebase is structurally ready for Sub-Phase 5.1.
