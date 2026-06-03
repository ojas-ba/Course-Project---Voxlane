# Notes

## 2026-06-02 - Phase 5 live-call readiness regression

- Root cause: readiness detection in `app/services/flow_runner.py` required exact full-string matches, so natural STT text such as `Yeah. Sure.`, `Yeah. Yeah. I'm ready.`, `I'm ready, man.`, and `Yes. Yes.` stayed in readiness and triggered clarification.
- Root cause: readiness clarifications use `slot_id=None` and were intentionally skipped by `RepeatedQuestionGuard`; they did not increment slot attempts or any readiness-specific cap, allowing the same readiness clarification to repeat indefinitely.
- Fix: readiness matching now accepts common affirmative readiness phrases with punctuation/casing normalization while rejecting negated or question-like text. Runtime state now tracks `readiness_attempts`, asks a direct continue/stop prompt for unclear readiness, and closes safely after the configured cap.
- Observability: outbound greeting generation now records JSON-safe pre-dial source/timing metrics, first TTS frame queue time, and first bot audio timestamp. Final call logs now separate phone transport completion from logical conversation outcome and missing required slots.
- Greeting startup: outbound calls now pass `greeting_start_delay_ms=0`, and the default/example setting is `OUTBOUND_GREETING_START_DELAY_MS=0`, so the prepared greeting is queued immediately after WebSocket/pipeline setup.
- Verification: normal `pytest` passed with live provider tests skipped by policy. Real Exotel/audio validation remains manual.
