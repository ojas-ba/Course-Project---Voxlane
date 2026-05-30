# AGENTS.md - Voice Agent Global Rules

## Mission

You are an AI software engineer helping build a local-development voice agent system that uses Pipecat, Exotel telephony, Deepgram, Cerebras, Cartesia, FastAPI, and MongoDB-style persistence patterns.

The human will handle real voice, Exotel, and telephony validation manually. Your responsibility is code quality, architecture adherence, type-safe implementation, structured logging, clean phase execution, and normal non-voice verification.

## Operating Model

This repository follows a spec-first, phase-gated workflow.

- Read `PROJECT_SPEC.md` for product intent and constraints.
- Read `TECHNICAL_PLAN.md` for implementation architecture before coding.
- Read the current `PHASE_*.md` file before touching code.
- Read `PHASE_PROGRESS.md` before starting any new phase.
- Update `NOTES.md` after meaningful implementation decisions or discoveries.

## Phase Gating Rules

- Work must happen in strict phase order.
- Do not start a phase unless the previous phase is marked `DONE` in `PHASE_PROGRESS.md`.
- Implement only the current phase's scope.
- Do not pre-build future-phase code "for convenience".
- If the current phase exposes a blocker that materially affects later phases, document it in `NOTES.md` and stop for review.

## Boundaries

### Always

- Use Python 3.11+ conventions and modern typing.
- Add type hints to public functions, classes, and data structures.
- Use Pydantic for configuration and schema models.
- Keep code modular and phase-appropriate.
- Prefer clear, boring, production-friendly structure over clever abstractions.
- Keep telephony-specific logic isolated from logging, schema, and config code.
- Hardcode Exotel transport and pipeline sample rate to 16000 Hz for this v1 architecture.
- Treat 16000 Hz as fixed and non-configurable in v1. Do not introduce dynamic sample-rate configuration unless the human explicitly changes the requirement.
- Use structured logging and include `call_sid` wherever call-scoped logs are emitted.
- Use `stream_sid` and `call_sid` consistently. Do not introduce `stream_id` or alternate naming for the same concepts.
- Build transport with `ExotelFrameSerializer.InputParams(exotel_sample_rate=16000, sample_rate=16000)`.
- Configure Pipecat services through typed `.Settings()` objects.
- Register observers directly on `PipelineTask(..., observers=[...])`, not inside `PipelineParams`.
- Use muted turn-management with `LLMUserAggregatorParams.user_mute_strategies=...` so user audio is dropped while the bot speaks.
- Include `SileroVADAnalyzer()` in `LLMUserAggregatorParams` even with no barge-in, because latency observers depend on timing frames.
- Use `UserBotLatencyObserver` for metrics capture.
- Normalize `breakdown.chronological_events()` into JSON-friendly dictionaries shaped like `{"time": float, "event": str}` before persisting them.
- On `WebSocketDisconnect`, gracefully await `task.cancel()`, let the pipeline runner unwind, and only then call `finalize_call_log()`.
- Write normal verification tests only:
  - config loading
  - schema validation
  - import sanity
  - router and flow checks
  - serializer wiring
  - logging and persistence shape validation
- Use `app/runtime.py` as the single source of truth for runtime session registry and session-state helper functions, including `ACTIVE_SESSIONS`.

### Ask First

- Adding dependencies not already approved in the setup phase.
- Changing public route structure after transport is defined.
- Changing the persistence shape after schemas are agreed.
- Changing how observer registration works.
- Changing 16000 Hz transport/pipeline assumptions for v1.
- Introducing background workers, queues, or infra not already in spec.
- Making cross-phase refactors that exceed the current phase scope.

### Never

- Commit secrets, tokens, or real `.env` values.
- Edit `.env` directly; only create or modify `.env.example`.
- Invent fake "successful" voice tests or claim Exotel behavior was validated automatically.
- Build real telephony/audio integration tests in this repo workflow unless the human explicitly asks.
- Register observers inside `PipelineParams`.
- Omit `SileroVADAnalyzer()` from muted-turn handling if latency metrics are required.
- Skip a phase checklist just because the code "looks correct".
- Modify future-phase files while completing the current phase unless the phase explicitly allows it.

## Testing Policy

The human owns:

- real Exotel validation
- live telephony behavior
- actual voice quality
- latency realism under real call conditions
- tunnel and webhook operational testing

The AI owns:

- project structure correctness
- config and schema correctness
- import and module wiring sanity
- route and flow verification
- serializer construction and parameter flow
- logging payload shape
- persistence-model shape validation

## Quality Standards

- Favor explicit naming over shorthand.
- Keep side effects easy to trace.
- Prefer small focused modules over overly broad files.
- Avoid hidden global behavior except for deliberately shared runtime registries.
- Add brief docstrings to public APIs.
- Write comments only where they reduce ambiguity.

## Session Workflow

For each implementation session:

1. Read `AGENTS.md`.
2. Read `PROJECT_SPEC.md`.
3. Read `TECHNICAL_PLAN.md`.
4. Read the active `PHASE_*.md`.
5. Confirm the active phase is allowed by `PHASE_PROGRESS.md`.
6. Implement only that phase.
7. Run only the phase's allowed verification steps.
8. Update `PHASE_PROGRESS.md`.
9. Update `NOTES.md`.
10. Stop after phase completion for human review.

## Definition of Done

A phase is only done when:

- all in-scope files exist or are updated correctly
- all checklist items are satisfied
- allowed verification steps have been run
- no known blocking inconsistency remains undocumented
- `PHASE_PROGRESS.md` and `NOTES.md` are updated

## File Authority

- `AGENTS.md` is the standing behavioral contract.
- `PROJECT_SPEC.md` is the product intent contract.
- `TECHNICAL_PLAN.md` is the implementation blueprint.
- `PHASE_*.md` files are the execution contracts.
- `PHASE_PROGRESS.md` is the phase-gating source of truth.
- `NOTES.md` is the persistent working memory.
