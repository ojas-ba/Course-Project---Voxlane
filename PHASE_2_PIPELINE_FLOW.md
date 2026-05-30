# PHASE_2_PIPELINE_FLOW.md - Pipeline Runtime Flow

## Purpose

Define the intended end-to-end Phase 2 call flow in implementation-facing language before pipeline code is written. This document narrows the handoff between the existing telephony bootstrap, the future Pipecat pipeline service, and later finalization work.

## Inputs And Assumptions Inherited From Phase 1

- The public route remains `WS /telephony/exotel`.
- Route identity is normalized to `call_sid` and `stream_sid`.
- Exotel transport construction already uses `ExotelFrameSerializer.InputParams(exotel_sample_rate=16000, sample_rate=16000)`.
- `app/runtime.py` owns `ACTIVE_SESSIONS` keyed by `call_sid`.
- A session already contains `call_sid`, `stream_sid`, `metrics`, `transcript`, `first_latency`, and `config_snapshot`.
- `config_snapshot` is attached at session creation and represents immutable per-call configuration. It includes `SYSTEM_PROMPT`, `LLM_MODEL`, `LLM_TEMPERATURE`, `TTS_VOICE_ID`, `TTS_MODEL`, `INITIAL_GREETING_PROMPT`, and `SAMPLE_RATE`.
- `INITIAL_GREETING_PROMPT` is a required Phase 2 field used to trigger the bot's opening turn via a queued `LLMMessagesAppendFrame`.
- `ACTIVE_SESSIONS` is a serializable-only runtime state store. Transport, pipeline, task, runner, observer, and service instance objects must not be stored in it.

## Current Phase 1 Behavior

The implemented route currently:

1. Accepts the WebSocket.
2. Resolves `call_sid` and `stream_sid` from the query string or parsed Exotel framing.
3. Creates a session in `ACTIVE_SESSIONS`, with `config_snapshot` already attached.
4. Builds the Exotel transport.
5. Closes the WebSocket with `1000` after successful bootstrap.
6. Deletes the created session in `finally`.

The successful `1000` close is an intentional Phase 1 stub. It validates bootstrap wiring only and is not final voice-agent behavior.

## What Phase 2 Changes

The later Phase 2 implementation replaces only the successful post-bootstrap stub path with pipeline creation and execution. Identity resolution, session ownership, fixed transport setup, and serializable session constraints remain in place.

Phase 2 must introduce a clear router-to-pipeline handoff without implementing Phase 3 persistence finalization. It must leave a clear boundary where later logging/finalization behavior will attach after graceful pipeline teardown.

## End-To-End Call Flow

The intended Phase 2 lifecycle is:

1. WebSocket is accepted by the telephony router.
2. Identity is resolved and normalized as `call_sid` and `stream_sid`.
3. A runtime session is created in `ACTIVE_SESSIONS` through `app/runtime.py`.
4. The runtime session already includes its immutable `config_snapshot`.
5. The Exotel transport is created with fixed `16000` Hz serializer and pipeline sample-rate inputs.
6. The pipeline service is created using the transport and serializable call/session inputs.
7. A Pipecat task and runner are started through the pipeline service boundary.
8. Active call processing continues while the task and runner are alive.
9. A WebSocket disconnect is caught at the owning call-flow boundary.
10. The active task is gracefully cancelled by awaiting `task.cancel()`.
11. The pipeline runner is allowed to unwind after cancellation.
12. A later finalization hook boundary is reached; Phase 3 will implement `finalize_call_log()` behavior.
13. The runtime session cleanup boundary removes the call from `ACTIVE_SESSIONS` after teardown and later finalization behavior have completed.

The implementation may choose narrow function names and return types, but it must preserve this ordering.

## Router Responsibilities

- Own the `WS /telephony/exotel` entrypoint and WebSocket acceptance.
- Resolve and normalize `call_sid` and `stream_sid`.
- Enforce existing close handling for unsupported provider, missing identity, and duplicate `call_sid` paths.
- Create the serializable runtime session and transport before invoking the pipeline handoff.
- Replace the successful Phase 1 `1000` stub close with pipeline execution logic during the later coding pass.
- Participate in disconnect and cleanup control flow without absorbing service assembly internals.

## Pipeline Service Responsibilities

- The pipeline service is implemented in `app/services/pipecat_pipeline.py` as defined in the repository layout.
- Accept the transport and the call-scoped, serializable inputs required to assemble execution.
- Construct STT, LLM, and TTS services through typed `.Settings()` objects.
- Assemble the Pipecat pipeline in the planned transport/STT/LLM/TTS flow.
- Configure muted turn-management through `LLMUserAggregatorParams.user_mute_strategies`.
- Include `SileroVADAnalyzer()` inside `LLMUserAggregatorParams` for timing frames.
- Register `UserBotLatencyObserver` directly on `PipelineTask(..., observers=[latency_observer])`.
- Expose a clean execution/cancellation boundary to the router without placing task or service objects in `ACTIVE_SESSIONS`.

## Runtime Session Responsibilities

- `app/runtime.py` remains the sole owner of `ACTIVE_SESSIONS` and its session helpers.
- The registered record remains serializable-only and keyed by `call_sid`.
- The session carries identity, `config_snapshot`, transcript/metrics containers, and `first_latency` for later phase use.
- The session does not own live Pipecat execution objects.
- Pipeline execution may receive or reference the serializable session as needed for phase-appropriate updates, but it must not change the registry ownership model.

## Disconnect And Teardown Flow

For a running Phase 2 pipeline, disconnect handling must observe this order:

1. Catch `WebSocketDisconnect`.
2. Await cancellation of the active pipeline task.
3. Let the pipeline runner unwind.
4. Reach the `finalize_call_log()` hook boundary reserved for the logging/persistence phase.
5. Remove the runtime session from `ACTIVE_SESSIONS`.

Phase 2 must preserve this ordered interface even though it does not implement persistence finalization logic. Phase 1's present bootstrap cleanup occurs without a running task and will be replaced on the successful pipeline path.

## Explicit Non-Goals

- No live Exotel or real voice validation.
- No persistence implementation or final call-log write in this phase.
- No dashboard/config mutation route work.
- No changes to fixed `16000` Hz decisions.
- No storage of live execution objects in `ACTIVE_SESSIONS`.
- No production readiness claims.

## Questions Already Resolved

- Route path: `WS /telephony/exotel`.
- Runtime registry owner: `app/runtime.py`.
- Runtime identity names: `call_sid` and `stream_sid`.
- Active-session configuration: immutable `config_snapshot`.
- Runtime session contents: serializable-only.
- Serializer inputs: `exotel_sample_rate=16000` and `sample_rate=16000`.
- Observer registration: on `PipelineTask(..., observers=[latency_observer])`.
- No-barge-in timing support: `SileroVADAnalyzer()` inside `LLMUserAggregatorParams`.
- Graceful teardown ordering: cancel task, unwind runner, later finalize, then cleanup.
- `INITIAL_GREETING_PROMPT`: required config snapshot field; delivered as a queued `LLMMessagesAppendFrame` after task creation, before `runner.run()`. The `asyncio.Queue` in `PipelineTask` buffers the frame safely until the runner drains it.

## Questions Intentionally Deferred

- Persistence model implementation and final `finalize_call_log()` behavior belong to Phase 3.
- Dashboard/config mutation routes belong to later integration work.
- Live voice quality, latency realism, and Exotel operational behavior remain human validation tasks.
