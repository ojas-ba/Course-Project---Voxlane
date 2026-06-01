# Phase 4: LLMAdapter And Metrics

## Goal

Move all Cerebras calls behind `LLMAdapter` and restore end-to-end LLM observability.

## Why This Phase Exists

Current LLM generation and turn-decision logic in `app/services/conversation_controller.py` is mixed with policy and has limited structured observability.

## Scope

- Add `app/services/llm_adapter.py` as single integration boundary for Cerebras operations.
- Enforce structured output contracts for classification/extraction/phrasing operations.
- Add metrics instrumentation for each LLM operation.
- Ensure `ConversationRuntime` validates adapter outputs before speech.

## Out Of Scope

- Rewriting telephony transport.
- Replacing STT/TTS providers.
- Production queue/worker infrastructure.
- Speculative two-lane latency architecture.

## Shared Constraints

- Keep sample rate fixed at `16000`.
- Preserve Exotel webhook/WebSocket behavior and outbound greeting path.
- Preserve immutable config snapshots and serializable runtime state only.
- Do not store live Pipecat objects in runtime registries.
- Do not write every live turn to MongoDB in the hot path.
- Keep live provider tests optional and keep normal `pytest` provider-independent.

## Files Likely Affected

- `app/services/llm_adapter.py` (new)
- `app/services/conversation_runtime.py` (consume adapter operations)
- `app/services/conversation_controller.py` (legacy bridge/deprecation)
- `app/utils/metrics_logger.py` (extend typed metrics normalization if needed)
- `app/models/schemas.py` (LLM metrics shapes if persisted)
- `app/routers/dashboard.py` + `app/templates/dashboard.html` (surface LLM timing/errors)
- `tests/` for adapter behavior + failure handling

## LLMAdapter Design

Adapter must be:

- thin
- bounded
- structured
- measured
- failure-aware
- not the conversation brain

Expected operations:

```python
class LLMAdapter:
    async def classify_turn(...)
    async def extract_slots(...)
    async def detect_correction_or_refusal(...)
    async def answer_user_question(...)
    async def phrase_response(...)
```

## Structured Output Contracts

- Use strict JSON-schema style outputs where supported.
- Validate all adapter outputs before runtime use.
- Reject invalid/empty/unparseable responses into deterministic fallback path.

## Error Handling

Handle and classify:

- `429` queue/rate limit
- `402` payment required
- timeout
- invalid JSON
- schema validation failure
- empty response
- provider connection error

Rules:

- No aggressive hidden retries.
- Bounded retry count with short timeout.
- Phase-aware fallback response when adapter fails.

## Metrics Design

Capture per LLM call:

- `llm_request_id`
- `llm_provider`
- `llm_model`
- `llm_operation`
- `llm_request_start_ms`
- `llm_ttft_ms`
- `llm_total_ms`
- `llm_queue_time`
- `llm_prompt_time`
- `llm_completion_time`
- `llm_input_tokens`
- `llm_output_tokens`
- `llm_total_tokens`
- `llm_status`
- `llm_error_type`
- `llm_retry_count`

## Dashboard Integration

- Show LLM status/timing per turn in call detail timeline.
- Surface provider error types in dashboard log detail.
- Keep existing dashboard usable even when metrics fields are missing.

## Testing Plan

- Successful structured extraction/classification path.
- Invalid JSON fallback.
- Schema validation failure fallback.
- `429` handling.
- timeout handling.
- Metrics recorded on success.
- Metrics recorded on failure.
- `ConversationRuntime` validates adapter output before speech.

## Acceptance Criteria

- All Cerebras calls route through `LLMAdapter`.
- Runtime policy remains owned by `ConversationRuntime`/`FlowRunner`.
- LLM metrics are visible in logs/dashboard and include failure context.
- Normal `pytest` remains provider-independent.

## Risks

- Strict schema failures may increase fallback usage initially.
- Metrics field growth may require careful backward-compatible persistence handling.

## Rollback Plan

- Keep legacy generation call path behind controlled fallback while adapter stabilizes.
- Disable per-call detailed metrics emission if persistence overhead/regressions appear.

## Handoff To Phase 5

- With adapter stability + observability in place, Phase 5 focuses on demo reliability, operator visibility, and manual scenario validation.
