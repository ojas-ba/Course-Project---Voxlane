# Voice Agent V2 Engineering Specification

## 1. Overview

Voice Agent V2 is a local-development, single-call voice agent prototype designed to evaluate whether a modular stack using Exotel, Pipecat, Deepgram Flux, Cerebras, and Cartesia can realistically replace Gemini Live Voice API behavior in client-style voice applications.

The current prototype already supports FastAPI routes, Exotel telephony/WebSocket handling, Pipecat pipeline wiring, Deepgram Flux STT, Cerebras LLM calls, Cartesia TTS, MongoDB-style completed call logs, and a Jinja2/HTMX dashboard. However, live testing exposed serious conversation-quality issues: the bot reacts to raw finalized STT fragments, asks repeated questions, forgets collected information, interrupts partial user thoughts, hides LLM latency, and uses generic fallback behavior even when the user refuses or wants to stop. 

V2 must fix conversation correctness first. Low latency remains important, but sub-1-second latency is not the immediate target for this prototype.

The primary V2 goal is:

```text
Reliable single-call feasibility demo
+ committed-turn conversation handling
+ durable in-memory task state
+ configurable bot behavior
+ visible latency/quality metrics
```

Not:

```text
production-grade scale
or
fully generic LLM prompt monster
or
speculative sub-1s voice agent
```

---

## 2. Product Goal

Build a configurable voice-agent prototype that can be used for multiple use cases, starting with simple demos such as interview intake, support intake, lead qualification, appointment booking, or feedback collection.

The system should prove:

1. A user can receive an outbound call.
2. The bot can speak an initial greeting.
3. The user can answer naturally.
4. The bot waits for committed user turns instead of reacting to partial STT fragments.
5. The bot extracts useful information into slot state.
6. The bot does not ask for already-collected information again.
7. The bot asks exactly one sensible next thing.
8. The bot handles refusal, stop intent, confusion, and fallback cleanly.
9. The dashboard shows transcript, state, latency, errors, and provider behavior.
10. The stack is feasible enough to compare against Gemini Live Voice API for future client applications.

---

## 3. Non-Goals

The following are explicitly out of scope for the first fixed V2 version:

```text
- Production deployment architecture
- Multi-call concurrency optimization
- Queue/worker architecture
- Horizontal scaling
- Full CRM/calendar/order integrations
- Full drag-and-drop flow builder
- Per-turn MongoDB writes in the hot audio path
- Changing sample rate away from 16000 Hz
- Sub-1s latency guarantee
- Speculative EagerEndOfTurn response generation
- Always speaking filler acknowledgements
- Replacing Pipecat entirely
- Migrating fully to Pipecat Flows before the core turn/state model is stable
```

---

## 4. Current Prototype Context

The current project uses:

```text
FastAPI
Exotel telephony + bidirectional WebSocket
Pipecat audio/pipeline orchestration
Deepgram Flux streaming STT
Cerebras LLM reasoning
Cartesia realtime TTS
MongoDB-style call log persistence
Jinja2/HTMX dashboard
Local ngrok tunnel for Exotel webhook/WebSocket exposure
```

This is not production infra. Local ngrok is used for feasibility testing and manual demos.

Automated tests currently validate configuration, schemas, routing, pipeline wiring, runtime state, logging shape, and non-voice behavior. They do not fully validate real live call behavior, natural turn-taking, telephony latency, or provider reliability. 

---

## 5. Fixed Constraints

These constraints must be preserved unless explicitly changed by a future ADR.

### 5.1 Audio sample rate

Transport and pipeline sample rate must remain fixed at:

```text
16000 Hz
```

The Exotel serializer must continue using:

```python
ExotelFrameSerializer.InputParams(
    exotel_sample_rate=16000,
    sample_rate=16000,
)
```

Do not make sample rate configurable in V2.

### 5.2 Pipecat constraints

Preserve these constraints:

```text
- Pipecat observers must be registered on PipelineTask(..., observers=[...]).
- Observers must not be moved into PipelineParams.
- SileroVADAnalyzer() must remain inside LLMUserAggregatorParams unless a later tested change replaces it.
- User audio should remain muted while bot speaks using existing Pipecat mute strategies.
```

### 5.3 Runtime object storage

Do not store live Pipecat transport/task/runner/service objects inside runtime registries.

Runtime registries may store serializable/session-safe state such as:

```text
call_sid
stream_sid
config snapshot
conversation state
metrics
transcript
timestamps
errors
```

### 5.4 Secrets

`.env` must remain local and must not be edited automatically.

Only `.env.example` may be committed.

---

## 6. Research Basis

Pipecat Flows is relevant because it separates conversation structure from the core Pipecat pipeline. Pipecat defines the core audio/STT/LLM/TTS pipeline, while Flows adds structured conversation progression and context/tool management outside the pipeline. ([Pipecat][1])

Pipecat’s own speech-input docs emphasize that turn detection is more nuanced than detecting a pause; user-turn strategies combine VAD, transcription, minimum-word rules, speech timeout, and turn-detection models to avoid interrupting users or waiting too long. ([Pipecat][2])

Deepgram’s Flux docs recommend starting with a purely `EndOfTurn`-driven implementation for simplicity: use `Update`, `EagerEndOfTurn`, and `TurnResumed` only as transcript/reference signals, and trigger the LLM/agent response only on `EndOfTurn`. ([Deepgram Docs][3])

Flux supports `eot_threshold`, `eager_eot_threshold`, and `eot_timeout_ms`; lower EOT thresholds reduce latency but increase false positives, while higher thresholds improve reliability at the cost of latency. ([Deepgram Docs][4])

For later latency optimization, `EagerEndOfTurn` can be used to start speculative LLM work early, but `TurnResumed` must cancel that draft if the user continues speaking. Deepgram explicitly describes this as more complex and recommends it only after the basic pipeline is stable. ([Deepgram Docs][5])

Cartesia WebSocket TTS is appropriate for realtime voice agents because it supports streaming text input and receiving audio chunks in real time, especially when text is generated incrementally. ([Cartesia Docs][6])

Cerebras supports structured outputs with JSON Schema and `strict: true`, which should be preferred over loose JSON mode for reliable machine-parseable responses. ([Cerebras Inference][7])

Cerebras metrics include TTFT, TPOT, end-to-end latency, queue time, and percentile monitoring; these should be captured in our own dashboard because current LLM timing is hidden. ([Cerebras Inference][8])

Rasa’s slot model is a useful design reference: slots act as assistant memory and store information provided by the user or gathered from external systems. ([Rasa][9])

---

## 7. Target Architecture

Target V2 architecture:

```text
Exotel WebSocket
  -> Pipecat audio pipeline
  -> Deepgram Flux STT
  -> TurnCollector
  -> ConversationRuntime
  -> FlowRunner
  -> LLMAdapter
  -> SpeechComposer
  -> Cartesia TTS
  -> Exotel audio output
```

Core rule:

```text
Pipecat pipeline = audio highway
ConversationRuntime = brain
FlowDefinition = use-case behavior
LLMAdapter = bounded AI helper
UI/DB = behavior configuration
.env = secrets and infrastructure only
```

The pipeline must not directly decide the conversation.

Raw STT finalized fragments must not directly trigger assistant responses.

---

## 8. Module Responsibilities

### 8.1 `app/services/pipecat_pipeline.py`

Responsible for:

```text
- Building Pipecat pipeline
- Wiring Exotel transport
- Wiring Deepgram Flux STT
- Wiring Cartesia TTS
- Registering observers correctly
- Routing frames
- Sending assistant speech frames
- Maintaining audio path constraints
```

Not responsible for:

```text
- deciding next question
- managing slot state
- deciding phase transitions
- calling Cerebras directly for business logic
- handling use-case flow
```

### 8.2 `app/services/turn_collector.py`

Responsible for converting STT/turn events into committed user turns.

It must prevent this bad flow:

```text
TranscriptionFrame.finalized
-> immediately call decide_next_turn()
-> bot replies too early
```

It must implement this flow:

```text
STT fragments / Flux events
-> TurnCollector
-> committed TurnInput
-> ConversationRuntime.handle_turn(...)
```

### 8.3 `app/services/conversation_runtime.py`

Responsible for:

```text
- maintaining phase
- maintaining active flow node
- maintaining active slot
- applying slot updates
- choosing next action
- enforcing no repeated completed-slot questions
- handling refusal/stop intent
- handling fallback by phase
- returning one TurnResult
```

### 8.4 `app/services/task_state.py`

Responsible for state models and helper functions:

```text
ConversationState
SlotState
SlotDefinition
SlotUpdate
FlowProgress
RepeatedQuestionGuard
```

### 8.5 `app/services/flow_runner.py`

Responsible for executing a use-case-specific flow definition.

For V2, implement a simple internal `SimpleFlowRunner`.

Later, Pipecat Flows may be evaluated behind the same interface.

### 8.6 `app/services/llm_adapter.py`

Responsible for all Cerebras calls.

It must be thin, bounded, measurable, and failure-aware.

It should expose methods like:

```python
class LLMAdapter:
    async def classify_turn(...)
    async def extract_slots(...)
    async def detect_correction_or_refusal(...)
    async def answer_user_question(...)
    async def phrase_response(...)
```

### 8.7 `app/services/speech_composer.py`

Responsible for converting a `TurnResult` into final assistant speech.

It must enforce:

```text
- short responses
- one question at a time
- no duplicate acknowledgement
- no repeated completed-slot question
- no giant paragraph unless explicitly required
```

### 8.8 `app/services/greeting_service.py`

Responsible for:

```text
- outbound greeting generation
- greeting validation
- greeting fallback
```

The current working greeting path should be preserved.

### 8.9 `app/services/metrics.py`

Responsible for:

```text
- per-call latency events
- per-turn latency events
- provider timing
- LLM timing
- TTS first-audio timing
- STT/turn commit timing
- error classification
```

---

## 9. Configuration Model

### 9.1 Configuration boundary

`.env` should contain only secrets and infrastructure.

Keep in `.env`:

```text
DEEPGRAM_API_KEY
CEREBRAS_API_KEY
CARTESIA_API_KEY
MONGO_URI
MONGO_DB_NAME
EXOTEL_ACCOUNT_SID
EXOTEL_API_KEY
EXOTEL_API_TOKEN
EXOTEL_VIRTUAL_NUMBER
EXOTEL_FLOW_ID
EXOTEL_SUBDOMAIN
PUBLIC_NGROK_URL
```

Move to UI/DB-backed config:

```text
system prompt
scenario instructions
greeting prompt
greeting fallback text
LLM model
LLM temperature
LLM max tokens
reasoning effort, if supported
TTS voice
TTS model
TTS speed/emotion options, if supported
STT model
Deepgram Flux EOT profile
flow/use-case type
slot schema
closing behavior
fallback behavior
demo phone number defaults
```

### 9.2 Immutable call snapshot

Every call must use an immutable config snapshot.

Once a call starts, changes in the UI must not affect that active call.

Call logs must persist the snapshot used for that call.

### 9.3 V2 configuration implementation rule

For V2, behavior config should move out of `.env`, but this can be implemented incrementally.

Minimum Phase 1 requirement:

- keep secrets and infrastructure in `.env`
- introduce BotProfile / FlowDefinition / ProviderConfig models
- provide default seeded configs so the app works immediately
- allow dashboard selection/editing of behavior config
- preserve immutable config snapshots for active and completed calls

The first implementation does not need a polished production-grade config UI. A simple dashboard form or JSON editor is acceptable.

---

## 10. Data Models

Use Pydantic models where practical.

### 10.1 `BotProfile`

```python
class BotProfile(BaseModel):
    id: str
    name: str
    description: str | None = None

    flow_id: str

    stt_config_id: str
    llm_config_id: str
    tts_config_id: str

    greeting_config: GreetingConfig
    fallback_policy: FallbackPolicy

    is_active: bool = True

    created_at: datetime
    updated_at: datetime
```

### 10.2 `STTConfig`

```python
class STTConfig(BaseModel):
    id: str
    provider: Literal["deepgram"]
    model: str = "flux-general-en"

    sample_rate: int = 16000

    eot_threshold: float = 0.75
    eot_timeout_ms: int = 1500

    eager_eot_threshold: float | None = None

    language_hint: list[str] | None = None
```

Default V2 should use conservative `EndOfTurn` behavior.

Suggested initial values:

```text
eot_threshold: 0.75
eot_timeout_ms: 1500
eager_eot_threshold: null
```

These values should be treated as tunable defaults, not final universal truth.

### 10.3 `LLMConfig`

```python
class LLMConfig(BaseModel):
    id: str
    provider: Literal["cerebras"]
    model: str

    temperature: float = 0.2
    max_tokens: int = 160
    timeout_secs: float = 8.0
    max_retries: int = 1

    structured_outputs: bool = True
    reasoning_effort: str | None = "low"
```

Notes:

```text
- Keep prompts small.
- Use strict JSON Schema for classification/extraction.
- Avoid large transcript prompts.
- Use state summary instead of full conversation history.
```

### 10.4 `TTSConfig`

```python
class TTSConfig(BaseModel):
    id: str
    provider: Literal["cartesia"]
    model: str
    voice_id: str

    sample_rate: int = 16000

    max_buffer_delay_ms: int = 0
    text_aggregation_mode: str = "sentence"
```

### 10.5 `GreetingConfig`

```python
class GreetingConfig(BaseModel):
    mode: Literal["static", "llm_generated"] = "static"
    static_text: str
    generation_prompt: str | None = None
    generation_timeout_secs: float = 3.0
    fallback_text: str
```

### 10.6 ProviderConfig storage rule

Provider configs may be stored as separate typed records:

- STTConfig
- LLMConfig
- TTSConfig

or under a generic provider config collection if simpler for V2.

The app must still validate provider-specific fields with typed Pydantic models before use.

Do not pass raw unvalidated UI config directly into the live pipeline.

### 10.7 `FlowDefinition`

```python
class FlowDefinition(BaseModel):
    id: str
    name: str
    version: str
    description: str | None = None

    use_case_type: Literal[
        "interview",
        "support_intake",
        "lead_qualification",
        "appointment_booking",
        "feedback_collection",
        "custom"
    ]

    slots: list[SlotDefinition]
    nodes: list[FlowNode]
    start_node_id: str
    fallback_rules: list[FallbackRule]

    created_at: datetime
    updated_at: datetime
```

### 10.8 `SlotDefinition`

```python
class SlotDefinition(BaseModel):
    id: str
    label: str
    description: str | None = None

    type: Literal["text", "number", "boolean", "categorical", "phone", "email", "date", "datetime"]

    required: bool = True
    allowed_values: list[str] | None = None

    question: str
    reprompt: str | None = None
    confirmation_required: bool = False

    max_attempts: int = 2
```

### 10.9 `FlowNode`

```python
class FlowNode(BaseModel):
    id: str
    type: Literal[
        "message",
        "readiness_check",
        "slot_collection",
        "question_answer",
        "confirmation",
        "closing",
        "handoff",
        "custom"
    ]

    slot_id: str | None = None
    assistant_text: str | None = None

    next_node_id: str | None = None
    on_success_node_id: str | None = None
    on_skip_node_id: str | None = None
    on_refusal_node_id: str | None = None
    on_error_node_id: str | None = None
```

### 10.10 `ConversationState`

```python
class ConversationState(BaseModel):
    call_sid: str
    stream_sid: str | None = None

    phase: Literal["greeting", "readiness", "task", "closing", "ended"]

    bot_profile_snapshot: dict
    flow_snapshot: dict

    active_node_id: str | None = None
    active_slot_id: str | None = None

    slots: dict[str, SlotState]

    transcript_events: list[TranscriptEvent] = []
    recent_events: list[ConversationEvent] = []

    last_user_text: str | None = None
    last_assistant_text: str | None = None
    last_assistant_question_slot_id: str | None = None

    fallback_count: int = 0
    refusal_count: int = 0

    started_at: datetime
    updated_at: datetime
```

### 10.11 `SlotState`

```python
class SlotState(BaseModel):
    slot_id: str
    value: Any | None = None

    status: Literal[
        "missing",
        "partial",
        "collected",
        "skipped",
        "confirmed",
        "invalid"
    ] = "missing"

    attempts: int = 0
    last_updated_turn_index: int | None = None
    confidence: float | None = None
    source_text: str | None = None
```

### 10.12 `TurnInput`

```python
class TurnInput(BaseModel):
    call_sid: str
    turn_index: int

    text: str
    normalized_text: str

    phase: str

    started_at: datetime | None = None
    committed_at: datetime

    source: Literal["deepgram_flux_end_of_turn", "manual_test", "fallback"]
    provider_metadata: dict = {}
```

### 10.13 `TurnResult`

```python
class TurnResult(BaseModel):
    assistant_text: str

    phase: Literal["greeting", "readiness", "task", "closing", "ended"]

    slot_updates: list[SlotUpdate] = []

    next_node_id: str | None = None
    active_slot_id: str | None = None

    should_end_call: bool = False
    end_reason: str | None = None

    fallback_used: bool = False
    llm_used: bool = False

    debug: dict = {}
```

---

## 11. Runtime Call Flow

### 11.1 Outbound call flow

```text
1. Dashboard user selects BotProfile.
2. Dashboard user enters target phone number.
3. App validates target number.
4. App builds immutable bot_profile_snapshot.
5. App prepares greeting text:
   - static greeting, or
   - generated greeting with timeout and fallback.
6. App creates pending outbound context.
7. App calls Exotel outbound API.
8. Exotel answers and hits telephony webhook.
9. App returns Exotel WebSocket URL.
10. Exotel connects to /telephony/exotel.
11. App resolves CallSid and StreamSid.
12. App creates ACTIVE_SESSION with ConversationState.
13. App starts Pipecat pipeline.
14. Bot speaks greeting.
15. After BotStoppedSpeakingFrame, phase becomes readiness/task depending on flow.
```

### 11.2 User turn flow

```text
1. User speaks.
2. Deepgram Flux streams transcript/turn events.
3. TurnCollector observes events.
4. TurnCollector emits TurnInput only after committed turn.
5. ConversationRuntime.handle_turn(TurnInput, ConversationState) runs.
6. FlowRunner identifies current node/slot.
7. LLMAdapter is called only if needed.
8. SlotState is updated.
9. RepeatedQuestionGuard validates next response.
10. SpeechComposer creates final assistant_text.
11. Pipecat sends text to Cartesia TTS.
12. TTS audio is streamed back through Exotel.
13. Transcript and metrics are updated.
```

### 11.3 Call completion flow

```text
1. Call ends normally, user refuses, or error occurs.
2. Pipeline shuts down.
3. Final transcript, metrics, state snapshot, config snapshot, and error summary are assembled.
4. Final call log is persisted to MongoDB.
5. ACTIVE_SESSION entry is deleted.
```

Do not write every live turn to MongoDB in the hot path for V2 unless needed for debugging. In-memory state is sufficient for single-call demo scope.

---

## 12. TurnCollector Requirements

### 12.1 Core requirement

Conversation logic must only receive committed turns.

Do not call `ConversationRuntime.handle_turn(...)` from raw finalized STT fragments.

### 12.2 V2 default strategy

Use Deepgram Flux `EndOfTurn` as the primary committed-turn signal.

For V2:

```text
Update: transcript reference only
EagerEndOfTurn: ignored or logged only
TurnResumed: ignored or logged only
EndOfTurn: commit user turn
StartOfTurn: potential barge-in signal, but do not overreact
```

Do not enable speculative eager response behavior yet.

TurnCollector V2 should be conservative.

It should prefer slightly slower but correct turn commits over fast but interrupting behavior.

It should not use `EagerEndOfTurn` to trigger bot speech in V2.

If `EagerEndOfTurn` and `TurnResumed` are available, they may be logged for future latency experiments, but they must not drive assistant response generation yet.

### 12.3 Fragment handling

TurnCollector must handle:

```text
- duplicate STT fragments
- tiny non-useful text
- user saying only filler/noise
- partial thoughts
- user speaking during greeting
- user interruption while bot is speaking
- provider event ordering issues
```

### 12.4 Minimum usable turn rules

A committed user turn is usable if:

```text
- text is non-empty after normalization
- text is not only punctuation/noise
- text is not a duplicate of already-processed turn_index
- bot is not in a phase where user audio must be ignored
```

### 12.5 Incomplete turn handling

If the user turn appears incomplete, the assistant should not proceed aggressively.

Examples of incomplete user turns:

```text
"Actually I was..."
"Wait, so..."
"My name is..."
"I mean..."
```

Default behavior:

```text
- Ask a gentle clarification.
- Do not move to next slot unless enough information was extracted.
```

Example:

```text
"Sorry, I missed the complete answer. Could you say that once more?"
```

---

## 13. ConversationRuntime Requirements

### 13.1 App owns the flow

The LLM must not decide the entire conversation flow from scratch every turn.

The app owns:

```text
phase
active node
active slot
collected slots
skipped slots
next required question
refusal handling
closing behavior
repeated-question prevention
```

The LLM helps with:

```text
readiness classification
intent classification
slot extraction
correction detection
refusal detection
simple question answering
optional phrasing
```

### 13.2 One turn in, one response out

Every committed user turn should produce at most one assistant response.

Avoid:

```text
assistant ack
assistant clarification
assistant question
assistant extra explanation
```

unless absolutely necessary.

### 13.3 One question at a time

The assistant should ask exactly one next thing.

Bad:

```text
What is your name, where are you located, and what role are you applying for?
```

Good:

```text
What role are you applying for?
```

### 13.4 Slot progression

For each active slot:

```text
1. Ask slot question.
2. Wait for committed turn.
3. Extract value.
4. Validate value.
5. Mark collected/partial/invalid/skipped.
6. Move to next slot or close.
```

### 13.5 Repeated-question prevention

Before speaking, validate that the assistant is not asking for a slot already marked:

```text
collected
confirmed
skipped
```

If the LLM suggests a repeated question, block it.

Fallback:

```text
- Ask the next missing slot.
- Or continue to the next flow node.
- Or close if nothing remains.
```

### 13.6 Correction handling

The user may correct previously collected info.

Example:

```text
"No, I said Bangalore, not Mangalore."
```

The system should detect correction intent and update the relevant slot.

### 13.7 Refusal handling

The user may refuse or stop.

Examples:

```text
"I don't want to continue."
"Stop."
"Not now."
"I am not doing this."
"Call me later."
```

The system must not ask them to repeat.

Correct behavior:

```text
No problem, we can stop here. Thank you.
```

Then mark `should_end_call=True`.

### 13.8 User question handling

The user may ask a question instead of answering.

Example:

```text
"Why do you need my location?"
```

The system should answer briefly, then return to the active slot if appropriate.

Example:

```text
"I need it to match you with the correct opening. Are you currently based in Bangalore?"
```

---

## 14. FlowRunner Requirements

### 14.1 SimpleFlowRunner

Implement a simple in-house flow runner first.

It must support:

```text
message node
readiness_check node
slot_collection node
question_answer node
confirmation node
closing node
```

For the first V2 implementation, FlowDefinition does not need to support every possible conversation graph feature.

Minimum required support:

- ordered slot collection
- readiness check
- closing node
- refusal path
- fallback path
- one question per turn
- skip optional slot after max attempts
- prevent asking collected/skipped slots again

Advanced branching, external tools, and complex conditional routing are future work.

### 14.2 Flow interface

```python
class FlowRunner(Protocol):
    async def handle_turn(
        self,
        turn: TurnInput,
        state: ConversationState,
    ) -> FlowStepResult:
        ...
```

### 14.3 Pipecat Flows compatibility

The architecture should allow future replacement or integration with Pipecat Flows.

Do not tightly couple all business logic to Pipecat Flows yet.

Recommended future interface:

```text
SimpleFlowRunner now
PipecatFlowsRunner later
same ConversationRuntime interface
```

Pipecat Flows should be evaluated after the base V2 bot works.

---

## 15. LLMAdapter Requirements

The LLMAdapter should not own conversation policy.

It should not decide the next slot globally.
It should not reset phase.
It should not ignore existing SlotState.
It should not ask repeated questions.

Its outputs are advisory and must be validated by ConversationRuntime before speech.

### 15.1 Bounded LLM usage

Do not send giant prompts or full transcript history on every turn.

Each LLM call should include:

```text
- current phase
- active slot
- slot definitions relevant to current node
- short state summary
- latest committed user turn
- required JSON schema
```

### 15.2 Structured outputs

Use Cerebras structured outputs with strict JSON Schema where supported.

Prefer:

```json
{
  "type": "json_schema",
  "json_schema": {
    "name": "turn_analysis",
    "strict": true,
    "schema": {}
  }
}
```

over loose JSON-only prompting.

### 15.3 Turn analysis schema

```json
{
  "intent": "answer | question | correction | refusal | unclear",
  "slot_updates": [
    {
      "slot_id": "string",
      "value": "any",
      "status": "missing | partial | collected | skipped | confirmed | invalid",
      "confidence": 0.0
    }
  ],
  "user_question": "string | null",
  "refusal_reason": "string | null",
  "assistant_text_suggestion": "string | null",
  "should_end_call": false
}
```

The app must validate all LLM outputs.

### 15.4 Error handling

LLM errors must not crash the conversation.

Handle:

```text
429 rate limit
402 payment required
timeout
invalid JSON
schema validation failure
empty response
provider connection error
```

For 429 or timeout:

```text
- log provider error
- do not retry aggressively
- use phase-aware fallback
```

Cerebras documents 429 as rate limiting and supports retry/timeout configuration; V2 should configure short timeouts and controlled retry behavior rather than letting provider defaults create long hidden waits. ([Cerebras Inference][10])

### 15.5 LLM metrics

Every LLM call must log:

```text
llm_request_id
llm_provider
llm_model
llm_operation
llm_request_start_ms
llm_ttft_ms, if available
llm_total_ms
llm_queue_time, if available
llm_prompt_time, if available
llm_completion_time, if available
llm_input_tokens
llm_output_tokens
llm_total_tokens
llm_status
llm_error_type
llm_retry_count
```

Cerebras completion responses can include `time_info` such as queue time, prompt time, completion time, and total time; capture these when available. ([Cerebras Inference][11])

---

## 16. SpeechComposer Requirements

SpeechComposer must convert `TurnResult` into safe assistant speech.

SpeechComposer is the final safety layer before TTS.

If assistant_text is empty, unsafe, too long, or repeats a completed slot question, SpeechComposer should replace it with a safe phase-aware fallback or next valid question.

### 16.1 Rules

```text
- Keep output short.
- Ask one question.
- Do not repeat already-collected slot questions.
- Do not include internal state/debug info.
- Do not mention JSON, slots, models, or providers.
- Do not apologize repeatedly.
- Do not speak giant paragraphs.
```

### 16.2 Good examples

```text
"Thanks. Which role are you applying for?"

"Got it. Where are you currently based?"

"No problem, we can stop here. Thank you."

"I need your location only to match the correct opening. Are you currently based in Bangalore?"
```

### 16.3 Bad examples

```text
"Based on your previous answer and current slot state, I will now proceed..."

"Sorry, I want to make sure I got that right. Could you repeat that briefly?"
```

The second one is only acceptable as a limited fallback, not as universal fallback.

---

## 17. Fallback Policy

Fallback must be phase-aware.

### 17.1 Readiness fallback

If phase is `readiness`:

```text
"Should I continue, or would you prefer to stop here?"
```

### 17.2 Active slot fallback

If phase is `task` and active slot exists:

```text
"Sorry, I missed that. Could you repeat your {slot_label}?"
```

### 17.3 Refusal fallback

If user likely refused:

```text
"No problem, we can stop here. Thank you."
```

### 17.4 LLM failure fallback

If LLM fails but current active slot is known:

```text
"Sorry, I missed that. Could you say that once more?"
```

If repeated failures occur:

```text
"Sorry, I’m having trouble processing this. We can stop here for now."
```

Then close.

### 17.5 Max fallback count

Suggested V2 default:

```text
max_fallback_count_per_call = 3
max_fallback_count_per_slot = 2
```

After limits:

```text
- mark slot skipped if optional
- close if required and impossible to collect
```

---

## 18. Dashboard/UI Requirements

V2 dashboard should remain simple and prototype-focused.

### 18.1 Required pages/sections

```text
Bot Profiles
Flow Definitions
Provider Settings
Demo Call Launcher
Live Session View
Call Logs
```

### 18.2 Bot Profiles UI

Must allow:

```text
- create bot profile
- edit bot profile
- choose FlowDefinition
- choose STT/LLM/TTS configs
- edit greeting config
- edit fallback policy
- mark profile active/inactive
- select profile for demo outbound call
```

### 18.3 Flow Definitions UI

For V2, a JSON/YAML textarea is enough.

No drag-and-drop flow builder yet.

Must support:

```text
- paste/edit FlowDefinition
- validate schema
- save version
- select flow for BotProfile
```

### 18.4 Provider Settings UI

Must allow editing behavior config, not secrets.

Do not expose API keys in UI.

Configurable:

```text
STT model
Deepgram EOT profile
LLM model
temperature
max tokens
timeout
TTS voice
TTS model
```

### 18.5 Demo Call Launcher

Must allow:

```text
- input phone number
- select BotProfile
- start outbound call
- show pending/started/error status
```

### 18.6 Live Session View

Must show:

```text
call_sid
stream_sid
active phase
active node
active slot
collected slots
latest transcript
latest assistant response
latency timeline
provider errors
```

### 18.7 Call Log View

Must show:

```text
full transcript
slot collection summary
phase transitions
config snapshot
provider configs used
latency metrics
error summary
completion status
```

---

## 19. Metrics And Observability

### 19.1 Per-call metrics

```text
call_started_at
ws_connected_at
pipeline_started_at
greeting_tts_started_at
first_bot_audio_at
first_bot_speech_latency_secs
call_ended_at
call_duration_secs
completed
error
end_reason
```

### 19.2 Per-turn metrics

```text
turn_index
user_speech_start_at
stt_first_partial_at
stt_end_of_turn_at
turn_committed_at
conversation_runtime_start_at
llm_request_start_at
llm_first_token_at
llm_response_done_at
tts_request_start_at
tts_first_audio_at
assistant_audio_done_at
turn_total_latency_ms
```

### 19.3 Provider metrics

```text
deepgram_model
deepgram_eot_threshold
deepgram_eot_timeout_ms
deepgram_turn_event_type
deepgram_turn_index

cerebras_model
cerebras_status
cerebras_error_type
cerebras_queue_time
cerebras_prompt_time
cerebras_completion_time
cerebras_total_time
cerebras_input_tokens
cerebras_output_tokens

cartesia_model
cartesia_voice_id
cartesia_tts_first_audio_ms
cartesia_error_type
```

### 19.4 Dashboard metrics

At minimum show:

```text
First bot speech latency
Per-turn user-stop-to-bot-audio latency
LLM total latency
TTS first-audio latency
Provider errors
Turn count
Fallback count
Repeated-question guard triggers
```

Do not rely only on averages. Track p50/p95/p99 once enough calls exist.

---

## 20. Testing Requirements

### 20.1 Unit tests

Add tests for:

```text
TurnCollector
ConversationRuntime
SlotState updates
RepeatedQuestionGuard
FallbackPolicy
SpeechComposer
FlowDefinition validation
BotProfile config snapshot
LLMAdapter schema validation
```

### 20.2 Integration tests

Add tests for:

```text
dashboard outbound route
pending outbound context creation
active session state creation
pipeline wiring
call log finalization
config snapshot immutability
```

### 20.3 Live provider tests

Live provider tests must be optional.

They should not fail normal local CI/local verification because of provider credits, billing, external outages, or API quota.

Mark them as:

```python
@pytest.mark.live
```

Normal run:

```bash
pytest
```

Live validation:

```bash
pytest -m live
```

or:

```bash
RUN_LIVE_TESTS=1 pytest
```

### 20.4 Manual live demo tests

Manual demo checklist:

```text
- greeting works
- user says ready
- user gives multiple details in one answer
- bot extracts details
- bot does not ask already collected details again
- user pauses mid-thought
- bot does not interrupt
- user asks a question
- bot answers briefly and returns to flow
- user refuses/stops
- bot closes gracefully
- dashboard shows transcript/state/latency/errors
```

---

## 21. Demo Acceptance Criteria

V2 is considered demo-ready when one local/ngrok single-call demo can consistently show:

```text
1. Outbound call starts from dashboard.
2. Greeting is spoken.
3. User can respond naturally.
4. Bot waits for committed user turns.
5. Bot does not respond to raw STT fragments.
6. Bot collects at least 3 configured slots.
7. Bot does not repeat collected slots.
8. Bot handles one user question.
9. Bot handles one refusal/stop phrase.
10. Bot closes cleanly.
11. Final call log persists.
12. Dashboard shows useful latency and error visibility.
```

Latency target for local ngrok demo:

```text
Correctness target: strict
Latency target: honest measurement
```

Suggested local prototype target:

```text
p50 bot first audio after committed turn: under 2 seconds
p95: measured and reported honestly
```

Do not claim production latency from local ngrok.

---

## 22. Pipecat Flows Evaluation

Pipecat Flows should not be mandatory in the first fixed V2 implementation.

However, the design should allow it later.

Evaluation criteria:

```text
- Can it represent our FlowDefinition cleanly?
- Can it reduce prompt size per node?
- Can it improve structured state transitions?
- Can it coexist with our TurnCollector?
- Can it preserve dashboard observability?
- Does it make debugging easier or harder?
```

Do not feed raw STT finalized fragments into Pipecat Flows.

Correct future architecture:

```text
Deepgram Flux
-> TurnCollector
-> committed TurnInput
-> PipecatFlowsRunner or SimpleFlowRunner
```

Wrong architecture:

```text
TranscriptionFrame.finalized
-> Pipecat Flows directly
```

---

## 23. Latency Strategy

### 23.1 V2 latency principle

The first fixed V2 version should reduce latency by reducing unnecessary work, not by risky speculative speech.

Do:

```text
- keep state outside the LLM
- keep prompts small
- use strict structured outputs
- cap output tokens
- avoid unnecessary LLM calls
- keep Cartesia WebSocket hot
- speak short responses
- measure every stage
```

Do not:

```text
- lower EOT aggressively just to look faster
- speak filler every turn
- use EagerEndOfTurn speculative speech before correctness is stable
- hide ngrok/provider latency
```

### 23.2 Later adaptive latency path

After V2 is stable, evaluate:

```text
EagerEndOfTurn draft reasoning
TurnResumed cancellation
Fast fallback acknowledgement only when real response is delayed
Response reuse when EagerEndOfTurn transcript matches EndOfTurn
```

This is future work, not V2 core.

---

## 24. Implementation Phases

This spec aligns with the rebuild plan.

Recommended order:

```text
Phase 0: Docs/spec cleanup and baseline
Phase 1: Move behavior config from .env to UI/DB
Phase 2: Add BotProfile and FlowDefinition
Phase 3: Add TurnCollector
Phase 4: Add ConversationState and SlotState
Phase 5: Add SimpleFlowRunner
Phase 6: Add LLMAdapter with metrics
Phase 7: Add repeated-question guard and phase-aware fallback
Phase 8: Improve dashboard logs/metrics
Phase 9: Single-call demo stabilization
Phase 10: Evaluate Pipecat Flows later
```

---

## Phase 1 Readiness Criteria

Phase 1 can start only when:

- docs/specs/VOICE_AGENT_V2_SPEC.md is finalized
- docs/README.md links to the spec
- ADRs align with this spec
- current tests are understood
- live provider tests are either passing or clearly marked optional
- no runtime behavior has been changed by docs/spec work

Phase 1 implementation should start with config/profile foundations, not TurnCollector yet.

---

## 25. V2 Implementation Acceptance Criteria

The V2 implementation should not be considered complete until:

```text
- .env contains only secrets/infrastructure.
- Bot behavior can be configured through UI/DB-backed profiles.
- Outbound greeting still works.
- Raw STT final fragments do not directly trigger bot responses.
- TurnCollector emits committed TurnInput objects.
- ConversationRuntime owns phase and slot state.
- FlowRunner decides next use-case action.
- LLMAdapter is bounded, structured, measured, and failure-aware.
- RepeatedQuestionGuard prevents asking completed/skipped slots.
- Fallback is phase-aware.
- Dashboard shows transcript, slot state, latency, and provider errors.
- Normal tests pass without live provider billing/API dependencies.
- Manual single-call demo succeeds consistently.
```

---

[1]: https://docs.pipecat.ai/pipecat-flows/introduction "Pipecat Flows - Pipecat"
[2]: https://docs.pipecat.ai/pipecat/learn/speech-input "Speech Input & Turn Detection - Pipecat"
[3]: https://developers.deepgram.com/docs/flux/agent "Build a Flux-enabled Voice Agent | Deepgram's Docs"
[4]: https://developers.deepgram.com/docs/flux/configuration "End-of-Turn Detection Parameters | Deepgram's Docs"
[5]: https://developers.deepgram.com/docs/flux/voice-agent-eager-eot "Optimize Voice Agent Latency with Eager End of Turn | Deepgram's Docs"
[6]: https://docs.cartesia.ai/get-started/realtime-text-to-speech-quickstart "Realtime Text to Speech Quickstart - Cartesia Docs"
[7]: https://inference-docs.cerebras.ai/capabilities/structured-outputs "Structured Outputs - Cerebras Inference"
[8]: https://inference-docs.cerebras.ai/capabilities/metrics "Metrics - Cerebras Inference"
[9]: https://rasa.com/docs/reference/primitives/slots/ "Slots | Rasa Documentation"
[10]: https://inference-docs.cerebras.ai/support/rate-limits "Rate Limits - Cerebras Inference"
[11]: https://inference-docs.cerebras.ai/api-reference/completions "Completions - Cerebras Inference"
