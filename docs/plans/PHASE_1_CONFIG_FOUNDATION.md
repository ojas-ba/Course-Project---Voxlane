# Phase 1: Config/Profile Foundation

## Goal

Introduce typed behavior configuration and immutable profile snapshots without breaking the existing outbound call flow.

## Why This Phase Comes First

- Current behavior config is still tied to `.env` in `app/config.py`.
- `build_config_snapshot()` in `app/runtime.py` already captures immutable call config at start; this is a strong base to evolve into `BotProfile`-driven snapshots.
- Later phases (TurnCollector/ConversationRuntime/LLMAdapter) need stable profile/config inputs.
- Live provider tests must become explicitly optional before implementation velocity increases.

## Current Codebase Context

- `app/config.py` defines secrets plus behavior settings (`SYSTEM_PROMPT`, `LLM_MODEL`, `TTS_VOICE_ID`, greeting fields).
- `app/runtime.py` stores `ACTIVE_SESSIONS` and `config_snapshot`, plus pending outbound context.
- `app/routers/dashboard.py` currently builds a snapshot from settings and prepares outbound greeting.
- `app/routers/telephony.py` consumes pending context snapshot and persists final call log.
- `app/models/schemas.py` currently defines call logging schemas, not profile/flow/provider config models.
- Live tests are marked with `@pytest.mark.live` (e.g., `tests/test_live_*.py`) and need policy enforcement so default `pytest` remains provider-independent.

## Scope

- Add typed models for behavior config foundation.
- Add default seeded behavior configs and one default profile/flow.
- Route outbound call preparation through selected/default profile snapshot.
- Preserve immutable snapshot behavior for active/completed calls.
- Add minimal dashboard visibility/selection for profile used in outbound call.
- Add/confirm live-test optional policy in test configuration/docs.

## Out Of Scope

- TurnCollector implementation.
- ConversationRuntime rewrite.
- Pipecat pipeline rewrite.
- Pipecat Flows integration.
- Full flow builder UI or polished config UI.
- Sub-1s latency optimization.

## Shared Constraints

- Keep sample rate fixed at `16000`.
- Preserve Exotel webhook/WebSocket behavior and outbound greeting path.
- Preserve immutable config snapshots for active/completed calls.
- Do not store live Pipecat objects in runtime registries.
- Keep live provider tests optional and keep normal `pytest` provider-independent.
- Do not implement speculative two-lane latency optimization in this phase.

## Files Likely Affected

- `app/models/schemas.py` (or new `app/models/profile_schemas.py`) for typed profile/flow/provider models.
- `app/runtime.py` for expanded snapshot shape and selected profile metadata.
- `app/config.py` for compatibility defaults and env fallback strategy.
- `app/routers/dashboard.py` for minimal profile selection and outbound snapshot sourcing.
- `app/routers/telephony.py` to preserve snapshot handoff/persistence path.
- `app/templates/dashboard.html` for minimal profile selector/debug snapshot display.
- `tests/` for model validation + snapshot immutability + optional live test policy.

## Data Models To Add

- `BotProfile`
- `FlowDefinition`
- `SlotDefinition`
- `FlowNode`
- `STTConfig`
- `LLMConfig`
- `TTSConfig`
- `GreetingConfig`
- `FallbackPolicy`

## Storage Strategy

- Start with simple in-repo/app-local storage abstraction for V2 prototype (e.g., seeded in-memory or JSON-backed config repository).
- Keep provider secrets in `.env`; do not move secrets to UI storage.
- Persist selected profile/config IDs and resolved snapshot in runtime/call log, not raw mutable references.

## Default Seeded Configs

- `Default Demo Bot`
- Default Deepgram Flux STT config
- Default Cerebras LLM config
- Default Cartesia TTS config
- Default static greeting config
- Default `FlowDefinition` with slots:
  - `name`
  - `location`
  - `role`

## Minimal Dashboard Changes

- Show current selected/default `BotProfile` on dialer view.
- Allow selecting `BotProfile` for outbound demo call if wiring is simple.
- Show profile/config snapshot metadata in log detail/debug area.

## Runtime Snapshot Changes

Transition rule:

```text
.env values -> default seeded BotProfile / ProviderConfig / FlowDefinition -> immutable call snapshot
```

- Snapshot must be resolved at call start and remain immutable during live call.
- Snapshot used for call must be stored in final call log.

## Backward Compatibility Plan

- Keep existing `.env` behavior as fallback while profile foundation is introduced.
- If no profile is selected/found, use `Default Demo Bot` seeded config.
- Do not remove existing outbound greeting path (`generate_outbound_greeting_text`) in this phase.

## Testing Plan

- Model validation tests for new profile/flow/provider schemas.
- Default seeded config creation tests.
- Snapshot immutability tests (UI/profile changes do not mutate active session snapshot).
- Outbound route tests verify selected/default profile snapshot is used.
- Live test policy tests/documentation updates so normal `pytest` does not depend on provider billing/quota.

## Acceptance Criteria

- Typed config/profile/flow models exist and validate correctly.
- Default seeded profile/config/flow set exists and can drive outbound flow.
- Outbound path still works with selected/default profile snapshot.
- Active-call snapshot immutability is preserved.
- Normal `pytest` path is independent from live provider billing/credits.

## Risks

- Snapshot migration may accidentally break call log compatibility if shape changes abruptly.
- UI selection state may drift from server-side selected profile if not snapshotted carefully.
- Over-scoping into full UI editor can delay Phase 2.

## Rollback Plan

- Feature-flag or fallback to existing `.env`-only snapshot build path.
- Keep old snapshot keys until call log consumers support expanded shape.
- Revert dashboard selector while retaining backend default profile resolution.

## Handoff To Phase 2

- Provide stable `BotProfile` + config snapshot contract for `TurnCollector` integration.
- Document where committed `TurnInput` will read phase/flow/slot context from snapshot/state bootstrap.
