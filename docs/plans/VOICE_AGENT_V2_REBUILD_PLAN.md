# Voice Agent V2 Rebuild Plan

## Phases

- Phase 0: Docs cleanup and baseline
- Phase 1: Move behavior config from .env to UI/DB
- Phase 2: Add BotProfile and FlowDefinition
- Phase 3: Add TurnCollector
- Phase 4: Add ConversationState and SlotState
- Phase 5: Add SimpleFlowRunner
- Phase 6: Add LLMAdapter with metrics
- Phase 7: Add repeated-question guard and phase-aware fallback
- Phase 8: Improve dashboard logs/metrics
- Phase 9: Single-call demo stabilization
- Phase 10: Evaluate Pipecat Flows later

## Not Yet

- Do not implement speculative two-lane latency optimization yet.
- Do not add fast filler acknowledgements yet.
- Do not optimize for sub-1s before correctness.
- Do not change sample rate away from 16000 Hz.
- Do not build production queue/worker infra.
- Do not write every live turn to MongoDB.