# PHASE_1_TRANSPORT.md - Telephony Transport

## Phase Gate

`PHASE_0_SETUP.md` must be marked `DONE` in `PHASE_PROGRESS.md` before this phase starts.

## Objective

Implement the FastAPI transport-facing layer for Exotel-style WebSocket communication and isolate telephony entry logic from the future Pipecat pipeline internals.

## Allowed Files

- `main.py`
- `app/routers/telephony.py`
- `app/services/transport_factory.py`
- `app/config.py`
- `app/runtime.py`

## In Scope

- telephony router creation
- WebSocket endpoint structure
- transport factory or equivalent construction helper
- serializer parameter flow
- creation of `app/runtime.py` as the runtime session registry module
- runtime session bootstrap shape
- route wiring into the main app
- hardcoded serializer setup at 16000 Hz
- consistent `call_sid` and `stream_sid` naming

## Out of Scope

- real voice testing
- pipeline orchestration internals
- metrics persistence
- dashboard/config mutation routes

## Acceptance Criteria

- A telephony router exists and is wired into the app.
- Transport creation is isolated from route glue.
- Serializer construction uses `ExotelFrameSerializer.InputParams(exotel_sample_rate=16000, sample_rate=16000)`.
- `app/runtime.py` exists and owns the in-memory session registry used by transport.
- Session bootstrap shape is clear enough for later pipeline and logging phases.
- Route-level flow is readable and does not mix in logging/persistence concerns prematurely.

## Self-Verification

Run normal transport-level checks only, such as:

- import sanity for router and transport modules
- app startup sanity
- route registration verification
- serializer construction sanity with representative parameters

Do not claim that a live Exotel call, handshake, or audio exchange was validated.

## Ask First

- changing WebSocket URL shape after it is defined
- changing the 16000 Hz transport assumption
- combining router logic with later pipeline/logging responsibilities

## Never

- simulate full voice behavior and claim transport is production-ready
- bury serializer inputs inside opaque defaults
- skip explicit handling of runtime session identity such as `call_sid`

## Completion Checklist

- [x] Telephony router exists and is mounted.
- [x] WebSocket entry flow is defined cleanly.
- [x] Transport creation is isolated in a helper or service boundary.
- [x] Serializer uses hardcoded 16000 Hz input and pipeline sample-rate values.
- [x] `app/runtime.py` is created as the single runtime session registry module.
- [x] Runtime session bootstrap structure is present for later phases.
- [x] `call_sid` and `stream_sid` naming is consistent.
- [x] Verification stayed at route and wiring level only.

## Implementation Outcome

Phase 1 has been implemented and verified at wiring level.

Implemented behavior:

- `main.py` mounts the telephony router.
- `app/routers/telephony.py` exposes `WS /telephony/exotel`.
- `app/services/transport_factory.py` builds the Exotel serializer and FastAPI WebSocket transport.
- `app/runtime.py` owns `ACTIVE_SESSIONS` plus typed session/config snapshot helpers.
- WebSocket bootstrap currently closes after successful setup with `1000` as a Phase 1 stub.

For Phase 2 handoff details, read `PHASE_1_IMPLEMENTATION_NOTES.md`.
