# PHASE_6_TESTS.md - Normal Non-Voice Verification

## Phase Gate

`PHASE_5_2_DASHBOARD.md` must be marked `DONE` in `PHASE_PROGRESS.md` before this phase starts.

## Objective

Add normal engineering tests that validate schemas, config, transport/serializer parameters, Exotel outbound connection triggering, webhook ExoML output, Jinja2 rendering, and MongoDB persistence shapes without touching real Exotel, real telephony, or actual voice/audio quality.

## Allowed Files

- `tests/test_models.py`
- `tests/test_config.py`
- `tests/test_transport.py`
- `tests/test_logging.py`
- `tests/test_outbound.py` [NEW]
- `tests/test_dashboard.py` [NEW]
- supporting test fixtures limited to non-voice behavior

## In Scope

- **Schema & Config Testing (`test_models.py`, `test_config.py`):** Validate schema validation for `LatencyEvent`, `TurnMetrics`, `CallMetrics`, `CallLog`, and verify settings load environment variables cleanly with Pydantic.
- **Transport & Serializer Wiring (`test_transport.py`):** Test `ExotelFrameSerializer` setup and parameters to ensure native 16 kHz sample rate direct matching without resampling.
- **Logging & persistence (`test_logging.py`):** Validate `normalize_latency_breakdown()` with simulated `LatencyBreakdown` objects.
- **Exotel Outbound & Webhook Testing (`test_outbound.py`):**
  * Mock `httpx.AsyncClient` calls within `ExotelOutboundClient` to ensure HTTP Basic Authentication headers and form-url-encoded bodies connect correctly.
  * Invoke `GET /telephony/webhook` and assert that the returned content is a JSON response containing the `url` key pointing to the ngrok WSS URL for the Voicebot Applet.
- **Dashboard & HTMX Testing (`test_dashboard.py`):**
  * Test `/dashboard` using FastAPI's standard `TestClient` to ensure the Jinja2 template renders and contains the required HTMX cdn reference.
  * Verify `/dashboard/sessions` returns valid HTML fragments matching in-memory active sessions.

## Out of Scope

- Real Exotel calls.
- Live WebSocket/audio exchanges.
- Voice-quality tests.
- End-to-end telephony tests.
- Synthetic audio payload realism tests.

## Acceptance Criteria

- Tests validate the typed models with both valid and invalid inputs.
- Config tests verify expected loading behavior and required fields.
- Outbound dialing trigger mocked successfully with no external network hits.
- Webhook response returns JSON `{"url": "wss://..."}` pointing to the ngrok WSS URL.
- Test coverage is implemented only within the designated files in `tests/`.
- No test depends on live telephony, real audio, or external voice infrastructure.

## Self-Verification

Run normal test execution only, using the project's standard pytest command:
`pytest`

Do not add or claim any test that proves real voice behavior, live transport quality, or Exotel operational correctness.

## Never

- Build fake end-to-end voice tests and represent them as meaningful voice-quality validation.
- Require active Exotel API credentials or internet connections for normal test execution.
- Rely on real network voice services for this test phase.

## Completion Checklist

- [ ] `tests/test_models.py` exists and covers valid and invalid schema inputs.
- [ ] `tests/test_config.py` exists and covers normal settings behavior.
- [ ] `tests/test_transport.py` exists and stays at wiring level only.
- [ ] `tests/test_logging.py` exists and validates data shape and breakdown normalization.
- [ ] `tests/test_outbound.py` exists and validates Exotel client mocks and webhook JSON responses.
- [ ] `tests/test_dashboard.py` exists and validates Jinja2 templates and HTMX session polling.
- [ ] Test suite executes and passes successfully with no external dependencies.
