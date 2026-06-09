# AGENTS.md - Voice Agent Global Rules

Active documentation lives under [docs/](docs/) — see [docs/README.md](docs/README.md) for the index.
Start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/LATENCY.md](docs/LATENCY.md), and
[docs/METRICS.md](docs/METRICS.md). Accepted decisions are in [docs/decisions/](docs/decisions/).

## Mission

Help build a local-development, open-ended multilingual voice agent using Pipecat, Exotel
telephony, Deepgram Flux, Cerebras, Cartesia, FastAPI, and MongoDB persistence.

The human handles real voice, Exotel, telephony, and latency validation manually. The AI owns
code quality, architecture adherence, type-safe implementation, structured logging, and normal
non-voice verification. Never claim the pipeline "works" from unit tests — a real Exotel live
call is the only proof.

## Operating Model

Implement in small, reviewable, test-backed slices. Don't pre-build speculative future work. If a blocker
materially affects the design, document it and stop for review. Never claim voice behavior "works" from
unit tests — a real Exotel live call is the only proof.

## Boundaries

### Always

- Python 3.11+ conventions and modern typing; type hints on public functions/classes/data.
- Pydantic for configuration and schema models.
- Keep telephony logic isolated from logging, schema, and config code.
- Exotel transport and pipeline sample rate fixed at 16000 Hz (see `docs/decisions/ADR_001`).
  Do not introduce dynamic sample-rate configuration unless the human changes the requirement.
- Build transport with `ExotelFrameSerializer.InputParams(exotel_sample_rate=16000, sample_rate=16000)`.
- Configure Pipecat services through typed `.Settings()` objects.
- Register observers on `PipelineTask(..., observers=[...])`, not inside `PipelineParams`.
- Include `SileroVADAnalyzer()` in user-aggregator params even without barge-in, because latency
  observers depend on timing frames.
- Use `UserBotLatencyObserver` for latency capture.
- On `WebSocketDisconnect`, await `task.cancel()`, let the runner unwind, then `finalize_call_log()`.
- Use `stream_sid` and `call_sid` consistently (never `stream_id` or alternate names).
- `app/runtime.py` is the single source of truth for the runtime session registry
  (`ACTIVE_SESSIONS`) and session-state helpers. Store only JSON-safe data there — never live
  Pipecat/provider objects.
- Tuning/behavior config is runtime-configurable (MongoDB-backed, frontend-editable) and
  snapshotted per call; only secrets live in `.env` (see `docs/decisions/ADR_002`).

### Ask First

- Adding dependencies not already present.
- Changing public route structure, the persistence shape, or observer registration.
- Changing the 16000 Hz transport/pipeline assumption.
- Introducing background workers, queues, or infra not already planned.
- Cross-cutting refactors beyond the current phase scope.

### Never

- Commit secrets, tokens, or real `.env` values. Edit only `.env.example`, never `.env`.
- Invent fake "successful" voice tests or claim Exotel behavior was validated automatically.
- Register observers inside `PipelineParams`.
- Place tuning/behavior config in `.env` (secrets only).

## Testing Policy

The human owns: real Exotel validation, live telephony behavior, voice quality, latency realism,
tunnel/webhook operational testing.

The AI owns: project structure, config/schema correctness, import/module wiring sanity, route/flow
verification, serializer construction, logging payload shape, and persistence-model shape.

## Quality Standards

- Favor explicit naming; keep side effects easy to trace.
- Prefer small focused modules over broad files.
- Avoid hidden global behavior except deliberately shared runtime registries.
- Brief docstrings on public APIs; comments only where they reduce ambiguity.
