# Voice Agent V2 Rebuild Plan

## Purpose

Provide a practical, phased implementation roadmap for Voice Agent V2 that preserves current working telephony behavior while fixing conversation correctness.

## Source Of Truth

- `docs/specs/VOICE_AGENT_V2_SPEC.md`
- `docs/decisions/ADR_*.md`
- `docs/current/ARCHITECTURE.md`
- `docs/current/CONFIGURATION.md`
- `docs/current/RUNTIME_FLOW.md`
- `docs/current/TESTING_AND_DEMO.md`

## Current Problems

- `UserConversationProcessor` in `app/services/pipecat_pipeline.py` currently reacts to finalized `TranscriptionFrame` events directly.
- Conversation policy is largely LLM-driven through `app/services/conversation_controller.py` instead of deterministic state/flow ownership.
- Behavior config still comes from `.env` fields in `app/config.py`.
- Dashboard and logs do not yet expose full slot/state/LLM timing observability required by V2.
- Live provider tests exist (`@pytest.mark.live`) but must be explicitly optional in normal verification flow.

## Target Architecture

```text
Exotel WebSocket
  -> Pipecat audio pipeline
  -> Deepgram Flux STT
  -> TurnCollector
  -> ConversationRuntime
  -> FlowRunner
  -> LLMAdapter
  -> SpeechComposer
  -> Cartesia TTS
  -> Exotel audio output
```

## Phase Overview

- Phase 0: Docs/spec baseline (completed)
- Phase 1: Config/profile foundation + live test optional policy
- Phase 2: TurnCollector
- Phase 3: ConversationRuntime + FlowRunner + TaskState
- Phase 4: LLMAdapter + Metrics
- Phase 5: Demo stabilization
- Future: Pipecat Flows evaluation
- Future: Adaptive low-latency optimization

## Dependency Graph

```text
Phase 0: Docs/spec baseline
  -> Phase 1: Config/profile foundation + optional live test policy
  -> Phase 2: TurnCollector
  -> Phase 3: ConversationRuntime + FlowRunner + TaskState
  -> Phase 4: LLMAdapter + Metrics
  -> Phase 5: Demo stabilization
  -> Future: Pipecat Flows evaluation
  -> Future: Adaptive low-latency optimization
```

Correctness first. Latency second. Speculative latency optimization later.

## Implementation Rules

- Keep sample rate fixed at `16000` and preserve `ExotelFrameSerializer.InputParams(exotel_sample_rate=16000, sample_rate=16000)`.
- Preserve working outbound greeting path and Exotel webhook/WebSocket behavior.
- Preserve immutable call snapshot behavior from call start through final log persistence.
- Do not store live Pipecat task/transport/service objects in runtime registries.
- Do not write every live turn to MongoDB in the hot path.
- Keep normal `pytest` passing without requiring live provider billing/credits.
- Keep live provider tests optional and intentionally invoked.
- Do not implement speculative two-lane latency optimization in V2 baseline phases.

## Testing Strategy

- Unit/integration tests expand phase-by-phase with deterministic assertions.
- Live provider tests remain `@pytest.mark.live` and must not block normal `pytest`.
- Optionalization task for live tests is required before or at the start of Phase 1.
- Each phase must include: model/schema validation, flow correctness checks, and regression checks for outbound path.

## Demo Strategy

- Target one reliable local/ngrok single-call demo.
- Validate committed-turn behavior, slot progression, refusal handling, and fallback safety before latency tuning.
- Report latency honestly (including provider/ngrok effects); do not claim production latency.

## Commit/Review Strategy

- One focused commit per phase sub-scope (models, runtime integration, tests, docs) where practical.
- Keep PRs reviewable with explicit scope boundaries and rollout notes.
- Require acceptance criteria + rollback notes in phase handoff summary.

## Final V2 Acceptance Criteria

- Behavior config is profile-driven and snapshotted per call.
- TurnCollector gates committed turns and raw STT finalized fragments do not directly trigger responses.
- ConversationRuntime + FlowRunner own phase/slot/next-step policy.
- LLMAdapter is bounded, structured, measured, and failure-aware.
- SpeechComposer enforces one-question and safety constraints.
- Dashboard/logs expose transcript, state, latency timeline, provider errors, fallback counts, and completion outcomes.
- Normal `pytest` passes without live provider dependencies.
- Manual single-call demo passes V2 success checklist consistently.