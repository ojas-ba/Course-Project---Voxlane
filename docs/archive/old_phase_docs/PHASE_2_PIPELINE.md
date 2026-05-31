# PHASE_2_PIPELINE.md - Pipecat Pipeline Wiring

## Phase Gate

`PHASE_1_TRANSPORT.md` must be marked `DONE` in `PHASE_PROGRESS.md` before Phase 2 implementation starts.

The preparatory documentation pass defined by this file may be completed while `PHASE_2_PIPELINE.md` remains `NOT STARTED`. It does not constitute pipeline implementation or phase completion.

## Objective

Prepare and later implement the core Pipecat pipeline orchestration layer so the established Exotel transport route can hand off to a structured STT -> LLM -> TTS flow with clear lifecycle, runtime-state, and observer boundaries.

Phase 2 is intentionally split into documentation preparation followed by a later implementation session. The current preparation work must make the coding pass bounded and mechanical rather than architecture-defining.

## Required Pre-Read Files

Before changing Phase 2 documentation or implementing Phase 2 code, read:

- `AGENTS.md`
- `PROJECT_SPEC.md`
- `TECHNICAL_PLAN.md`
- `PHASE_PROGRESS.md`
- `PHASE_1_TRANSPORT.md`
- `PHASE_1_IMPLEMENTATION_NOTES.md`
- `NOTES.md`

Before implementation, also read the completed Phase 2 support documents:

- `PHASE_2_PIPELINE_FLOW.md`
- `PHASE_2_STATE_MACHINE.md`
- `PHASE_2_BREAKDOWN.md`

## Documentation-First Rule

Phase 2 must begin by creating or updating its support documents before any pipeline implementation is attempted.

The agent must not jump directly from the Phase 1 bootstrap route into pipeline code without first aligning the Phase 2 flow, lifecycle, state, and execution-breakdown documents.

The purpose of this rule is to reduce lifecycle ambiguity, preserve established runtime and transport boundaries, and prevent architectural improvisation while replacing the Phase 1 bootstrap stub.

## Internal Execution Order

Complete Phase 2 work in this order:

1. Review Phase 1 handoff and current runtime boundaries.
2. Create or update Phase 2 support docs.
3. Lock the Phase 2 runtime lifecycle and state transitions in documentation.
4. Confirm pipeline/router handoff boundaries.
5. Only then begin implementation work in a later execution session.

### Internal Phase 2 Breakdown

The following chunks organize work inside Phase 2. They are not official repository phases and must not be added to `PHASE_PROGRESS.md` as separate entries.

- Phase 2A: Flow and lifecycle documentation.
- Phase 2B: Runtime-to-pipeline interface definition.
- Phase 2C: Pipecat service assembly plan.
- Phase 2D: Observer/VAD/turn-management wiring plan.
- Phase 2E: Route handoff replacement plan.

See `PHASE_2_BREAKDOWN.md` for the implementation-facing checklist.

## Allowed Files For This Phase

Documentation preparation may update or create:

- `PHASE_2_PIPELINE.md`
- `PHASE_2_PIPELINE_FLOW.md`
- `PHASE_2_STATE_MACHINE.md`
- `PHASE_2_BREAKDOWN.md`
- `TECHNICAL_PLAN.md`
- `NOTES.md`
- `PHASE_1_IMPLEMENTATION_NOTES.md` only for a narrow handoff cross-reference

After the documentation pass is reviewed, the later Phase 2 implementation session may update:

- `app/services/pipecat_pipeline.py`
- `app/services/transport_factory.py` only if a narrow pipeline handoff extension is required
- `app/routers/telephony.py`
- `app/config.py` only for already-planned pipeline service configuration
- `app/runtime.py`

For `app/runtime.py`, changes are permitted only to extend existing serializable session update helpers if pipeline execution requires writing phase-appropriate data such as transcript or metrics fields. The `ACTIVE_SESSIONS` registry ownership model and helper function naming convention must not change.

Documentation changes must come first. This file does not authorize code implementation during a documentation-only preparation session.

## In Scope

- defining the router-to-pipeline boundary
- documenting the call lifecycle
- documenting runtime session state transitions
- documenting Pipecat assembly expectations
- documenting observer/VAD/turn-management constraints
- preparing Phase 2 for a later coding pass
- in the later implementation session, replacing the temporary bootstrap close with pipeline handoff and execution
- in the later implementation session, assembling STT, LLM, and TTS services using typed `.Settings()` objects

## Out of Scope

- live Exotel validation
- real voice or audio-quality validation
- persistence finalization logic
- dashboard/config mutation work
- production telephony claims
- logging/persistence phase work beyond what is needed to explain interfaces
- changing fixed v1 transport, observer, teardown, or runtime ownership decisions

## Acceptance Criteria

- `PHASE_2_PIPELINE_FLOW.md`, `PHASE_2_STATE_MACHINE.md`, and `PHASE_2_BREAKDOWN.md` exist and are internally consistent with this phase contract and `TECHNICAL_PLAN.md`.
- The route bootstrap-to-pipeline replacement path is clearly documented.
- The runtime lifecycle is documented from WebSocket accept through disconnect teardown.
- The Phase 1 `1000` close after successful bootstrap is explicitly identified as temporary behavior to be replaced during Phase 2 implementation.
- `ACTIVE_SESSIONS` remains owned by `app/runtime.py` and is documented as serializable-only; transport, pipeline, and task objects are not stored there.
- Observer and VAD rules remain aligned with `AGENTS.md` and `TECHNICAL_PLAN.md`: observer registration on `PipelineTask(..., observers=[...])` and `SileroVADAnalyzer()` inside `LLMUserAggregatorParams`.
- The later coding pass has a clear boundary for replacing route bootstrap behavior without beginning Phase 3 persistence implementation.

## Self-Verification

For this documentation pass:

- compare Phase 2 support docs against `AGENTS.md`, `TECHNICAL_PLAN.md`, and `PHASE_1_IMPLEMENTATION_NOTES.md`
- confirm each document uses `call_sid`, `stream_sid`, `ACTIVE_SESSIONS`, and `config_snapshot` consistently
- confirm no document claims Phase 2 implementation or live validation has happened
- confirm `PHASE_PROGRESS.md` still leaves Phase 2 not complete

For the later implementation pass, run normal pipeline-level checks only, such as:

- import sanity for pipeline modules
- service construction sanity
- route-to-pipeline call-path verification
- observer registration sanity without live call execution

Do not claim STT, LLM, TTS, Exotel, or voice behavior was validated against live telephony.

## Ask First

- changing the route structure fixed as `WS /telephony/exotel`
- changing `16000` Hz transport or pipeline assumptions
- changing observer registration away from `PipelineTask(..., observers=[...])`
- changing the serializable runtime session shape or `app/runtime.py` ownership boundary
- implementing persistence finalization or dashboard/config mutation work in Phase 2
- replacing muted/non-barge-in assumptions without explicit approval

## Never

- begin Phase 2 pipeline implementation before its support docs are aligned
- store transport, pipeline, task, observer, or other non-serializable runtime objects in `ACTIVE_SESSIONS`
- preserve the Phase 1 post-bootstrap `1000` close as final pipeline behavior
- register observers inside `PipelineParams`
- omit `SileroVADAnalyzer()` from `LLMUserAggregatorParams` when latency timing is required
- present mocked or structural validation as real telephony success
- mark Phase 2 complete from documentation preparation alone

## Completion Checklist

### Documentation Preparation

- [x] Phase 1 handoff and current runtime boundaries have been reviewed.
- [x] `PHASE_2_PIPELINE_FLOW.md` defines end-to-end flow and route/pipeline boundaries.
- [x] `PHASE_2_STATE_MACHINE.md` defines lifecycle transitions and guardrails.
- [x] `PHASE_2_BREAKDOWN.md` defines internal planning chunks without altering official phase tracking.
- [x] `TECHNICAL_PLAN.md` recognizes the Phase 2 support documents.
- [x] `NOTES.md` records the documentation-first working decision.
- [x] No Phase 2 runtime code was implemented during this documentation pass.

### Later Implementation

- [x] Dedicated pipeline service exists.
- [x] Route-to-pipeline handoff replaces the temporary Phase 1 success close.
- [x] Observer registration uses `PipelineTask(..., observers=[latency_observer])`.
- [x] Runtime session linkage needed for later metrics is preserved without storing runtime objects.
- [x] User audio is muted/dropped while the bot speaks.
- [x] `SileroVADAnalyzer()` is passed inside `LLMUserAggregatorParams` for timing-frame emission.
- [x] STT, LLM, and TTS use typed `.Settings()` configuration.
- [x] Verification stays at structure and wiring level only.

## Handoff Expectations

After this documentation pass is reviewed, the next implementation session may begin actual Phase 2 coding.

The next agent must read and reuse `PHASE_2_PIPELINE_FLOW.md`, `PHASE_2_STATE_MACHINE.md`, and `PHASE_2_BREAKDOWN.md` rather than redefining Phase 2 architecture from scratch. The implementation session must preserve the Phase 1 transport and runtime boundaries unless a documented inconsistency requires human review.
