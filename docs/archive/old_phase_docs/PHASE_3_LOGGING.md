# PHASE_3_LOGGING.md - Logging, Schemas, and Finalization

## Phase Gate

`PHASE_2_PIPELINE.md` must be marked `DONE` in `PHASE_PROGRESS.md` before this phase starts.

## Objective

Add structured logging, typed call/turn schemas, and call-finalization helpers so runtime behavior can be captured cleanly without mixing persistence concerns throughout the transport and pipeline layers.

## Allowed Files

- `app/models/schemas.py`
- `app/utils/metrics_logger.py`
- `app/logging_config.py`
- `app/services/pipecat_pipeline.py`
- `app/database.py`
- `app/runtime.py`
- `main.py`

## In Scope

- typed turn and call models
- structured logging setup
- per-call correlation strategy
- finalize-call helper flow
- persistence-safe serialization shape
- pipeline callback attachment needed for metrics capture
- normalized latency-event storage shape

## Out of Scope

- dashboard/config routes
- live database ops validation beyond normal code-level checks
- real Exotel or voice metrics validation
- performance tuning

## Acceptance Criteria

- Typed schemas exist for turn-level and call-level data.
- Structured logs are configured with clear per-call correlation support.
- Finalization logic has a defined path from runtime session state to persistence shape.
- Pipeline or runtime callbacks can attach metrics into session state without breaking separation of concerns.
- `breakdown.chronological_events()` output is transformed before persistence into dictionaries shaped exactly like `{"time": float, "event": str}` as the required persistence shape.
- Logging and schema code is clear enough for later tests to validate shape without live telephony.

## Self-Verification

Run normal logging/schema checks only, such as:

- schema instantiation sanity
- serialization sanity
- import sanity for logging helpers
- finalize-call flow checks using controlled mock-like runtime data

Do not claim real latency, call duration realism, or live persistence behavior was validated through telephony.

## Ask First

- changing schema shapes after they are established
- changing persistence ownership boundaries
- introducing logging side effects that alter runtime flow materially

## Never

- store non-serializable runtime objects inside persisted payloads
- persist raw observer-event objects without normalization
- treat manual telephony metrics as automated proof
- spread logging and persistence behavior across unrelated modules without clear ownership

## Completion Checklist

- [x] Typed turn and call schemas exist.
- [x] Structured logging configuration exists.
- [x] Per-call correlation strategy includes `call_sid`.
- [x] Finalization helper path is defined clearly.
- [x] Runtime/pipeline metrics capture hooks are attached in a maintainable way.
- [x] Latency events are normalized into dictionaries shaped exactly like `{"time": float, "event": str}`.
- [x] Verification stayed at schema, logging, and flow-shape level only.
