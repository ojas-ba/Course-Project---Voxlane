# Current Architecture

## Target V2 Runtime Path

Exotel WS
  -> Pipecat audio pipeline
  -> Deepgram Flux STT
  -> TurnCollector
  -> ConversationRuntime
  -> FlowRunner
  -> LLMAdapter
  -> SpeechComposer
  -> Cartesia TTS

## Core Rules

- Pipecat pipeline is only the audio/frame routing layer.
- Conversation logic must not live inside the pipeline.
- Raw STT finalized fragments must not directly trigger bot responses.
- User turns must go through TurnCollector first.
- ConversationRuntime owns phase, slot state, fallback behavior, and next action.