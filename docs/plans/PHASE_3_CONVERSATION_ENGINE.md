# Phase 3: ConversationRuntime And FlowRunner

## Completion Status

Phase 3 is complete through Phase 3F. This document is the final Phase 3
planning and completion artifact. Active committed turns now use
`ConversationRuntime.handle_turn()` instead of the old committed-turn
controller bridge.

The old committed-turn APIs `decide_next_turn()` and `apply_turn_decision()`
are not active app behavior. Outbound greeting generation may still use
`conversation_controller.generate_outbound_greeting_text()` before an outbound
call connects.

## Goal

Replace "LLM decides everything every turn" with deterministic state and flow
progression owned by the app.

After Phase 2, turn commitment is stabilized. Phase 3 keeps the TurnCollector
contract intact and replaces only the committed-turn decision path:

```text
TurnInput
-> ConversationRuntime
-> ConversationState / SlotState update
-> SimpleFlowRunner
-> RepeatedQuestionGuard
-> SpeechComposer
-> RuntimeResult
-> pipeline sends TTSSpeakFrame
```

Phase 3 must improve the demo call without overbuilding: remember collected
slots, avoid repeated questions, ask one sensible next question, handle refusal
cleanly, answer narrow flow-related questions with canned text, support obvious
corrections, and avoid unnecessary LLM calls.

## Scope

- Add JSON-safe `ConversationState`, `SlotState`, `RuntimeAction`, and
  `RuntimeResult` models.
- Introduce a synchronous `ConversationRuntime.handle_turn()` that owns the
  live `ConversationState` object.
- Add `FlowRunner` interface and deterministic `SimpleFlowRunner`.
- Add `RepeatedQuestionGuard` and `SpeechComposer`.
- Replace the active committed-turn bridge in Phase 3E only.
- Store only serialized conversation-state snapshots in `ACTIVE_SESSIONS`.

## Out Of Scope

- Phase 3A implementation before explicit approval.
- Generic graph engine, workflow DSL, or Pipecat Flows runtime integration.
- DB-backed runtime state.
- New live LLM calls by default.
- Speculative `EagerEndOfTurn` response generation.
- Filler acknowledgements by default.
- New telephony hangup behavior.
- Live provider or real Exotel validation.

## Shared Constraints

- Keep sample rate fixed at `16000`.
- Preserve Exotel webhook/WebSocket behavior and outbound greeting path.
- Preserve immutable config snapshots and serializable runtime state only.
- Do not store live Pipecat objects in runtime registries.
- Do not write every live turn to MongoDB in the hot path.
- Keep live provider tests optional and keep normal `pytest` provider-independent.
- Active Phase 3 path must not call `conversation_controller.decide_next_turn()`.

## Phase Breakdown

Phase 3 was implemented in reviewed subphases:

```text
Phase 3A: Models
Phase 3B: SimpleFlowRunner
Phase 3C: RepeatedQuestionGuard + SpeechComposer
Phase 3D: ConversationRuntime
Phase 3E: Pipeline bridge replacement
Phase 3F: Docs + final audit
```

Subphase commit messages:

```text
feat: add phase 3 conversation models
feat: add deterministic simple flow runner
feat: add repeated question guard and speech composer
feat: add conversation runtime orchestration
feat: complete phase 3 runtime bridge
docs: align phase 3 runtime bridge documentation
```

## Phase 3A Model Guidance

Phase 3A should keep models simple and data-focused. Do not add complex
behavior validators such as "close actions must set `should_end_call=True`";
normalize that behavior later in `SimpleFlowRunner` or `ConversationRuntime`.

Models belong in `app/models/conversation_models.py` unless review approves a
different location:

- `SlotState`
- `ConversationState`
- `RuntimeAction`
- `RuntimeResult`
- `normalize_conversation_phase()`

Use `Field(default_factory=...)` for mutable fields:

```python
slots: dict[str, SlotState] = Field(default_factory=dict)
debug: dict[str, Any] = Field(default_factory=dict)
```

Use timezone-aware timestamp defaults:

```python
started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

`normalize_conversation_phase()` must map known legacy names forward:

```text
awaiting_readiness -> readiness
task -> collecting
greeting -> greeting
readiness -> readiness
collecting -> collecting
answering_question -> answering_question
closing -> closing
ended -> ended
```

Unknown phases must raise a clear `ValueError`. Do not silently map unknown
phases to `collecting`, `readiness`, or any other fallback phase.

`RuntimeResult.state` must be a deep copied snapshot of the current
`ConversationState` at result creation time. `ConversationRuntime` keeps the
live state internally so older result objects do not change when future turns
mutate state.

## ConversationState Design

`ConversationState` owns the JSON-safe per-call memory:

- `call_sid` and optional `stream_sid`
- phase
- active node and active slot
- slot map
- turn, fallback, and refusal counters
- last user and assistant text
- last asked slot id
- timezone-aware `started_at` and `updated_at`
- debug-safe metadata only

Allowed Phase 3 phases:

```text
greeting
readiness
collecting
answering_question
closing
ended
```

When `RuntimeResult.should_end_call=True`, `ConversationRuntime` should return
state with `phase="closing"`. Do not set `ended` until a later explicit
call-ended event or handoff marks the call ended. The assistant still needs to
speak the closing text.

`should_end_call=True` means the conversation is complete after the assistant
message. It does not mean Phase 3 should force an immediate telephony hangup.

## SlotState Design

`SlotState` tracks one configured slot:

- `slot_id`
- `status`: `missing`, `partial`, `collected`, `skipped`, `confirmed`, or
  `invalid`
- `value`
- `raw_value`
- `attempts`
- `last_asked_at_turn`
- `collected_at_turn`

Collected, confirmed, and skipped slots must not be asked again. Partial or
invalid slots may be clarified only while attempts allow it.

## State Mutation Ownership

`ConversationRuntime` owns the live `ConversationState` object.

`SimpleFlowRunner` may mutate the `ConversationState` passed to it, but it must
not store state internally.

`RepeatedQuestionGuard` and `SpeechComposer` should not mutate
`ConversationState` except for explicitly approved debug-safe metadata.

## SimpleFlowRunner Design

Before implementing Phase 3B, inspect the actual `FlowDefinition` and
`SlotDefinition` fields in `app/models/config_models.py`. Do not invent new
config schema fields in Phase 3B. If a field such as `question`, `reprompt`,
`max_attempts`, or `required` is absent, use safe defaults inside the runner or
composer without changing `config_models.py`.

`SimpleFlowRunner` is deterministic and local. It must not await provider calls,
perform network I/O, or call an LLM.

Minimum behavior:

- readiness check
- ordered slot collection from configured slots
- closing action when required slots are complete
- refusal path
- fallback path
- one question per turn
- optional slot skip after max attempts
- required slot graceful close after max attempts
- no repeated collected/skipped slot questions

`ConversationRuntime.handle_turn()` should be synchronous in Phase 3 unless a
concrete existing pipeline constraint requires async. Do not make it async only
for future-proofing.

## Deterministic Extraction Rules

Extraction scope is intentionally small.

Allowed patterns:

```text
name:
- "I am <name>"
- "I'm <name>"
- "my name is <name>"

location:
- "from <location>"
- "based in <location>"
- "located in <location>"

role:
- "applying for <role>"
- "<role> role"
- "for <role> position"
```

Known slot phrase boundaries must stop extraction values. Required
anti-overcapture scenarios:

```text
"I am Ojas from Bangalore" -> name=Ojas, location=Bangalore
"I am Ojas, applying for backend" -> name=Ojas, role=backend
"I am applying for backend" -> role=backend, do not collect name
"from Bangalore applying for backend" -> location=Bangalore, role=backend
```

Deterministic extraction may update only slot IDs that exist in the current
`FlowDefinition`. Never create new slot state from extracted text. For example,
if the configured flow does not contain `location`, the runtime must not create
a `location` slot just because the user said "from Bangalore."

If input is ambiguous, do not guess. Ask the active slot question or use a
phase-aware clarification.

## Attempts And Reprompt Rules

Slot attempts increment only when the final action emits `ask_slot` or
`clarify` for that slot.

Attempts must not increment merely because the user sent unclear text.

Attempts must not increment for slots collected from the current user turn.

`answer_then_ask` after a user question should not consume another slot attempt
unless the action is explicitly treated as a reprompt.

Before emitting a reprompt, if `slot.attempts >= max_attempts`, skip optional
slots or close required slots instead of asking again.

Max-attempt source order:

```text
1. SlotDefinition.max_attempts if present
2. fallback_policy.max_attempts if present
3. default 2
```

## Minimal Question And Correction Patterns

Supported user question patterns:

```text
why do you need
what is this for
how long
```

Supported correction patterns:

```text
actually, my name is X
no, my name is X
actually, I am from X
no, I am from X
correction, role is X
actually, role is X
```

If a question or correction is unclear, do not infer. Ask a clarification or
return to the active slot.

## RepeatedQuestionGuard

The guard validates what action is allowed:

- do not ask a collected, confirmed, or skipped slot
- do not ask the same slot twice in a row unless it is a controlled
  partial/invalid reprompt and attempts allow it
- do not restart readiness after collection begins
- do not continue asking slots after refusal or close

If the action is invalid, normalize to the next valid slot or a close action.
If no valid slot remains, close with `should_end_call=True`.

## SpeechComposer

The composer validates how the action is spoken:

- one to two short sentences
- one question at a time
- deterministic templates first
- no JSON, debug text, provider names, model names, or internal slot jargon
- replace empty, unsafe, or overlong text with a phase-aware fallback
- do not call TTS or mutate Pipecat state

Default useful texts:

```text
name: "What is your name?"
location: "Where are you located?"
role: "What role are you interested in?"
completion: "Thank you. That is all I needed for now."
refusal: "No problem, we can stop here. Thank you."
readiness fallback: "Should I continue, or would you prefer to stop here?"
```

## ConversationRuntime

`ConversationRuntime` orchestrates one committed turn:

```text
normalize turn phase
increment turn_count
update last_user_text and updated_at
call SimpleFlowRunner
call RepeatedQuestionGuard
call SpeechComposer
apply attempts based on final action
update last_assistant_text and last_asked_slot_id
return RuntimeResult with a deep copied state snapshot
```

Final close, refusal, and all-required-slots-collected completion actions must
produce `RuntimeResult.should_end_call=True` and return state with
`phase="closing"`.

The runtime service object must not be stored in `ACTIVE_SESSIONS`.

## Pipeline Integration Plan

Phase 3E replaces only the committed-turn bridge:

```text
TurnInput
-> UserConversationProcessor._handle_committed_turn()
-> ConversationRuntime.handle_turn()
-> RuntimeResult
-> TTSSpeakFrame(result.assistant_text)
```

Preserve:

- `TurnCollector`
- raw finalized STT buffer-only behavior
- `_turn_processing_lock`
- transcript append order
- outbound greeting playback
- greeting-time user speech suppression
- 16000 Hz sample-rate assumptions

Greeting/readiness rule:

- `ConversationRuntime` may initialize as `greeting`.
- After outbound `BotStoppedSpeakingFrame`, runtime state must become
  `readiness`.
- For non-greeting/default mode, initialize directly to `readiness` or
  `collecting` based on existing opening behavior.
- Do not leave runtime stuck in `greeting` after `TurnCollector` starts
  accepting user turns.

When `RuntimeResult.should_end_call=True`, the pipeline may speak the closing
text and preserve existing teardown behavior. Phase 3 must not invent new
telephony hangup logic.

## Testing Plan

Phase 3A model tests:

- `Field(default_factory=...)` is used for dict/list fields
- timestamp defaults are timezone-aware
- known phase normalization works
- unknown phase raises `ValueError`
- `RuntimeResult.state` is JSON-safe and represents a snapshot copy
- close actions can set `should_end_call=True` without model-level behavior
  validators

Flow/runtime tests:

- state initializes only from configured slots
- readiness moves to collecting
- outbound greeting completion moves runtime to readiness
- multiple obvious slots collect from one turn
- anti-overcapture examples pass
- ambiguous input does not guess
- attempts increment only on final `ask_slot` or `clarify`
- `answer_then_ask` after a user question does not consume an attempt by
  default
- optional slot max attempts skips
- required slot max attempts closes with `should_end_call=True`
- refusal closes with `should_end_call=True`
- completion closes with `should_end_call=True`

Pipeline regression tests:

- raw finalized transcript still does not trigger assistant response
- EndOfTurn produces exactly one assistant response
- active Phase 3 path does not call `conversation_controller.decide_next_turn()`
- quick overlapping committed turns remain suppressed by the existing lock
- only serialized conversation-state snapshots are stored in `ACTIVE_SESSIONS`

No live Exotel, Deepgram, Cerebras, Cartesia, or voice tests are required for
normal verification.

## Acceptance Criteria

Phase 3 is complete only when:

- `ConversationRuntime` exists
- durable per-call `ConversationState` exists
- `RuntimeResult.state` is a snapshot copy
- slot memory works across committed turns
- deterministic extraction updates only configured slots
- repeated questions are blocked
- final close/refusal/completion returns `should_end_call=True`
- close results return state phase `closing`, not `ended`
- raw finalized STT still does not trigger response
- no new live LLM calls are introduced by default
- no active legacy `decide_next_turn()` call remains
- no runtime service object is stored in `ACTIVE_SESSIONS`
- greeting behavior remains intact
- normal `pytest` passes without live provider dependencies

## Risks

- Overbuilding a flow engine instead of ordered slot collection.
- Regex extraction overcaptures values.
- Attempt counters become off by one.
- Runtime gets stuck in `greeting`.
- Legacy controller remains active by accident.
- Runtime objects leak into `ACTIVE_SESSIONS`.
- Tests over-mock the runtime and miss behavior regressions.

## Handoff To Phase 4

Phase 3 remains deterministic by default. Phase 4 may add bounded LLMAdapter
support for classification, extraction, question answering, and metrics, but
LLM output must remain advisory and must not bypass runtime state,
RepeatedQuestionGuard, or SpeechComposer.

## Phase 3F Final Audit Notes

- Active committed turns use `ConversationRuntime.handle_turn()`.
- The active pipeline no longer calls committed-turn controller APIs.
- `TurnCollector` remains the committed-turn gate.
- Raw finalized STT remains buffer/reference only.
- Deepgram EndOfTurn remains the primary commit signal.
- EagerEndOfTurn and TurnResumed remain non-response triggers.
- `ACTIVE_SESSIONS` stores JSON-safe `conversation_state` snapshots only.
- Runtime, Pipecat, provider, task, lock, and client objects are not stored in
  `ACTIVE_SESSIONS`.
- `CallLog` persists optional `conversation_state`.
- Mongo startup currently tolerates broad startup exceptions for local demo
  resilience. Before production, narrow this to known Mongo, network, or config
  availability errors.
