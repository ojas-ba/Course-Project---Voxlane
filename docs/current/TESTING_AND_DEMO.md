# Testing And Demo Notes

- Local ngrok environment is for functional validation.
- Do not judge production latency from local ngrok.
- Current goal is single-call feasibility demo.
- Automated tests do not fully validate live voice behavior.

## Test Execution Policy

- Normal local test runs exclude live provider calls by default (`pytest`).
- Live tests are preserved and can be run explicitly with `pytest -m live` or `RUN_LIVE_TESTS=1 pytest`.
- Live tests require valid API keys and active provider billing/credits.
- Live tests may fail due to provider-side issues (for example `402`, `429`, quota, or outage) even when app logic is correct.

## Demo Success Criteria

- Greeting works.
- User can give multi-field answer.
- Bot does not ask already-collected information again.
- Bot does not respond to partial fragments.
- Bot handles refusal/stop intent cleanly.
- Dashboard shows transcript, slot state, latency, and errors.
