# PHASE_2_BREAKDOWN.md - Internal Execution Checklist

## Purpose

Provide the next Phase 2 implementation session with a practical, ordered checklist that makes pipeline work follow the documented runtime boundaries.

## How This File Should Be Used

This file refines execution inside `PHASE_2_PIPELINE.md`. It does not change `PHASE_PROGRESS.md`, and the internal chunks below are planning aids only, not official repository phases.

Use this document after reading the required Phase 2 pre-read set and before modifying runtime or pipeline code. Confirm the documentation outcomes first, then implement only the approved Phase 2 scope.

## Internal Chunk List

### 2A Flow And Lifecycle Documentation

Expected outcome:

- `PHASE_2_PIPELINE_FLOW.md` and `PHASE_2_STATE_MACHINE.md` describe one consistent lifecycle from WebSocket acceptance through teardown and session cleanup.

What should be clarified before code:

- The current Phase 1 bootstrap behavior and the intended pipeline replacement path.
- The order of disconnect cancellation, runner unwind, later finalization boundary, and cleanup.

What should not be implemented early:

- No pipeline service, task execution, or route behavior replacement during the documentation step.

### 2B Route-To-Pipeline Interface Lock

Expected outcome:

- The coding session knows which router-owned inputs cross into pipeline assembly: existing transport plus serializable call-scoped/session context using `call_sid`, `stream_sid`, and `config_snapshot`.

What should be clarified before code:

- The router continues to own acceptance, identity resolution, session creation, and bootstrap rejection paths.
- `ACTIVE_SESSIONS` remains owned by `app/runtime.py` and stores no live execution objects.

What should not be implemented early:

- No relocation of route identity handling or runtime registry ownership into the pipeline service.

### 2C Pipecat Service Assembly Plan

Expected outcome:

- The pipeline assembly has a narrow service boundary for transport, STT, LLM, and TTS with typed `.Settings()` configuration and fixed `16000` Hz assumptions.
- `INITIAL_GREETING_PROMPT` is read from `config_snapshot` and delivered as a queued `LLMMessagesAppendFrame` after task creation.

What should be clarified before code:

- Required Pipecat API imports and installed-version surface must be checked when implementation begins.
- STT (Deepgram), LLM (Cerebras), and TTS (Cartesia) import paths must each be verified against the installed venv before service construction begins, following the same Pipecat API check discipline established in Phase 1.
- Verified Pipecat 1.2.1 service construction pattern: `DeepgramSTTService` uses `sample_rate=` as a direct constructor kwarg and `settings=DeepgramSTTSettings(...)`; `CerebrasLLMService` uses `settings=CerebrasLLMSettings(...)`; `CartesiaTTSService` uses `sample_rate=` as a direct constructor kwarg and `settings=CartesiaTTSSettings(voice=..., model=...)` — the deprecated `voice_id=` and `model=` direct kwargs emit `DeprecationWarning` in 1.2.1. None of the three use `.InputParams()` as the primary config path.
- Pipeline service responsibilities stay distinct from router and persistence responsibilities.

What should not be implemented early:

- No speculative abstractions or later-phase persistence hooks beyond documenting their boundary.

### 2D Observer/VAD/Mute-Strategy Lock

Expected outcome:

- The implementation follows the fixed no-barge-in and latency-observer architecture without reopening decisions.

What should be clarified before code:

- Use `PipelineTask(..., observers=[latency_observer])`.
- Use `UserBotLatencyObserver`.
- Configure `LLMUserAggregatorParams.user_mute_strategies` so user audio is dropped while the bot speaks.
- Include `SileroVADAnalyzer()` inside `LLMUserAggregatorParams`, not as a standalone pipeline stage.

What should not be implemented early:

- No alternate observer registration, standalone VAD stage, or changed interruption policy.

### 2E Route Replacement Implementation Plan

Expected outcome:

- The coding session can replace the successful Phase 1 post-bootstrap `1000` close with pipeline execution while preserving all bootstrap and cleanup boundaries.

What should be clarified before code:

- Existing unsupported-provider, missing-identity, and duplicate-call close handling remains meaningful.
- Disconnect teardown reaches the Phase 3 finalization hook boundary without implementing persistence in Phase 2.

What should not be implemented early:

- No persistence writes, dashboard/config routes, live voice test claims, or production telephony behavior assertions.

## Entry Criteria

- `PHASE_1_TRANSPORT.md` is `DONE` in `PHASE_PROGRESS.md`.
- `PHASE_1_IMPLEMENTATION_NOTES.md` has been read.
- `PHASE_2_PIPELINE_FLOW.md`, `PHASE_2_STATE_MACHINE.md`, and this document exist and agree with `TECHNICAL_PLAN.md`.
- Fixed v1 route, sample-rate, runtime ownership, observer, VAD, and teardown decisions remain unchanged.
- The implementation session explicitly starts after this documentation pass has been reviewed.

## Exit Criteria

- For the documentation pass: all three support documents are consistent, `TECHNICAL_PLAN.md` and `NOTES.md` record the style, and no Phase 2 runtime code or status completion has been introduced.
- For the later coding pass: the Phase 2 contract checklist is complete, allowed structural verification is recorded, and any handoff to Phase 3 names the finalization boundary without implementing out-of-scope behavior.

## Cross-Checks Before Coding

- Confirm the route still exists at `WS /telephony/exotel`.
- Confirm the Phase 1 successful `1000` close is still a temporary stub to be replaced, not a desired pipeline close.
- Confirm `ACTIVE_SESSIONS` is still owned by `app/runtime.py` and remains serializable-only.
- Confirm the runtime record already carries `config_snapshot`.
- Confirm serializer setup remains `ExotelFrameSerializer.InputParams(exotel_sample_rate=16000, sample_rate=16000)`.
- Confirm observer registration, VAD placement, mute strategy, and teardown order match `AGENTS.md`.
- Confirm implementation does not begin Phase 3 logging/persistence or later dashboard/config work.

## Risks To Avoid

- Treating internal chunks as new official phases or adding them to `PHASE_PROGRESS.md`.
- Storing task, transport, pipeline, observer, runner, or service objects inside `ACTIVE_SESSIONS`.
- Blurring router bootstrap, pipeline assembly, and later finalization responsibilities.
- Preserving the Phase 1 successful bootstrap close after a running pipeline path exists.
- Reopening fixed v1 decisions without human approval.
- Reporting structural or mocked checks as live Exotel or voice validation.
