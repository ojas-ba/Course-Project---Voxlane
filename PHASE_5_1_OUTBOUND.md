# PHASE_5_1_OUTBOUND.md - Exotel Outbound Calling

## Phase Gate

`PHASE_4_INTEGRATION.md` must be marked `DONE` in `PHASE_PROGRESS.md` before this phase starts.

## Objective

Build and configure the Exotel Outbound Calling integration. This enables the server to trigger outgoing telephone calls via Exotel's REST API and correctly handle the incoming Voicebot Applet webhook callback, routing call audio into our local FastAPI WebSocket transport.

## Allowed Files

- `app/services/exotel_client.py` [NEW]
- `app/routers/telephony.py`
- `app/config.py`
- `main.py` (lifespan: ExotelOutboundClient lifecycle — approved by user)
- `.env.example`

## In Scope

- **Exotel Outbound Client:** Build `ExotelOutboundClient` in `app/services/exotel_client.py` using `httpx.AsyncClient` to POST to Exotel's `/v1/Accounts/{sid}/Calls/connect` endpoint on the Mumbai cluster (`api.in.exotel.com`). Enforce HTTP Basic Authentication using `EXOTEL_API_KEY` (username) and `EXOTEL_API_TOKEN` (password). Format phone numbers with `0`-prefix for the v1 API.
- **Outbound Webhook Route:** Implement a `GET /telephony/webhook` route in `app/routers/telephony.py` that handles Exotel's Voicebot Applet dynamic URL callback. It must parse the query string params (`CallSid`, `CallFrom`, `CallTo`, `Direction`) and return a JSON response:
  ```json
  {"url": "wss://{PUBLIC_NGROK_URL}/telephony/exotel"}
  ```
  This is the correct format for the Voicebot Applet (bidirectional). The ExoML XML `<Connect><Stream>` pattern is for the Stream Applet (unidirectional only).
- **Configuration Alias Bindings:** Add Pydantic fields in `app/config.py` for Exotel configuration (`EXOTEL_ACCOUNT_SID`, `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN`, `EXOTEL_VIRTUAL_NUMBER`, `EXOTEL_FLOW_ID`, `EXOTEL_SUBDOMAIN`, `PUBLIC_NGROK_URL`).
- **Client Lifecycle:** Attach `ExotelOutboundClient` to the FastAPI lifespan in `main.py` (Option B — create at startup, close at shutdown, stored on `app.state`).
- **Environment Placeholders:** Add all newly required configuration keys in `.env.example`.

## Out of Scope

- HTML templates or CSS stylesheets (Sub-Phase 5.2).
- HTMX frontend browser integration (Sub-Phase 5.2).
- Unit and integration tests (Phase 6).

## Acceptance Criteria

- `ExotelOutboundClient` successfully makes formatted HTTP POST requests to Exotel's connect API.
- Numbers are formatted with `0`-prefix for the Exotel v1 API (not E.164).
- Webhook callback returns valid JSON `{"url": "wss://..."}` with a correctly resolved ngrok WebSocket endpoint.
- All newly added Exotel configuration keys are validated on server launch by Pydantic.
- `ExotelOutboundClient` is created at startup and closed at shutdown via FastAPI lifespan.

## Self-Verification

Run normal checks:
- Verify configuration parses without errors.
- Mock the outbound connection and inspect headers and payload.
- Query `GET /telephony/webhook` using curl or HTTP Client to ensure it yields valid JSON.

## Never

- Hardcode Exotel credentials in the source code.
- Modify active call sessions asynchronously during outbound triggering.
- Claim real outbound dialing completed successfully without actual configuration.

## Completion Checklist

- [ ] New Exotel settings parsed and aliased in `app/config.py`.
- [ ] `.env.example` updated with all required outbound placeholders.
- [ ] `ExotelOutboundClient` implemented using `httpx.AsyncClient` and Basic Auth.
- [ ] Webhook callback route `GET /telephony/webhook` implemented and returning JSON `{"url": "wss://..."}`.
- [ ] `ExotelOutboundClient` managed via FastAPI lifespan in `main.py`.
- [ ] Codebase is structurally ready for Sub-Phase 5.2.
