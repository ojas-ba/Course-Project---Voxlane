# Runtime Flow

## Single-Call Prototype Flow

1. Dashboard starts outbound call.
2. Selected bot profile/config snapshot is prepared.
3. Exotel connects to WebSocket.
4. Pipecat pipeline handles transport/STT/TTS.
5. TurnCollector emits committed user turns.
6. ConversationRuntime validates call identity, owns per-call state, and orchestrates deterministic turn handling.
7. SimpleFlowRunner decides the next configured slot/action.
8. RepeatedQuestionGuard normalizes repeated or invalid asking actions.
9. SpeechComposer creates final assistant text.
10. Cartesia speaks response.
11. Final call log is persisted after call completion.

Phase 3E removed the active committed-turn bridge to the old turn-decision
controller. The active path does not call `decide_next_turn()` for committed
turns. Outbound greeting generation may still use the LLM-backed greeting
helper before the call connects.

## Persistence And State Notes

- No MongoDB writes inside the hot live audio path unless necessary.
- Active call state should stay in memory.
- Active call sessions store a JSON-safe `conversation_state` snapshot for
  dashboard/debug visibility.
- Final call logs should persist after call completion.
- Final call logs include the latest `conversation_state` snapshot so collected
  slot state survives `ACTIVE_SESSIONS` cleanup.
