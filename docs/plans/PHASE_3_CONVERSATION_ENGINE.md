# Phase 3: ConversationRuntime And FlowRunner

## Goal

Replace "LLM decides everything every turn" with deterministic state and flow progression owned by the app.

## Why This Phase Exists

After Phase 2, turn commitment is stabilized. This phase ensures policy/state decisions are deterministic and not delegated wholesale to LLM output.

## Scope

- Add `ConversationState` and `SlotState` runtime models.
- Introduce `ConversationRuntime` that owns phase/slot/next-action policy.
- Add `FlowRunner` interface + `SimpleFlowRunner` implementation.
- Add `RepeatedQuestionGuard` and `FallbackPolicy` handling.
- Integrate with committed-turn path from Phase 2.

## Out Of Scope

- Advanced branching graphs and external tool workflows.
- Pipecat Flows runtime integration.
- Full dashboard polish.
- Latency speculative optimization.

## Shared Constraints

- Keep sample rate fixed at `16000`.
- Preserve Exotel webhook/WebSocket behavior and outbound greeting path.
- Preserve immutable config snapshots and serializable runtime state only.
- Do not store live Pipecat objects in runtime registries.
- Do not write every live turn to MongoDB in the hot path.
- Keep live provider tests optional and keep normal `pytest` provider-independent.

## Files Likely Affected

- `app/services/conversation_runtime.py` (new)
- `app/services/flow_runner.py` (new)
- `app/services/task_state.py` (new)
- `app/services/conversation_controller.py` (deprecate/bridge responsibilities)
- `app/services/pipecat_pipeline.py` (wire runtime call path)
- `app/runtime.py` (store serializable conversation state references only)
- `app/models/schemas.py` (optional persisted state event shapes)
- `tests/` for deterministic flow/state behavior

## ConversationState Design

- Owns `phase`, `active_node_id`, `active_slot_id`, slot map, recent transcript events, fallback/refusal counters.
- Must be serializable and safe for runtime session storage.
- Must be initialized from immutable call/profile snapshot.

## SlotState Design

- Per-slot `value`, `status`, `attempts`, confidence/source metadata.
- Status progression supports `missing`, `partial`, `collected`, `skipped`, `confirmed`, `invalid`.

## FlowRunner Design

- `FlowRunner` protocol accepts committed `TurnInput` + current `ConversationState`.
- Returns deterministic next step instructions to `ConversationRuntime`.
- No direct provider/network side effects inside flow runner.

## SimpleFlowRunner Minimum Behavior

- readiness check
- ordered slot collection
- closing node
- refusal path
- fallback path
- one question per turn
- skip optional slot after max attempts
- prevent asking collected/skipped slots again

## RepeatedQuestionGuard

- Validate outgoing question against slot statuses before speech.
- Block asking slots already `collected`, `confirmed`, or `skipped`.
- If blocked, advance to next valid slot/node or close safely.

## FallbackPolicy

- Phase-aware fallback text for readiness/task/closing contexts.
- Deterministic fallback escalation by count.
- Required-slot failure closes or escalates safely after threshold.

## Integration Plan

- Consume committed turns from `TurnCollector`.
- Route turn -> `ConversationRuntime` -> `FlowRunner` -> `TurnResult`.
- Keep existing telephony and outbound greeting path unchanged.
- Keep config snapshots immutable and persisted in final call log.

Example scenario to support:

```text
User: "I am Ojas from Bangalore."
System:
- collect name = Ojas
- collect location = Bangalore
- next question asks role
```

## Testing Plan

- Collect multiple slots from one turn.
- Do not repeat collected slots.
- Correction handling updates prior slot value.
- Refusal/stop intent transitions to closing.
- User question mid-flow handled briefly then returns to slot.
- Max-attempt fallback behavior.
- Optional slot skip.
- Required slot failure closes/escalates safely.

## Acceptance Criteria

- Deterministic phase/slot ownership is app-driven, not LLM-driven.
- One committed user turn yields at most one safe assistant response.
- RepeatedQuestionGuard prevents re-asking completed/skipped slots.
- Existing outbound telephony behavior remains stable.

## Risks

- Overly rigid flow transitions may degrade naturalness.
- State migration from legacy controller may produce edge regressions.
- Slot extraction confidence handling may need Phase 4 tuning.

## Rollback Plan

- Keep bridge to existing `conversation_controller.py` behind explicit switch during rollout.
- Roll back `ConversationRuntime` path per route/pipeline flag if critical regressions surface.

## Handoff To Phase 4

- `ConversationRuntime` emits bounded LLM operations needed per turn.
- Phase 4 implements `LLMAdapter` and full per-operation metrics around those calls.