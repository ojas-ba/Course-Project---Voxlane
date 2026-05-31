# PHASE_2_STATE_MACHINE.md - Active Call Lifecycle Contract

## Purpose

Document the intended state transitions for one active call before Phase 2 pipeline implementation begins.

## Why This File Exists

Phase 1 proves the transport bootstrap boundary, while Phase 2 introduces a long-lived pipeline task and disconnect handling. This file prevents the coding pass from inventing teardown ordering or registry ownership as it goes.

It is a lifecycle contract, not a required data-model change. Phase 2 implementation does not need to store a literal `state` field in `ACTIVE_SESSIONS` unless that proves useful within approved scope.

## State Model Overview

The v1 lifecycle is expressed as:

```text
accepted -> identified -> session_created -> transport_ready -> pipeline_ready
    -> running -> disconnecting -> finished
```

Failure during identity or session bootstrap may close the WebSocket and end before a runtime session exists. Failure after session creation must still respect the cleanup boundary for the created session.

## States

### `accepted`

What must already be true:

- The telephony route received the connection.
- The WebSocket has been accepted.

What may happen next:

- Resolve Exotel identity into `call_sid` and `stream_sid`.
- Reject invalid or unsupported bootstrap input with the established policy close behavior.

What must not happen:

- Do not build a session, transport, or pipeline before identity is valid.

### `identified`

What must already be true:

- Both `call_sid` and `stream_sid` are available using repository-standard naming.
- Provider parsing, if required, has accepted Exotel input.

What may happen next:

- Create the serializable runtime session through `app/runtime.py`.
- Reject a duplicate `call_sid`.

What must not happen:

- Do not introduce `stream_id` as internal runtime naming.
- Do not construct the pipeline before session ownership is established.

### `session_created`

What must already be true:

- `ACTIVE_SESSIONS[call_sid]` exists under `app/runtime.py` ownership.
- The session contains `call_sid`, `stream_sid`, and its `config_snapshot`.
- Its runtime data remains serializable-only.

What may happen next:

- Build the Exotel transport.
- Later update serializable transcript or metric fields through approved behavior.

What must not happen:

- Do not store transport, pipeline, task, runner, observer, or provider service objects in `ACTIVE_SESSIONS`.

### `transport_ready`

What must already be true:

- The session has been created.
- The transport uses `ExotelFrameSerializer.InputParams(exotel_sample_rate=16000, sample_rate=16000)`.

What may happen next:

- Pass the transport and call-scoped inputs through the route-to-pipeline interface.
- Assemble the Phase 2 pipeline service.

What must not happen:

- Do not change sample-rate configuration dynamically.
- Do not treat transport creation alone as a running call.

### `pipeline_ready`

What must already be true:

- The pipeline has been assembled with the planned service boundaries.
- Services use typed `.Settings()` configuration (verified Pipecat 1.2.1 pattern).
- Turn management and observer/VAD wiring follow fixed rules.
- `INITIAL_GREETING_PROMPT` is present in the session's `config_snapshot` and a greeting frame has been queued on the task via `LLMMessagesAppendFrame`.

What may happen next:

- Start the task and pipeline runner.

What must not happen:

- Do not register observers inside `PipelineParams`.
- Do not omit `SileroVADAnalyzer()` from `LLMUserAggregatorParams`.

### `running`

What must already be true:

- The task and runner have begun active processing.
- A matching serializable session remains available in `ACTIVE_SESSIONS`.

What may happen next:

- Continue active call processing.
- Accumulate phase-appropriate serializable runtime data.
- Move to disconnect handling when the WebSocket disconnects.

What must not happen:

- Do not run the Phase 1 successful-bootstrap `1000` close path.
- Do not remove the session before teardown is complete.
- Do not claim live behavior has been validated by structural tests.

### `disconnecting`

What must already be true:

- A disconnect or equivalent pipeline-ending condition has been reached for a running call.
- Any created session requiring cleanup is still traceable by `call_sid`.

What may happen next:

- Await `task.cancel()` when a task is active.
- Allow the runner to unwind.
- Reach the later `finalize_call_log()` boundary.
- Delete the session after required teardown/finalization steps.

What must not happen:

- Do not finalize before active pipeline teardown unwinds.
- Do not silently abandon a registered session.

### `finished`

What must already be true:

- Active pipeline teardown is complete when a pipeline had started.
- The later finalization boundary has been observed when that functionality exists.
- The session cleanup boundary has removed the call from `ACTIVE_SESSIONS`.

What may happen next:

- Return control from the route handler.

What must not happen:

- Do not continue processing frames or mutate a completed active-call session.

## Allowed Transitions

| From | To | Required Event Or Result |
| --- | --- | --- |
| `accepted` | `identified` | Valid `call_sid` and `stream_sid` resolved |
| `identified` | `session_created` | Session registered without duplicate `call_sid` |
| `session_created` | `transport_ready` | Fixed-rate Exotel transport created |
| `transport_ready` | `pipeline_ready` | Pipeline assembly succeeds |
| `pipeline_ready` | `running` | Task and runner start |
| `running` | `disconnecting` | Disconnect or controlled pipeline termination begins |
| `disconnecting` | `finished` | Teardown boundary completes and session is cleaned up |

Bootstrap rejection paths before `session_created` close without entering the active-call lifecycle. Errors after `session_created` must pass through cleanup handling appropriate to the created resources.

## Invalid Transitions / Guardrails

- `accepted` directly to `transport_ready` or `pipeline_ready` is invalid because call identity and session creation are required first.
- `session_created` directly to `running` is invalid because fixed transport and pipeline assembly boundaries must remain visible.
- `running` directly to `finished` is invalid for a disconnecting pipeline because cancellation and unwind ordering must be honored.
- `finished` back to any active state is invalid.
- Failure during `transport_ready` or `pipeline_ready` must still pass through session cleanup. Assembly errors must not leave an orphaned session in `ACTIVE_SESSIONS`.
- Any transition that stores non-serializable execution objects in `ACTIVE_SESSIONS` violates the runtime boundary.
- Any transition that makes the Phase 1 successful `1000` stub close part of the running pipeline path violates the Phase 2 handoff goal.

## State Ownership

- `app/runtime.py` remains the owner of the runtime registry and session-state helper functions, including `ACTIVE_SESSIONS`.
- The route owns entry/bootstrap control flow and calls the pipeline boundary after transport setup.
- The pipeline service owns pipeline assembly and task/runner execution behavior.
- Later logging/persistence work owns final stored-call shaping and `finalize_call_log()` implementation.
- These documented lifecycle states do not require expanding the runtime session schema during this documentation pass.

## Disconnect Semantics

For an active Phase 2 pipeline, `WebSocketDisconnect` triggers the documented `running -> disconnecting -> finished` path:

1. Await `task.cancel()`.
2. Let the runner unwind.
3. Invoke the later finalization hook when implemented in the appropriate phase.
4. Remove the session from `ACTIVE_SESSIONS`.

This replaces Phase 1 successful bootstrap behavior only after the pipeline execution path is implemented.

## Future Integration Notes

- Phase 3 can attach structured metrics and finalization behavior at the documented post-unwind boundary.
- Later dashboard/config work must not mutate a running session's `config_snapshot`.
- Normal tests may verify transitions and cleanup shape without representing live telephony or voice validation.
