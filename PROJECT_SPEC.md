# PROJECT_SPEC.md - Voice Agent Product Spec

## Objective

Build a local-development conversational voice agent that integrates:

- Exotel telephony transport
- FastAPI and WebSocket entrypoints
- Pipecat orchestration
- Deepgram for speech-to-text
- Cerebras for LLM responses
- Cartesia for text-to-speech
- structured logging and stored call metrics

The system should be implemented in phases so the codebase stays reviewable and future agentic coding sessions remain tightly controlled.

## Product Intent

This project is intended to become a clean, high-quality voice-agent codebase built incrementally from scratch. The implementation should emphasize architecture clarity, safe iteration, and reliable handoff between future coding sessions.

## Primary Goals

- Create a maintainable transport-to-pipeline architecture.
- Preserve clear boundaries between telephony, orchestration, logging, config, and persistence.
- Capture call and turn metadata in structured form.
- Enable future incremental development through phase-gated execution documents.
- Keep the repository easy for future AI agents to understand without guessing.

## Non-Goals

- Do not automate real Exotel validation in the initial agent workflow.
- Do not optimize voice quality in these planning and scaffold phases.
- Do not introduce production infra such as queues, worker orchestration, or deployment automation unless later specified.
- Do not treat this setup as a generic template pack for unrelated projects.

## Constraints

- Local development only for now.
- Real telephony and voice behavior will be validated manually by the human.
- For v1, both Exotel transport and internal pipeline sample rate are intentionally hardcoded to 16000 Hz.
- Testing owned by the AI must stay at normal engineering-validation level:
  - config
  - schema
  - routing
  - serializer wiring
  - import sanity
  - logging and persistence data shape
- The architecture should remain compatible with phased implementation, not a one-shot prototype.
- Pipeline observer wiring must follow Pipecat 1.0.0+ constructor style on `PipelineTask`.

## Success Criteria

- The repo contains enough spec detail that future implementation sessions can proceed phase by phase without inventing requirements.
- Each execution phase has clear file scope, completion criteria, and verification steps.
- Manual voice testing responsibilities are clearly separated from AI-owned verification responsibilities.
- The architecture docs remain concrete to the intended voice-agent stack.
- The docs resolve key implementation choices so future agents do not reopen already-decided transport, observer, or teardown patterns.

## Human vs AI Responsibilities

### Human-Owned

- Exotel account and telephony setup
- tunnel or webhook setup
- real call testing
- voice-quality judgment
- live latency judgment
- operational environment verification

### AI-Owned

- Markdown operating system for phased delivery
- future code implementation within approved phases
- schema validation
- config structure
- module wiring
- logging and persistence modeling
- clean separation of concerns

## Expected Architecture Shape

The project is expected to grow into a structure with:

- an application entrypoint
- config management
- telephony router layer
- Pipecat pipeline service layer
- logging and metrics utilities
- persistence layer
- tests limited to non-voice behavior unless explicitly expanded later

## Delivery Strategy

The implementation must proceed through explicit phase documents:

1. setup and project skeleton
2. telephony transport
3. Pipecat pipeline wiring
4. logging and persistence models
5. integration glue
6. normal non-voice tests

No later phase should begin before the current phase is reviewed and marked complete.
