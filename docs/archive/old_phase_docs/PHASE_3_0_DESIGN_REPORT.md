# Phase 3.0 Design Report: Minimal Conversation Brain

Archived historical design report. This document describes the state before
Phase 3 implementation and is not current runtime guidance. Current committed
turn behavior is documented in `docs/current/RUNTIME_FLOW.md` and
`docs/design/TURN_AND_CONVERSATION_RULES.md`.

## 1. Current State After Phase 2

The active call path is now committed-turn gated, but still uses the legacy decision bridge:

```text
Deepgram Flux / Pipecat STT
-> finalized transcript buffers in TurnCollector
-> Deepgram EndOfTurn commits TurnInput
-> UserConversationProcessor temporary bridge
-> conversation_controller.decide_next_turn()
-> apply_turn_decision()
-> TTSSpeakFrame
```

`app/services/turn_collector.py` is the main Phase 2 success. It buffers finalized transcript text, commits only on `end_of_turn`, ignores `update`, `eager_end_of_turn`, and `turn_resumed`, suppresses greeting-time speech, and guards narrow duplicates. `app/models/conversation_models.py` currently contains only turn collection models: `TurnEvent`, `TurnInput`, and `TurnCollectionResult`.

`app/services/pipecat_pipeline.py` still owns the bridge. `UserConversationProcessor` receives Pipecat frames, observes finalized transcript frames for buffering only, commits turns through `handle_end_of_turn()`, logs transcript events, calls `decide_next_turn()`, and pushes `TTSSpeakFrame`.

`app/services/conversation_controller.py` is still LLM-led. It keeps a small `ConversationRuntimeState` dataclass with `phase`, `greeting_audio_pending`, `last_user_text`, and recent events. It asks Cerebras for a JSON `TurnDecision`, validates text safety, and updates phase. This is useful as a temporary bridge, but it is not durable conversation state.

Current weaknesses:

- Legacy controller still owns too much conversation policy.
- There is no durable per-call `ConversationState`.
- Slot memory does not exist, so collected information cannot be trusted across turns.
- Repeated-question prevention does not exist beyond basic assistant text validation.
- The LLM still chooses too much: phase, intent, and assistant wording.
- `ACTIVE_SESSIONS` stores transcript, metrics, and config snapshot, but not conversation state.
- The default `FlowDefinition` exists in config snapshots, but no runtime consumes it yet.

## 2. Phase 3 Goal

Phase 3 should add the minimum useful conversation brain:

```text
TurnInput
-> ConversationRuntime
-> ConversationState / SlotState update
-> SimpleFlowRunner chooses next action
-> RepeatedQuestionGuard validates action
-> SpeechComposer returns final short text
-> UserConversationProcessor sends TTSSpeakFrame
```

The goal is not a generic dialog platform. The goal is a reliable local/ngrok demo where the bot remembers collected slots, asks one sensible next question, handles refusal cleanly, and does not let the LLM randomly restart the call.

## 3. What Phase 3 Must Build

Phase 3 must build:

- `ConversationState`, `SlotState`, `RuntimeAction`, and `RuntimeResult` models.
- `ConversationRuntime.handle_turn(turn)` as the only committed-turn decision boundary.
- `SimpleFlowRunner` for readiness, ordered slot collection, refusal, fallback, and closing.
- `RepeatedQuestionGuard` to block collected/skipped slot questions.
- `SpeechComposer` to produce short, safe assistant text.
- Pipeline bridge replacement from `decide_next_turn()` to `ConversationRuntime.handle_turn()`.
- Focused tests proving slot memory, multi-slot extraction, refusal, user questions, fallback, and no raw fragment responses.

Phase 3 should also initialize state from the immutable `flow_definition` already present in the call snapshot.

Phase 3 active path must not call `conversation_controller.decide_next_turn()`. The legacy controller may remain in the repository, but it must not be used in the active Phase 3 path. If a temporary fallback is absolutely needed, it must be behind an explicit disabled-by-default flag and must be removed before Phase 3 is accepted.

## 4. What Phase 3 Must Not Build

Do not build:

- A generic graph engine.
- A workflow DSL or flow builder UI.
- Pipecat Flows runtime integration.
- DB-backed runtime state.
- Background workers or queues.
- CRM, calendar, or external tool integrations.
- Speculative `EagerEndOfTurn` response generation.
- Filler acknowledgements by default.
- Full LLM adapter and provider metrics work from Phase 4.
- New live LLM calls unless explicitly approved after Phase 3A/3B review.
- Generic NER, a large parser, or arbitrary extraction logic.
- Any change to 16000 Hz sample-rate assumptions.

## 5. Minimal Runtime Architecture

Target runtime:

```text
TurnInput
-> ConversationRuntime.handle_turn(turn)
-> SimpleFlowRunner.handle_turn(state, turn, flow)
-> deterministic extraction / flow rules
-> RepeatedQuestionGuard.validate(action, state, flow)
-> SpeechComposer.compose(action, state, flow)
-> RuntimeResult
-> TTSSpeakFrame
```

Ownership:

- `ConversationRuntime` owns call state, counters, and state mutation.
- `SimpleFlowRunner` owns progression through the configured flow.
- `RepeatedQuestionGuard` owns final action validity.
- `SpeechComposer` owns final spoken text.
- The pipeline owns frame routing only.
- The LLM must not own the flow.
- Phase 3 active path is deterministic by default.
- General LLM classification, extraction, and question answering belongs to Phase 4.

## 6. Conversation State Model

Use a JSON-safe Pydantic model in `app/models/conversation_models.py` unless it becomes too crowded. Do not create `task_state.py` unless model size genuinely justifies it.

```python
class ConversationState(BaseModel):
    call_sid: str
    stream_sid: str | None = None

    phase: Literal[
        "greeting",
        "readiness",
        "collecting",
        "answering_question",
        "closing",
        "ended",
    ] = "greeting"

    active_node_id: str | None = None
    active_slot_id: str | None = None

    slots: dict[str, SlotState] = Field(default_factory=dict)

    turn_count: int = 0
    fallback_count: int = 0
    refusal_count: int = 0

    last_user_text: str | None = None
    last_assistant_text: str | None = None
    last_asked_slot_id: str | None = None

    started_at: datetime
    updated_at: datetime

    debug: dict[str, Any] = Field(default_factory=dict)
```

Rules:

- JSON-safe only.
- No Pipecat objects.
- No provider service objects.
- No transport, task, runner, or live WebSocket objects.
- No secrets.
- Persisted `ConversationState.phase` must use only `greeting`, `readiness`, `collecting`, `answering_question`, `closing`, or `ended`.
- Pipeline/runtime boundary code must normalize old names: `awaiting_readiness` -> `readiness`, and `task` -> `collecting`.
- Do not mix old and new phase names inside `ConversationState`.
- `ACTIVE_SESSIONS` may store only a JSON-safe serialized conversation state snapshot for dashboard/debugging.
- Do not store `ConversationRuntime` service objects, Pipecat objects, transport objects, tasks, runners, WebSocket objects, or provider clients in `ACTIVE_SESSIONS`.
- The `ConversationRuntime` service object should live inside the processor/call execution path.

## 7. Slot State Model

```python
class SlotState(BaseModel):
    slot_id: str
    status: Literal[
        "missing",
        "partial",
        "collected",
        "skipped",
        "confirmed",
        "invalid",
    ] = "missing"

    value: str | None = None
    raw_value: str | None = None

    attempts: int = 0
    last_asked_at_turn: int | None = None
    collected_at_turn: int | None = None
```

Rules:

- `collected`, `confirmed`, and `skipped` slots must not be asked again.
- `partial` slots may be clarified.
- `invalid` slots may be reprompted until max attempts.
- Optional slots can become `skipped` after max attempts.
- Required slot failure should produce a graceful close or phase-aware fallback.
- Corrections may reopen a collected/skipped slot, but only through an explicit correction path.

## 8. FlowRunner Design

Use `SimpleFlowRunner`, not a graph engine.

Input:

```text
ConversationState
TurnInput
FlowDefinition from snapshot["flow_definition"]
FallbackPolicy from snapshot["fallback_policy"]
```

Output:

```python
class RuntimeAction(BaseModel):
    action_type: Literal[
        "ask_slot",
        "acknowledge",
        "answer_then_ask",
        "clarify",
        "close",
        "fallback",
    ]
    slot_id: str | None = None
    assistant_text_hint: str | None = None
    should_end_call: bool = False
    reason: str | None = None
```

Runtime result:

```python
class RuntimeResult(BaseModel):
    action: RuntimeAction
    assistant_text: str
    should_end_call: bool = False

    state: ConversationState

    user_transcript_text: str | None = None
    assistant_transcript_text: str | None = None

    debug: dict[str, Any] = Field(default_factory=dict)
```

`RuntimeResult` rules:

- JSON-safe only.
- No Pipecat frames.
- No provider objects.
- No TTS calls.
- The pipeline converts `RuntimeResult.assistant_text` into `TTSSpeakFrame`.

Minimum behavior:

- Readiness check after greeting.
- Ordered slot collection from `flow_definition.slots`.
- Extract obvious slot values deterministically for the default demo slots.
- Collect multiple obvious slots from one sentence.
- Ask exactly one next missing slot.
- Close when required slots are collected.
- Handle stop/refusal before slot collection.
- Handle user question briefly and return to active slot.
- Skip optional slots after max attempts.

Critical simplification: start with ordered slot collection as the authoritative flow. Add only simple readiness handling and simple closing. Use `FlowNode` metadata only where it helps map start/closing text. Do not implement arbitrary branching, parallel nodes, or a graph engine in Phase 3.

`max_attempts` source order:

1. Use slot-level `max_attempts` from `SlotDefinition` when present.
2. Otherwise use fallback policy default if available.
3. Otherwise default to 2 attempts for Phase 3.

## 8A. Minimal Deterministic Slot Extraction

Phase 3 deterministic extraction supports only simple default-demo patterns.

Name patterns:

- `I am <name>`
- `I'm <name>`
- `my name is <name>`

Location patterns:

- `from <location>`
- `based in <location>`
- `located in <location>`

Role patterns:

- `applying for <role>`
- `<role> role`
- `for <role> position`

Rules:

- If extraction is obvious, collect the slot.
- If extraction is ambiguous, do not guess.
- Ask the active slot question when unsure.
- Do not build generic NER.
- Do not build a large parser.
- Do not call an LLM for extraction in Phase 3 unless separately approved.

## 9. RepeatedQuestionGuard Design

The guard runs after `SimpleFlowRunner` chooses an action and before `SpeechComposer` speaks.

Validate:

- Do not ask a `collected`, `confirmed`, or `skipped` slot.
- Do not ask the same slot twice in a row unless it is a controlled reprompt for `partial` or `invalid`.
- Do not restart readiness after the user has already moved to collection.
- Do not continue asking slots after refusal or close.

If invalid:

- Pick the next `missing`, `partial`, or `invalid` slot allowed by attempts.
- If no valid slot remains, return a close action.
- If state is inconsistent, return a phase-aware fallback and increment debug/guard counters.

The guard should return a normalized action rather than only raising exceptions. Tests should still assert guard violations are detectable.

Boundary: `RepeatedQuestionGuard` validates what action is allowed. `SpeechComposer` validates how that action is spoken.

## 10. SpeechComposer Design

`SpeechComposer` takes a valid `RuntimeAction`, `ConversationState`, and `FlowDefinition`, then returns final assistant text.

Rules:

- 1 to 2 short sentences.
- One question at a time.
- No verbose explanations.
- Prefer deterministic templates.
- Never include JSON, model names, provider details, slots, or debug text.
- Never directly trust arbitrary LLM text without validation.
- Replace empty or too-long text with a safe fallback.
- Do not call TTS or mutate Pipecat state.

Good output:

```text
Thanks. What role are you applying for?
```

Bad output:

```text
Based on the conversation state, I will now collect the next slot.
```

## 11. LLM Usage Boundary In Phase 3

Phase 3 should not build the full `LLMAdapter`; that is Phase 4.

Allowed in Phase 3:

- Define a small interface shape for later LLM help.
- Use deterministic extraction for obvious default demo values.
- Use canned/template answers for simple flow-related user questions.
- Optionally mark places where Phase 4 will add structured classification/extraction.

Avoid in Phase 3:

- New live LLM calls unless explicitly approved after Phase 3A/3B review.
- LLM deciding the whole flow.
- LLM choosing arbitrary next slot.
- LLM clearing slot state.
- LLM bypassing `RepeatedQuestionGuard`.
- LLM text going directly to TTS.
- Multi-step LLM chains.

Phase 3 should prove state, slots, no repeated questions, and simple flow. It should not try to prove that the LLM can understand everything. Full structured LLM classification, extraction, and question answering belongs to Phase 4.

## 12. Latency Strategy

Phase 3 latency should improve by removing unnecessary work:

- Do not add artificial sleeps.
- Do not run multi-step LLM chains per committed turn.
- Do not generate long responses.
- Do not use speculative `EagerEndOfTurn`.
- Do not add default filler acknowledgement.
- Let deterministic slot progression be instant.
- Do not add multi-step reasoning chains.

Target: after committed `TurnInput`, local/ngrok p50 first bot audio should remain around or under 2 seconds when providers behave normally. Measure honestly and do not claim production latency.

## 13. User Experience Rules

The call should feel like a simple, attentive intake:

- Greet clearly.
- Wait for a committed user turn.
- Ask one thing at a time.
- Remember already provided details.
- Accept multiple answers in one sentence.
- Handle "not now" and "stop" without arguing.
- Answer only simple flow-related questions with canned/template text.
- Handle only obvious corrections without restarting.
- Close cleanly.

Example:

```text
User: "I am Ojas from Bangalore, applying for backend."

State:
name = Ojas
location = Bangalore
role = backend

Next action:
close or ask the next missing configured slot
```

Example:

```text
User: "I don't want to do this."

Assistant:
No problem, we can stop here. Thank you.

Result:
should_end_call=True
```

Phase 3 user-question handling is intentionally narrow. Supported examples:

- "why do you need this?"
- "what is this for?"
- "how long will this take?"

For unknown questions, briefly say the call can be kept short, then return to the active slot. Do not call a general LLM unless Phase 4 is implemented.

Example:

```text
User: "Why do you need my location?"

Assistant:
It helps match the right opening. Are you currently based in Bangalore?
```

Phase 3 correction support is minimal and deterministic. Supported examples:

- "actually, my name is X"
- "no, I am from Y"
- "correction, role is Z"

If correction is unclear, ask a short clarification. Do not build complex correction inference in Phase 3.

## 14. Failure / Fallback Rules

Fallback must be phase-aware:

- Unclear readiness: ask whether to continue or stop.
- Unclear active slot: ask for that specific slot again, not a generic repeat every time.
- Same slot max attempts: skip optional slots or close gracefully for required slots.
- Any unapproved helper failure: deterministic fallback.
- Empty/too-long assistant text: `SpeechComposer` fallback.
- Repeated-question violation: choose next valid slot or close.

Avoid blindly saying:

```text
Could you repeat that?
```

Use it only when the active phase and slot justify it.

## 15. Integration With Phase 2 TurnCollector

Keep `TurnCollector` and `TurnInput` intact.

Current Phase 2 bridge:

```text
TurnInput
-> UserConversationProcessor._handle_committed_turn()
-> decide_next_turn()
-> TurnDecision
-> TTSSpeakFrame
```

Target Phase 3 bridge:

```text
TurnInput
-> UserConversationProcessor._handle_committed_turn()
-> ConversationRuntime.handle_turn()
-> RuntimeResult
-> TTSSpeakFrame
```

`UserConversationProcessor` should still own Pipecat frame push mechanics, transcript append calls, and lock protection. It should stop owning conversation decisions.

`ConversationRuntime` should be created during pipeline assembly using:

- `call_sid`
- `stream_sid`
- immutable `ConfigSnapshot`
- `flow_definition`
- `fallback_policy`
- opening mode

Runtime session should expose serialized conversation state for dashboard/logging without storing live Pipecat objects.

`ConversationRuntime` should be initialized after the call context is resolved and before the first committed `TurnInput` is handled. If `stream_sid` is unavailable at construction time, initialize with `call_sid` and update `stream_sid` once available. Do not delay first turn handling because of optional `stream_sid`.

When `RuntimeResult.should_end_call=True`, Phase 3 should return closing assistant text and mark runtime state as `closing` or `ended`. Pipeline should preserve the existing closing/end-call behavior if one exists. If no automatic hangup exists, Phase 3 must not invent new telephony termination logic. Actual call termination behavior can remain current/manual unless already supported.

## 16. Files Proposed For Phase 3

Keep the file set small:

- `app/models/conversation_models.py`: add `ConversationState`, `SlotState`, `RuntimeAction`, `RuntimeResult`, optional `SlotUpdate`.
- `app/services/conversation_runtime.py`: new state owner and `handle_turn()`.
- `app/services/flow_runner.py`: new `FlowRunner` protocol and `SimpleFlowRunner`.
- `app/services/repeated_question_guard.py`: new guard module.
- `app/services/speech_composer.py`: new final text composer.
- `app/services/pipecat_pipeline.py`: replace `decide_next_turn()` bridge with runtime call.
- `app/runtime.py`: add JSON-safe conversation state helpers if needed.
- `tests/test_conversation_runtime.py`: runtime behavior.
- `tests/test_flow_runner.py`: slot progression and refusal.
- `tests/test_speech_composer.py`: final text safety.
- `tests/test_pipeline.py`: bridge still commits only via EndOfTurn.

Avoid adding `conversation_runtime.py`, `flow_runner.py`, and `speech_composer.py` before Phase 3 implementation begins. This report is the checkpoint only.

## 17. Tests Required

Required normal tests:

- `ConversationState` and `SlotState` JSON serialization.
- Phase-name normalization from `awaiting_readiness` to `readiness` and `task` to `collecting`.
- State initialization from default `FlowDefinition`.
- Readiness moves to collection.
- Multiple obvious default-demo slots collected from one sentence.
- Ambiguous slot extraction does not guess.
- Collected slots are not asked again.
- Skipped slots are not asked again.
- Partial/invalid slots reprompt within max attempts.
- `max_attempts` resolves from slot, then fallback policy, then default 2.
- Optional slot max attempts skips.
- Required slot max attempts closes or safe-fallbacks.
- Refusal/stop closes gracefully.
- Simple flow-related user question gets canned answer and resumes active slot.
- Unknown user question returns to active slot without live LLM use.
- Obvious correction updates prior slot without resetting flow.
- RepeatedQuestionGuard replaces invalid repeated ask.
- SpeechComposer rejects debug/provider/JSON/overlong text.
- `RuntimeResult` is JSON-safe and contains no Pipecat/provider objects.
- Pipeline raw finalized transcript still does not trigger response.
- Pipeline EndOfTurn still produces exactly one assistant response.
- Phase 3 active path does not call `conversation_controller.decide_next_turn()`.

Do not add live telephony or provider tests for this phase.

## 18. Implementation Breakdown

Hard rule: do not implement all of Phase 3 in one commit. Phase 3 must be implemented as separate reviewed subphases. Each subphase must have a small diff, focused tests, review, and commit before moving to the next subphase.

Phase 3A: Models

- Files changed: `app/models/conversation_models.py`, model tests.
- Behavior added: JSON-safe runtime state and action/result contracts.
- Acceptance: model validation and serialization pass.
- Risk: making models too broad; keep only needed fields.

Phase 3B: SimpleFlowRunner

- Files changed: `app/services/flow_runner.py`, `tests/test_flow_runner.py`.
- Behavior added: readiness, ordered slot collection, next-slot selection, close action.
- Acceptance: default flow can progress through name, location, and role.
- Risk: extraction heuristics may be crude; isolate them and keep Phase 4 ready.

Phase 3C: Guard And Composer

- Files changed: `app/services/repeated_question_guard.py`, `app/services/speech_composer.py`, tests.
- Behavior added: repeated-slot prevention and final short speech.
- Acceptance: collected/skipped slots are never asked again.
- Risk: guard/composer overlap; guard should validate action, composer should validate text.

Phase 3D: ConversationRuntime

- Files changed: `app/services/conversation_runtime.py`, runtime tests.
- Behavior added: state initialization, handle_turn orchestration, counters, result creation.
- Acceptance: one committed turn yields one safe `RuntimeResult`.
- Risk: concurrency; reuse the existing pipeline lock and avoid internal parallel tasks.

Phase 3E: Pipeline Bridge Replacement

- Files changed: `app/services/pipecat_pipeline.py`, maybe `app/runtime.py`, pipeline tests.
- Behavior added: `UserConversationProcessor` calls runtime instead of `decide_next_turn()`.
- Acceptance: raw finalized fragments still do not respond; EndOfTurn does; active path never calls `conversation_controller.decide_next_turn()`.
- Risk: breaking working greeting path; keep greeting behavior unchanged.

Phase 3F: Docs And Handoff

- Files changed: Phase progress/handoff docs and notes if the active project files exist.
- Behavior added: no runtime behavior.
- Acceptance: normal `pytest` passes; manual telephony validation remains human-owned.
- Risk: current active progress/notes location is unclear because root files were moved/removed.

## 19. Risks And Mitigations

- Risk: overbuilding a flow engine.
  Mitigation: ordered slots first; only honor simple `FlowNode` metadata.

- Risk: brittle deterministic slot extraction.
  Mitigation: keep extraction small and replaceable; use tests for default demo utterances.

- Risk: duplicate state between `ACTIVE_SESSIONS` and runtime object.
  Mitigation: one runtime state object per call, serialized into session for observation.

- Risk: legacy controller remains in path accidentally.
  Mitigation: make active Phase 3 path call `ConversationRuntime.handle_turn()` only; add tests ensuring `decide_next_turn()` is not called.

- Risk: user questions derail slot collection.
  Mitigation: use `answer_then_ask` with active slot preserved.

- Risk: refusal is treated as unclear input.
  Mitigation: refusal detection runs before extraction and fallback.

- Risk: phase names drift between old `awaiting_readiness` and new `readiness`.
  Mitigation: normalize at pipeline/runtime boundary and keep persisted state using Phase 3 names.

- Risk: Phase 3 becomes a large, hard-to-review implementation.
  Mitigation: require reviewed subphases 3A through 3F with focused tests and commits.

## 20. Acceptance Criteria

Phase 3 is complete only when:

- `ConversationRuntime` exists.
- Durable per-call `ConversationState` exists.
- Phase 3 persisted state uses only `greeting`, `readiness`, `collecting`, `answering_question`, `closing`, and `ended`.
- `SlotState` is initialized from configured slots.
- Slot states update from committed turns.
- Multiple obvious slots can be collected from one user sentence.
- Ambiguous extraction does not guess.
- Collected, confirmed, and skipped slots are not asked again.
- Refusal/stop closes gracefully.
- Simple user questions are answered with canned/template text and flow resumes.
- No new live LLM calls are introduced by default.
- Active Phase 3 path does not call `conversation_controller.decide_next_turn()`.
- RepeatedQuestionGuard works before speech.
- SpeechComposer returns short safe responses.
- `RuntimeResult` is JSON-safe and is converted to `TTSSpeakFrame` only by the pipeline.
- Phase 2 TurnCollector remains intact.
- Raw STT finalized fragments still do not trigger responses.
- No speculative EOT or filler system is added.
- Normal `pytest` passes without live provider dependencies.
- No real Exotel/voice success is claimed by automated tests.

## 21. Open Questions

No blocking questions for this design checkpoint.

Before implementation, identify the active replacement for root `PHASE_PROGRESS.md` and `NOTES.md`, because `AGENTS.md` still requires them but the current docs tree only exposes the old archived phase progress file. That is a process/documentation cleanup item, not a blocker for this report.
