# Phase 5: Demo Stabilization

## Goal

Stabilize the single-call V2 demo so correctness and observability are reliable in local/ngrok conditions.

## Why This Phase Exists

After core architecture phases, the project must prove repeatable demo behavior under real provider/network variability without over-optimizing prematurely.

## Scope

- Validate end-to-end single-call behavior across critical scenarios.
- Harden fallback/repeated-question/refusal handling in integrated runtime.
- Ensure dashboard surfaces enough detail for debugging and demo confidence.
- Formalize optional live provider test policy and manual demo script.

## Out Of Scope

- Multi-call scaling architecture.
- Production deployment hardening.
- Pipecat Flows integration.
- Adaptive speculative latency optimization.

## Shared Constraints

- Keep sample rate fixed at `16000`.
- Preserve Exotel webhook/WebSocket behavior and outbound greeting path.
- Preserve immutable config snapshots and serializable runtime state only.
- Do not store live Pipecat objects in runtime registries.
- Do not write every live turn to MongoDB in the hot path.
- Keep live provider tests optional and keep normal `pytest` provider-independent.

## Files Likely Affected

- `app/services/conversation_runtime.py`
- `app/services/flow_runner.py`
- `app/services/llm_adapter.py`
- `app/services/pipecat_pipeline.py`
- `app/utils/metrics_logger.py`
- `app/routers/dashboard.py`
- `app/templates/dashboard.html`
- `tests/` (integration + optional live policy assertions)
- `docs/current/TESTING_AND_DEMO.md` (if checklist sync update is needed)

## Demo Scenarios

1. Happy path demo flow
2. Multi-slot answer in one sentence
3. User pauses mid-thought
4. User asks why a question is needed
5. User corrects earlier info
6. User refuses or says stop
7. Provider LLM failure fallback

## Manual Test Script

- Start app and confirm dashboard loads.
- Trigger outbound call with selected/default profile.
- Confirm greeting is spoken.
- Provide multi-slot answer; verify slot extraction and next-question choice.
- Pause mid-thought; verify assistant does not interrupt aggressively.
- Ask "why" question; verify short answer then return to active slot.
- Refuse/stop; verify graceful close.
- Confirm final call log contains snapshot, transcript, metrics, and error summary.

## Dashboard Requirements

Must show:

- `call_sid`
- `stream_sid`
- active phase
- active node
- active slot
- slot state
- transcript
- latency timeline
- provider errors
- fallback count
- repeated-question guard triggers
- final call log

## Latency Reporting

- Do not claim production latency from local ngrok.
- Report measured latency honestly.
- Correctness remains strict.
- Prototype target: p50 under ~2s after committed turn is acceptable when observed consistently.

## Known Local Ngrok Caveats

- Tunnel/network jitter inflates perceived turn latency.
- Provider queue/credit state can create transient failures unrelated to app logic.
- Single-call success is the target; low percentiles at scale are out of scope.

## Live Provider Test Policy

- Live tests remain optional and explicitly invoked.
- Normal `pytest` must pass without provider credits/quota/network.
- Live failures should be reported separately from core regression status.

## Acceptance Criteria

- Demo scenarios above can be run with stable behavior and actionable telemetry.
- Final call logs persist consistently with config snapshot + conversation outcomes.
- Dashboard exposes enough detail to diagnose failures quickly.
- Normal tests remain green without live provider requirements.

## Risks

- Provider instability may mask true application regressions.
- Over-tuning latency may regress conversation correctness.
- Dashboard overload may reduce operator clarity.

## Rollback Plan

- Keep fallback-safe response policies as default if advanced tuning regresses stability.
- Revert non-essential dashboard additions if they reduce reliability.
- Freeze on last stable phase commit before future optimization experiments.

## Future Work After Demo

- Pipecat Flows evaluation
- adaptive two-lane latency optimization
- cloud staging latency comparison
- production deployment design