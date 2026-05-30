# TECHNICAL_PLAN.md - Implementation Blueprint

## Purpose

This file is the architecture blueprint that must be reviewed before phase execution begins. It should be refined as the implementation plan becomes clearer, but it must remain aligned with `PROJECT_SPEC.md` and the phase contracts.

## Current Status

Architecture constraints for v1 are now fixed and should be treated as implementation requirements, not open questions.

## Phase 2 Documentation Support

Complex phases may require support documents before implementation begins so lifecycle and subsystem boundaries are agreed before code changes are made.

For Phase 2, the required support documents are:

- `PHASE_2_PIPELINE_FLOW.md`
- `PHASE_2_STATE_MACHINE.md`
- `PHASE_2_BREAKDOWN.md`

These documents lock the call lifecycle, router-to-pipeline boundary, runtime state constraints, and internal execution sequence before the Phase 2 coding pass begins. They do not replace the official phase file or create additional official phases; they refine execution inside `PHASE_2_PIPELINE.md`.

## Proposed Repository Layout

```text
.
├── .env.example
├── .gitignore
├── pyproject.toml
├── main.py
├── README.md
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── database.py
│   ├── runtime.py
│   ├── logging_config.py
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── telephony.py
│   │   └── dashboard.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── pipecat_pipeline.py
│   │   └── transport_factory.py
│   └── utils/
│       ├── __init__.py
│       └── metrics_logger.py
└── tests/
    ├── test_config.py
    ├── test_logging.py
    ├── test_models.py
    └── test_transport.py
```

## Planned Architecture

### Application Layer

- FastAPI app as the main runtime entrypoint
- router registration for telephony and dashboard/config surfaces
- startup wiring for logging and persistence setup

### Configuration Layer

- Pydantic-based settings object
- environment-driven configuration
- safe separation between committed examples and local secrets
- support for runtime snapshots where call-scoped configuration must remain stable during a live session
- Active-call configurations are strictly immutable.
- When a call begins, it performs a deep-copy read of the current configuration to generate its `config_snapshot`.
- Mutations through any `/dashboard/config` route may update global configuration for future calls only.
- No dashboard/config mutation may affect a live session already stored in `ACTIVE_SESSIONS`.
- `app/runtime.py` owns the in-memory `ACTIVE_SESSIONS` registry keyed by `call_sid`.
- Runtime session state for active calls must live in `app/runtime.py`, not inside `app/config.py`.

### Runtime State Layer

- `app/runtime.py` is required and is not optional
- `app/runtime.py` owns the in-memory session registry and helper functions for session creation, lookup, update, and cleanup
- `ACTIVE_SESSIONS` lives in `app/runtime.py`, not in `app/config.py`
- session state in `app/runtime.py` is the shared handoff surface between transport, pipeline, logging, and integration teardown
- each session record must support:
  - `call_sid`
  - `stream_sid`
  - `metrics`
  - `transcript`
  - `first_latency`
  - `config_snapshot`

### Transport Layer

- FastAPI WebSocket endpoint for Exotel-facing transport
- The Exotel WebSocket route for v1 is fixed as: `WS /telephony/exotel`
- The route handler must extract `CallSid` and `StreamSid`, mapping them internally to snake_case variables `call_sid` and `stream_sid`.
- Those IDs may come from either the initial query string or the first incoming text framing payload, depending on Exotel metadata delivery.
- `ExotelFrameSerializer` initialized with `ExotelFrameSerializer.InputParams(exotel_sample_rate=16000, sample_rate=16000)`
- transport creation isolated from pipeline orchestration
- Transport creation must remain isolated from router glue.
- Serializer construction remains fixed to `ExotelFrameSerializer.InputParams(exotel_sample_rate=16000, sample_rate=16000)`.
- route-level handling centered on `call_sid` and `stream_sid`

### Pipeline Layer

- Pipecat pipeline service responsible for orchestrating:
  - transport frames
  - STT
  - LLM
  - TTS
  - VAD/observer utilities required for metrics
- clear interface between router entrypoint and pipeline runner
- service configs passed through typed `.Settings()` objects
- turn management disables barge-in by dropping user audio while the bot is speaking
- `LLMUserAggregatorParams` must include `SileroVADAnalyzer()` so timing frames are still emitted
- observers are registered on `PipelineTask(..., observers=[latency_observer])`
- `SileroVADAnalyzer()` must be passed inside `LLMUserAggregatorParams` for timing-frame emission and must not be introduced as a standalone pipeline stage.
- Observer registration remains fixed to `PipelineTask(..., observers=[latency_observer])` and must not be moved into `PipelineParams`.
- Phase 2 implementation begins only after `PHASE_2_PIPELINE_FLOW.md`, `PHASE_2_STATE_MACHINE.md`, and `PHASE_2_BREAKDOWN.md` have defined the intended service and lifecycle boundaries.
- The router-to-pipeline handoff replaces the Phase 1 successful bootstrap `1000` close; it must not replace established identity resolution, serializable session bootstrap, or fixed transport behavior.
- Live transport, pipeline, task, runner, observer, and service objects must not be stored inside `ACTIVE_SESSIONS`.

### Logging and Metrics Layer

- structured logs using `loguru`
- per-call correlation using `call_sid`
- turn and call metrics captured in typed schemas
- clean finalize path for persisting call outcomes
- `UserBotLatencyObserver` captures latency breakdowns
- raw chronological events are normalized into JSON-friendly dictionaries shaped exactly like `{"time": float, "event": str}` before storage
- `breakdown.chronological_events()` must be normalized before persistence into JSON-friendly dictionaries shaped exactly like `{"time": float, "event": str}`.

### Persistence Layer

- database access isolated from business logic
- storage model shaped by Pydantic schemas
- finalization helpers that serialize call data into a persistence-safe structure
- `finalize_call_log()` runs only after graceful teardown completes

### Test Layer

- normal non-voice tests only
- no real Exotel or live audio assertions
- focus on module wiring, schemas, config, routes, serializer setup, and logging/persistence shape

## Design Rules

- Keep file responsibilities narrow and phase-specific.
- Avoid premature abstractions.
- Prefer stable interfaces between subsystems.
- Capture open implementation decisions in `NOTES.md` rather than improvising inconsistently across files.
- Do not reopen fixed v1 decisions unless the human explicitly changes them.

## Pre-Execution Checklist

Before phase implementation begins, this file should explicitly document:

- proposed repository layout
- expected key modules and responsibilities
- chosen observer registration pattern
- logging and persistence ownership boundaries
- how runtime session state is stored and finalized
- what verification is acceptable without live telephony
- graceful `WebSocketDisconnect` cancellation and finalization sequence

## Runtime Session Lifecycle

- WebSocket accepted
- bind `call_sid` and `stream_sid`
- bootstrap session into `ACTIVE_SESSIONS` with immutable `config_snapshot`
- construct transport
- construct pipeline
- run pipeline
- active call processing
- catch `WebSocketDisconnect`
- await `task.cancel()`
- let pipeline runner unwind
- call `finalize_call_log()`
- remove session from `ACTIVE_SESSIONS`

The lifecycle description above is a structural contract, not a requirement to use exact helper names like `init_session()` or `cleanup_session()`.

The implementation may use different helper function names as long as the lifecycle order is preserved.

For Phase 2, pipeline implementation reaches and preserves the later `finalize_call_log()` boundary; logging and persistence implementation remains Phase 3 scope. Phase 1's current successful-bootstrap `1000` close is temporary and is replaced by pipeline execution during the later Phase 2 coding pass.

See `PHASE_2_PIPELINE_FLOW.md` and `PHASE_2_STATE_MACHINE.md` for the implementation-facing lifecycle detail.

## Fixed v1 Decisions

- Exact repo layout is fixed as documented above.
- Expected module names are fixed as documented in the repository layout.
- Transport sample rate: `16000`
- Exotel serializer input sample rate: `16000`
- Sample rate configurability: not configurable in v1
- STT/LLM/TTS service configuration style: typed `.Settings()` objects
- Observer registration: `PipelineTask(..., observers=[latency_observer])`
- Turn management: muted while bot speaks
- VAD requirement: `SileroVADAnalyzer()` is passed as a named parameter inside `LLMUserAggregatorParams` and remains enabled for timing frames even without barge-in
- Metrics normalization: `breakdown.chronological_events()` must become `[{"time": float, "event": str}]`
- Active-call config mutation policy is fixed: live calls are immutable through `config_snapshot`.
- Teardown order: catch `WebSocketDisconnect` -> await `task.cancel()` -> let runner finish -> call `finalize_call_log()`
- Runtime registry ownership: `app/runtime.py` owns `ACTIVE_SESSIONS` and related helper functions
- Finalize-call lifecycle path is fixed by the runtime session lifecycle defined above.
- WebSocket route path is fixed as `WS /telephony/exotel`.
- Snapshot shape at call start: `session["config_snapshot"] = {"SYSTEM_PROMPT": ..., "LLM_MODEL": ..., "LLM_TEMPERATURE": ..., "TTS_VOICE_ID": ..., "TTS_MODEL": ..., "INITIAL_GREETING_PROMPT": ..., "SAMPLE_RATE": 16000}`

## Open Items To Refine

- ~~persistence target schema details once schema definitions are implemented~~ — Resolved in Phase 3: `CallLog`, `CallMetrics`, `TurnMetrics`, `LatencyEvent` in `app/models/schemas.py`
