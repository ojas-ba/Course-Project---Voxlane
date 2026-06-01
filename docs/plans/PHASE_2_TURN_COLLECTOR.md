# Phase 2: TurnCollector

## Goal

Introduce `TurnCollector` so only committed user turns trigger assistant responses.

## Why This Phase Exists

Current pipeline flow in `app/services/pipecat_pipeline.py` handles finalized `TranscriptionFrame` directly in legacy `UserConversationProcessor`, which can respond too early.

## Current Problem

```text
TranscriptionFrame.finalized
  -> legacy UserConversationProcessor
  -> decide_next_turn()
  -> bot replies
```

This bypasses committed-turn policy and can react to partial/unstable user speech.

## Scope

- Add `app/services/turn_collector.py` with `TurnCollector` that emits exactly one committed `TurnInput` per accepted turn.
- Integrate TurnCollector into pipeline path before response generation.
- Gate response generation on committed turn events, not raw finalized fragments.
- Keep conservative commit behavior (correctness > speed).

## Out Of Scope

- Full ConversationRuntime replacement.
- FlowRunner redesign.
- LLMAdapter redesign.
- Latency speculative response strategies.

## Shared Constraints

- Keep sample rate fixed at `16000`.
- Preserve Exotel webhook/WebSocket behavior and outbound greeting path.
- Preserve immutable config snapshots and serializable runtime state only.
- Do not store live Pipecat objects in runtime registries.
- Do not write every live turn to MongoDB in the hot path.
- Keep live provider tests optional and keep normal `pytest` provider-independent.

## Files Likely Affected

- `app/services/turn_collector.py` (new)
- `app/services/pipecat_pipeline.py` (replace raw finalized-trigger path)
- `app/services/conversation_controller.py` (temporary bridge target only)
- `app/runtime.py` (optional turn counters/phase-safe gating fields)
- `tests/` (TurnCollector + pipeline integration tests)

## TurnCollector Responsibilities

- Accept STT/Flux events and transcript fragments.
- Commit turns only on valid committed signal (Flux `EndOfTurn` in V2).
- Emit normalized `TurnInput` payloads.
- De-duplicate repeated fragments/events.
- Ignore empty/noise-only utterances.
- Respect greeting-pending/phase gating.

## Event Handling Strategy

V2 conservative strategy:

- `EndOfTurn`: commit user turn.
- `Update`: transcript reference only.
- `EagerEndOfTurn`: log only; do not trigger speech.
- `TurnResumed`: log/cancel pending commit if relevant.
- Prefer slightly slower correct commits over interrupting behavior.

## Bridge Strategy

If `ConversationRuntime` is not ready in Phase 2:

Allowed:

```text
TurnCollector -> existing conversation_controller
```

Not allowed:

```text
raw finalized STT frame -> existing conversation_controller
```

## Runtime Integration Plan

- Add committed-turn path in pipeline after STT stage.
- Maintain existing outbound greeting and telephony transport behavior.
- Ensure call/session snapshot stays immutable and serializable.
- Do not store live Pipecat objects in runtime registries.

## Testing Plan

- `EndOfTurn` commits exactly once.
- Raw finalized fragments do not directly trigger responses.
- Duplicate fragments/events are ignored.
- Empty/noise text is ignored.
- User speech during greeting-pending phase is ignored or safely deferred.
- `EagerEndOfTurn` does not trigger assistant response.
- `TurnResumed` cancels/prevents pending commit when relevant.
- `TurnInput` schema/shape validation passes.

## Acceptance Criteria

- Response generation path is driven only by committed turns.
- No direct raw finalized-fragment trigger remains in active response path.
- Existing outbound call and greeting behavior remains intact.
- Normal test suite remains green without live provider dependency.

## Risks

- Incorrect event-order assumptions may drop valid turns.
- Over-aggressive dedupe can suppress legitimate repeated answers.
- Bridge layer may create temporary duplication if old path is not fully gated.

## Rollback Plan

- Keep legacy processor path behind a temporary switch for emergency fallback.
- Re-enable old finalized handling only for controlled debugging, never as default.

## Handoff To Phase 3

- TurnCollector provides stable committed `TurnInput` contract.
- Phase 3 consumes committed turns in deterministic `ConversationRuntime` + `FlowRunner`.
