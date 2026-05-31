# Testing And Demo Notes

- Local ngrok environment is for functional validation.
- Do not judge production latency from local ngrok.
- Current goal is single-call feasibility demo.
- Automated tests do not fully validate live voice behavior.

## Demo Success Criteria

- Greeting works.
- User can give multi-field answer.
- Bot does not ask already-collected information again.
- Bot does not respond to partial fragments.
- Bot handles refusal/stop intent cleanly.
- Dashboard shows transcript, slot state, latency, and errors.