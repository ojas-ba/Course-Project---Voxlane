# Turn And Conversation Rules

## 1. Purpose

This document prevents random implementation choices during Phase 2, Phase 3, and Phase 4.

It defines:

- how speech becomes a committed user turn
- who owns conversation state
- when the LLM is allowed
- how repeated questions are prevented
- how latency is balanced with correctness

This is a design rulebook, not an implementation spec. It should be used to keep later code changes small, testable, and aligned with the V2 architecture.

## 2. Scope

This document covers:

- TurnCollector
- TurnInput
- ConversationRuntime
- ConversationState
- SlotState
- FlowRunner
- RepeatedQuestionGuard
- LLMAdapter boundaries
- SpeechComposer rules
- latency tradeoffs
- metrics expectations

It does not implement those modules.

## 3. Current Phase 3 Runtime Flow

The current committed-turn runtime path is:

```text
Exotel WebSocket
-> Pipecat pipeline
-> Deepgram Flux STT
-> TurnCollector
-> committed TurnInput
-> UserConversationProcessor._handle_committed_turn()
-> ConversationRuntime.handle_turn()
-> SimpleFlowRunner
-> RepeatedQuestionGuard
-> SpeechComposer
-> RuntimeResult
-> TTSSpeakFrame
-> Cartesia TTS
-> Exotel audio output
```

In `app/services/pipecat_pipeline.py`, `UserConversationProcessor` receives
downstream finalized `TranscriptionFrame` objects for buffering/reference only.
Committed turns come from Deepgram Flux EndOfTurn through `TurnCollector`.
`UserConversationProcessor` then calls `ConversationRuntime.handle_turn()` and
sends `RuntimeResult.assistant_text` to TTS with `TTSSpeakFrame`.

`conversation_controller` no longer owns committed-turn decisions.
`decide_next_turn()` and `apply_turn_decision()` are removed from active app
code. `conversation_controller.generate_outbound_greeting_text()` may still be
used before an outbound call connects to prepare greeting text.

Historical problems Phase 3 fixed:

- finalized STT fragments can trigger assistant responses too early
- LLM currently has too much responsibility for flow decisions
- bot can ask repeated questions because durable slot state is missing
- fallback behavior is not phase-aware enough

Finalized STT text may still be useful for transcript/debugging, but it must not directly trigger assistant response generation in V2.

## 4. Target V2 Flow

The target V2 runtime path is:

```text
Exotel WebSocket
-> Pipecat audio pipeline
-> Deepgram Flux STT
-> TurnCollector
-> committed TurnInput
-> ConversationRuntime
-> FlowRunner
-> LLMAdapter only if needed
-> SpeechComposer
-> Cartesia TTS
-> Exotel audio output
```

The pipeline remains the audio/frame routing layer. Conversation policy moves behind committed-turn and runtime-state contracts.

Phase 3 bridge rule:

- TurnCollector remains the committed-turn gate.
- Active committed turns must route to `ConversationRuntime.handle_turn()`.
- Raw finalized STT remains buffer/reference only.
- Deepgram EndOfTurn remains the primary commit signal.
- EagerEndOfTurn and TurnResumed remain non-response triggers.
- Not allowed: `raw finalized TranscriptionFrame -> committed-turn runtime`.
- Not allowed: committed-turn decisions owned by `conversation_controller`.

## 5. Core Design Principles

- Pipeline is transport, not brain.
- TurnCollector commits user turns.
- ConversationRuntime owns state.
- FlowRunner owns use-case progression.
- LLMAdapter is a bounded helper, not the decision-maker.
- SpeechComposer is the final safety layer before TTS.
- Correctness first, latency second.
- No speculative EagerEndOfTurn speech in V2.
- No always-on filler acknowledgement in V2.
- No random fixed sleeps to hide turn-taking problems.

## 6. Module Boundaries

### `app/services/turn_collector.py`

Responsible for:

- accepting STT/turn-related events from the pipeline layer
- buffering transcript text where needed
- filtering noise/duplicates
- emitting one committed TurnInput per accepted user turn

Not responsible for:

- deciding next question
- extracting slots
- calling LLM
- updating ConversationState

### `app/services/conversation_runtime.py`

Responsible for:

- phase
- active node
- active slot
- slot state
- fallback count
- refusal handling
- repeated-question prevention
- deciding next valid action

### `app/services/flow_runner.py`

Responsible for:

- readiness check
- ordered slot collection
- closing path
- optional slot skipping
- simple flow progression

### `app/services/llm_adapter.py`

Responsible for:

- intent classification
- slot extraction
- correction/refusal detection
- user question answering
- optional phrasing

Not responsible for:

- global conversation flow
- arbitrary next-slot selection
- resetting state
- bypassing repeated-question rules
- speaking directly to TTS
- pushing Pipecat frames, TTS frames, or pipeline frames
- mutating runtime state directly

### `app/services/speech_composer.py`

Responsible for:

- final assistant text
- one question at a time
- short response
- no repeated collected-slot question
- safe fallback if generated text is bad

### `app/models/conversation_models.py`

Future model file for:

- TurnInput
- TurnResult
- ConversationState
- SlotState
- SlotUpdate
- ConversationEvent

Rejected names unless a future design doc explicitly changes them:

- turn_manager
- dialog_manager
- conversation_engine
- brain_controller

## 7. TurnCollector Contract

TurnCollector converts noisy STT/turn events into committed TurnInput objects.

Conversation logic must never run directly from raw finalized STT fragments.

Bad flow:

```text
TranscriptionFrame.finalized
-> committed-turn decision
-> assistant speaks
```

Good flow:

```text
STT fragments / turn events
-> TurnCollector
-> committed TurnInput
-> ConversationRuntime
```

If Deepgram Flux EndOfTurn is exposed directly, use it as the commit signal.

If Pipecat exposes Deepgram turn completion through a frame/event abstraction, use that equivalent turn-complete signal.

Do not invent a custom sleep-based debounce as the primary commit mechanism.

Turn-complete signal discovery gate:

- Before implementing TurnCollector, inspect the actual Pipecat/Deepgram frames or events available in the current pipeline.
- Document which frames/events are observed and which one is used as the commit signal.
- If no reliable provider or turn-complete signal is available, do not invent arbitrary sleeps as the primary solution.
- Pause for review before choosing fallback behavior if commit-signal exposure is unclear.

## 8. TurnInput Contract

Target shape:

```python
class TurnInput(BaseModel):
    call_sid: str
    stream_sid: str | None = None
    turn_index: int

    text: str
    normalized_text: str

    phase: str

    source: Literal[
        "deepgram_flux_end_of_turn",
        "pipecat_turn_complete",
        "manual_test",
        "fallback",
    ]

    started_at: datetime | None = None
    committed_at: datetime

    provider_metadata: dict = Field(default_factory=dict)
```

Rules:

- TurnInput must be JSON-safe.
- TurnInput must not contain provider objects.
- TurnInput must not contain Pipecat service objects.
- One user turn should produce at most one TurnInput.

## 9. Turn Commit Rules

Conservative V2 rules:

- A provider/end-of-turn-complete signal is required to commit a user turn.
- Deepgram Flux EndOfTurn is preferred if directly available.
- A Pipecat turn-complete abstraction is acceptable if that is what the current integration exposes.
- Update/interim transcript events are transcript/reference only.
- EagerEndOfTurn may be logged but must not trigger assistant speech in V2.
- TurnResumed may be logged and used later for speculation cancellation, but V2 should not use speculation yet.
- Raw finalized TranscriptionFrame alone must not trigger assistant response.

Filtering rules:

- Ignore empty text.
- Ignore punctuation-only text.
- Ignore duplicate commits for the same provider turn id if available.
- If no provider turn id exists, use a session-local `turn_index` and recent committed `normalized_text`/timestamp guard.
- Do not trigger conversation logic while `greeting_audio_pending=True`.
- User speech during greeting may be logged as a debug event, but must not be treated as a valid user turn.
- Avoid storing excessive raw audio, raw provider payloads, or provider objects for greeting-time speech.
- Do not add artificial long sleeps after EndOfTurn.
- Prefer provider turn events over custom random debounce logic.

Latency rule:

- Do not make TurnCollector slow with unnecessary fixed sleeps.
- Slightly slower correct EndOfTurn is acceptable.
- Random extra delay is not acceptable.

## 10. Greeting And Bot-Speaking Rules

Preserve current working greeting behavior.

Rules:

- During `greeting_audio_pending=True`, user speech must not trigger conversation logic.
- After `BotStoppedSpeakingFrame` for greeting, phase can move to readiness/task as designed.
- While bot is speaking, user audio may remain muted using the existing Pipecat mute strategy.
- Do not implement complex barge-in behavior in Phase 2.
- Do not break the current outbound greeting path.

## 11. ConversationRuntime Contract

ConversationRuntime receives only committed TurnInput.

It owns:

- phase
- active_node_id
- active_slot_id
- collected slots
- skipped slots
- confirmed slots
- fallback count
- refusal count
- next valid action

It must not let the LLM decide the whole flow.

Processing lock rule:

- The runtime must prevent overlapping assistant response generation for the same call.
- While a committed turn is being processed, additional user input must not start a second concurrent ConversationRuntime task.
- While the assistant is speaking, additional user input must not create parallel assistant responses.
- Phase 2 may log, ignore, or defer such input depending on current state, but it must not create overlapping TTS or duplicate response generation.

## 12. ConversationState Rules

ConversationState must be serializable and safe to store in runtime registries.

Rules:

- Do not store Pipecat objects.
- Do not store provider service objects.
- Do not store live transport/task/runner objects.
- Keep only call/session IDs, phase, active node/slot, slot state, transcript metadata, counters, timestamps, and debug-safe metadata.
- Do not store `ConversationRuntime` itself in `ACTIVE_SESSIONS`.
- `ACTIVE_SESSIONS` stores only JSON-safe `conversation_state` snapshots.
- `CallLog` persists optional `conversation_state` so final slot state survives session cleanup.

## 13. SlotState Rules

Statuses:

- missing
- partial
- collected
- skipped
- confirmed
- invalid

Rules:

- A collected slot must not be asked again.
- A skipped slot must not be asked again unless user correction reopens it.
- A partial slot can be clarified.
- An invalid slot can be reprompted until max attempts.
- Optional slots can be skipped after max attempts.
- Required slot failure should trigger fallback or graceful close.

Example:

```text
User: "I am Ojas from Bangalore."

Expected:
name = Ojas -> collected
location = Bangalore -> collected
next question = role
```

## 14. FlowRunner Rules

Minimum V2 behavior:

- readiness check
- ordered slot collection
- closing node
- refusal path
- fallback path
- one question per turn
- skip optional slot after max attempts
- prevent collected/skipped slot repetition

Out of scope:

- complex branching
- external tools
- Pipecat Flows
- calendar/CRM integrations

## 15. RepeatedQuestionGuard Rules

Before speaking, validate:

- Assistant must not ask for a slot already collected.
- Assistant must not ask for a slot already skipped.
- Assistant must not ask the same question twice in a row unless it is a controlled reprompt.

If violation is detected:

- replace with next valid slot question
- or safe fallback
- or close if no valid next action exists

## 16. Refusal And Stop Rules

Examples:

- stop
- not now
- I don't want to continue
- call me later
- I am not doing this

Correct behavior:

- Acknowledge briefly.
- Do not ask repeat.
- Do not continue slot collection.
- Set `should_end_call=True`.

Example assistant text:

```text
No problem, we can stop here. Thank you.
```

## 17. User Question Rules

If user asks a question mid-flow:

- Answer briefly.
- Then return to the active slot if appropriate.
- Do not forget current state.
- Do not restart the flow.

Example:

```text
User: "Why do you need my location?"

Assistant:
"I need it to match the correct opening. Are you currently based in Bangalore?"
```

## 18. LLMAdapter Boundaries

LLMAdapter may help with:

- intent classification
- slot extraction
- correction detection
- refusal detection
- short answer generation
- optional phrasing

LLMAdapter must not:

- own phase
- choose arbitrary next slot
- clear existing slot state
- ask repeated questions
- bypass fallback policy
- speak directly to TTS

All LLM outputs are advisory.

ConversationRuntime validates them before SpeechComposer.

## 19. SpeechComposer Rules

SpeechComposer is the final guard.

It must enforce:

- short response
- one question at a time
- no internal debug text
- no JSON/model/provider mention
- no repeated collected-slot question
- safe fallback if assistant_text is empty, unsafe, too long, or repeats a completed/skipped slot question

Length and shape targets:

- Default assistant response target: 1-2 short sentences.
- Default question response: answer briefly, then ask one question.
- Avoid long explanations unless explicitly needed.

Bad:

```text
Based on your slot state, I will now proceed to the next node...
```

Good:

```text
Thanks. What role are you applying for?
```

## 20. Latency Rules

The V2 latency strategy is:

- correct turn-taking first
- avoid unnecessary LLM calls
- keep prompts small
- keep assistant responses short
- measure every stage

Do not implement yet:

- EagerEndOfTurn speculative response
- two-lane fast/slow architecture
- always-on filler acknowledgements
- sub-1s optimization

Acceptable prototype target:

```text
Local/ngrok p50 bot first audio after committed turn: around or under 2 seconds.
p95 should be measured honestly.
Do not claim production latency from local ngrok.
```

No random delay rule:

- Do not add fixed sleeps to make turns feel safer unless there is a specific tested reason.
- Prefer provider turn events and measurable thresholds.

## 21. Metrics Rules

Track eventually:

- stt_first_partial_at
- stt_end_of_turn_at
- turn_committed_at
- conversation_runtime_start_at
- llm_request_start_at
- llm_first_token_at
- llm_done_at
- tts_request_start_at
- tts_first_audio_at
- assistant_audio_done_at
- turn_total_latency_ms

Phase 2 only needs enough logging/metrics to verify:

- raw fragments did not trigger response
- turn-complete signal committed exactly once
- TurnInput was produced

Phase 2 debug fields should make turn decisions explainable:

- turn_commit_source
- ignored_reason
- raw_text
- normalized_text
- turn_index
- greeting_pending_at_event
- duplicate_detected
- commit_timestamp

## 22. Testing Scenarios

### Phase 2 tests

- turn-complete signal commits exactly once
- raw finalized fragments do not trigger response
- EagerEndOfTurn does not trigger assistant response
- empty text ignored
- punctuation-only text ignored
- duplicate text ignored
- speech during greeting pending ignored for conversation logic
- TurnInput shape valid

### Phase 3 tests

- collect multiple slots from one turn
- do not repeat collected slots
- correct previous slot
- handle refusal
- answer user question mid-flow
- optional slot skip
- required slot failure close

### Phase 4 tests

- structured extraction success
- invalid JSON fallback
- schema validation failure
- 429 handling
- timeout handling
- LLM metrics recorded
- ConversationRuntime validates LLM output before speech

## 23. Out Of Scope

Explicitly out of scope for this design checkpoint:

- runtime code changes
- test code changes
- TurnCollector implementation
- ConversationRuntime implementation
- Pipecat Flows implementation
- EagerEndOfTurn optimization
- sub-1s latency optimization
- profile editor UI
- DB-backed config storage

## 24. Phase Mapping

- Phase 2 implements TurnCollector and committed TurnInput emission.
- Phase 3 implements ConversationRuntime, ConversationState, SlotState, FlowRunner, RepeatedQuestionGuard, SpeechComposer, and the active runtime bridge.
- Phase 4 implements LLMAdapter and full LLM/provider metrics.
- Phase 5 stabilizes demo behavior.

## 25. Acceptance Criteria

This docs-only task is complete when:

- `docs/design/TURN_AND_CONVERSATION_RULES.md` exists
- `docs/README.md` links to it
- `docs/README.md` source-of-truth priority includes `docs/design/`
- the doc defines module boundaries
- the doc defines TurnCollector commit rules
- the doc defines ConversationRuntime rules
- the doc defines LLM boundaries
- the doc defines SpeechComposer guardrails
- the doc defines latency tradeoffs
- the doc maps rules to Phase 2/3/4
- no runtime code changed
- no tests changed
- no secrets changed

## 26. Phase 3F Future Hardening Notes

- Mongo startup currently tolerates broad startup exceptions for local demo
  resilience. Before production, narrow this to known Mongo, network, or config
  availability errors.
