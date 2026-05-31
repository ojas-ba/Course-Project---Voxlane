# PHASE_1_IMPLEMENTATION_NOTES.md - Phase 1 Handoff

## Purpose

This file records what was actually implemented during `PHASE_1_TRANSPORT.md` so the next agent can start `PHASE_2_PIPELINE.md` with concrete repo context instead of re-discovering Phase 1 behavior.

Read this file together with:

- `PHASE_1_TRANSPORT.md`
- `PHASE_2_PIPELINE.md`
- `NOTES.md`
- `TECHNICAL_PLAN.md`

## What Was Implemented

### App Wiring

- `main.py` now mounts the telephony router on the main FastAPI app.
- The Exotel WebSocket route is active at `WS /telephony/exotel`.

### Fixed Transport Configuration

- `app/config.py` defines `V1_SAMPLE_RATE = 16000`.
- The sample rate remains fixed and non-configurable in v1.
- `app/services/transport_factory.py` constructs:
  - `ExotelFrameSerializer`
  - `FastAPIWebsocketTransport`
- Serializer construction is fixed to:

```python
ExotelFrameSerializer.InputParams(
    exotel_sample_rate=16000,
    sample_rate=16000,
)
```

### Runtime Session Registry

- `app/runtime.py` is now the runtime source of truth for active calls.
- `ACTIVE_SESSIONS` is keyed by `call_sid`.
- The runtime session shape is already defined for later phases:

```python
{
    "call_sid": str,
    "stream_sid": str,
    "metrics": dict[str, Any],
    "transcript": list[dict[str, Any]],
    "first_latency": float | None,
    "config_snapshot": {
        "SYSTEM_PROMPT": str,
        "LLM_MODEL": str,
        "LLM_TEMPERATURE": float,
        "TTS_VOICE_ID": str,
        "TTS_MODEL": str,
        "INITIAL_GREETING_PROMPT": str,
        "SAMPLE_RATE": 16000,
    },
}
```

- The following helpers exist and should be reused instead of replaced casually:
  - `build_config_snapshot(settings)`
  - `create_session(call_sid, stream_sid, settings)`
  - `get_session(call_sid)`
  - `delete_session(call_sid)`

### Telephony Route Bootstrap Flow

The current `app/routers/telephony.py` route does the following:

1. Accepts the WebSocket.
2. Tries to resolve `CallSid` and `StreamSid` from query params.
3. Falls back to `pipecat.runner.utils.parse_telephony_websocket(websocket)` if either ID is missing.
4. Requires the parsed provider to be `exotel`.
5. Remaps Pipecat parser keys:
   - `call_data["call_id"] -> call_sid`
   - `call_data["stream_id"] -> stream_sid`
6. Creates the runtime session only after both IDs are known.
7. Builds the Exotel transport through the factory.
8. Closes the socket with `1000` because Phase 1 stops at bootstrap and does not run a pipeline yet.

## Intentional Stub Behavior

The current route closes the WebSocket after bootstrap on purpose.

This is not a bug in Phase 1. It is a placeholder so the router, identity flow, serializer wiring, and runtime session bootstrap could be validated without building Phase 2 early.

Phase 2 should replace this behavior in-place with pipeline handoff logic.

Do not preserve the current post-bootstrap close as if it were final runtime behavior.

## Exact Close Semantics Implemented

The router currently uses these exact close outcomes:

- unsupported provider: `1008`, `"unsupported_provider"`
- missing Exotel identity: `1008`, `"missing_exotel_identity"`
- duplicate `call_sid`: `1008`, `"duplicate_call_sid"`
- successful Phase 1 bootstrap stub close: `1000`

Keep these meanings in mind when modifying the route in Phase 2.

## Pipecat API Decisions Verified

The Phase 1 implementation verified the installed repo venv before coding.

Confirmed working import path in the repo venv:

```python
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
```

Other confirmed Phase 1 Pipecat usage:

- `pipecat.serializers.exotel.ExotelFrameSerializer`
- `pipecat.runner.utils.parse_telephony_websocket`

Project rule going forward:

- For Pipecat-related code, check official Pipecat docs and the installed repo venv API surface before implementing.
- Do not silently switch to alternate import paths if the expected API surface changes. Stop and report the mismatch.

## Important Constraints For Phase 2

- Keep `call_sid` and `stream_sid` naming exactly as-is.
- Keep runtime session data serializable-only.
- Do not store transport, pipeline, or other non-serializable runtime objects inside `ACTIVE_SESSIONS`.
- Preserve `app/runtime.py` as the shared handoff layer between transport, pipeline, and later logging/finalization code.
- Do not move observer registration into `PipelineParams`.
- Keep `SileroVADAnalyzer()` inside `LLMUserAggregatorParams`.
- Keep muted turn-management behavior for no-barge-in flow.

## Recommended Phase 2 Starting Point

Before coding, the next agent must read `PHASE_2_PIPELINE_FLOW.md`, `PHASE_2_STATE_MACHINE.md`, and `PHASE_2_BREAKDOWN.md` once those support documents exist. They define the approved Phase 2 lifecycle and handoff boundaries.

When starting `PHASE_2_PIPELINE.md`, the next agent should:

1. Keep the current route/bootstrap logic.
2. Replace the post-bootstrap `1000` close with pipeline creation and execution.
3. Thread the runtime session through the route-to-pipeline boundary without storing non-serializable objects in `ACTIVE_SESSIONS`.
4. Preserve the existing transport factory unless Phase 2 truly needs a narrow extension.
5. Keep logging and persistence out of Phase 2 except for the runtime state needed by later phases.

## Phase 1 Verification That Was Run

The following non-voice checks were completed locally with the repo venv:

- import sanity for `main`, `app.runtime`, `app.routers.telephony`, and `app.services.transport_factory`
- Pipecat FastAPI transport import verification
- app route registration verification for `/telephony/exotel`
- Exotel serializer construction verification with `16000` Hz values
- route bootstrap sanity for:
  - query-param identity success
  - Exotel parsed payload success
  - unsupported provider close path
  - missing identity close path
  - duplicate `call_sid` close path

No live Exotel, telephony, or voice behavior was claimed or validated.
