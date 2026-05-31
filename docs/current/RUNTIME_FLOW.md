# Runtime Flow

## Single-Call Prototype Flow

1. Dashboard starts outbound call.
2. Selected bot profile/config snapshot is prepared.
3. Exotel connects to WebSocket.
4. Pipecat pipeline handles transport/STT/TTS.
5. TurnCollector emits committed user turns.
6. ConversationRuntime processes the turn.
7. FlowRunner decides next use-case action.
8. LLMAdapter performs bounded classification/extraction/phrasing.
9. SpeechComposer creates final assistant text.
10. Cartesia speaks response.
11. Final call log is persisted after call completion.

## Persistence And State Notes

- No MongoDB writes inside the hot live audio path unless necessary.
- Active call state should stay in memory.
- Final call logs should persist after call completion.