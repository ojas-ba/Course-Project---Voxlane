# PHASE_5_2_DASHBOARD.md - HTMX Dashboard and Pages

## Phase Gate

`PHASE_5_1_OUTBOUND.md` must be marked `DONE` in `PHASE_PROGRESS.md` before this phase starts.

## Objective

Design and build the lightweight, local-development dashboard. This UI enables developers to trigger outbound calls, inspect running pipeline sessions, and visualize structured latency events queried directly from MongoDB call logs using HTMX and Jinja2 templates.

## Allowed Files

- `app/templates/dashboard.html` [NEW]
- `app/routers/dashboard.py` [NEW]
- `main.py`

## In Scope

- **Jinja2 Templates Setup:** Configure FastAPI to mount the `app/templates/` folder and render html responses using `Jinja2Templates`.
- **Vanilla CSS Dark Theme:** Style the single-page visualizer directly within the template or a static stylesheet, adopting premium HSL dark-tones,Outfit/Inter fonts, grid-layouts for key stats, and glassmorphic cards.
- **HTMX Active Sessions:** Implement a live call panel that polls `GET /dashboard/sessions` every 2 seconds via HTMX (`hx-get` + `hx-trigger="every 2s"`), returning lightweight HTML snippets representing current active sessions in `ACTIVE_SESSIONS`.
- **Outbound Dialer Panel:** Build an HTML dialer form that targets `POST /telephony/outbound` and uses HTMX to swap the dialing trigger button with a "Ringing customer phone..." status block.
- **Latency Telemetry Timeline:** Retrieve completed logs from MongoDB call-log collections and display them in a list. Clicking a call log must query its details and render a clean chronological timeline of the Turn metrics and raw latency events (`ttfb`, VAD timings).
- **FastAPI Router Mounting:** Mount `app.routers.dashboard` in `main.py`.

## Out of Scope

- Raw Exotel outbound REST HTTP connection client logic (Sub-Phase 5.1).
- Outbound connect webhook XML endpoints (Sub-Phase 5.1).
- Unit and integration tests (Phase 6).

## Acceptance Criteria

- The dashboard loads successfully in the browser at `http://127.0.0.1:8000/dashboard` and displays the grid layout.
- HTMX polls `GET /dashboard/sessions` periodically without memory leaks or console exceptions.
- Triggering an outbound call correctly swaps out HTML form states.
- Latency events and breakdowns are queried from MongoDB and successfully formatted into the visual timeline widget.

## Self-Verification

Run manual dashboard checks:
- Verify `/dashboard` returns valid HTML5.
- Open Chrome DevTools, confirm HTMX fetches `/dashboard/sessions` every 2s, and inspect live HTML swaps.
- Click call logs to verify Mongo queries and timeline rendering.

## Never

- Introduce thick frontend JavaScript frameworks (React, Vue, Vite, npm builds).
- Mutate active call states inside `ACTIVE_SESSIONS` from the dashboard UI.
- Use ad-hoc CSS utilities; stick to a clean, cohesive Vanilla CSS styling system.

## Completion Checklist

- [ ] Templates directory created and FastAPI Jinja2 rendering mounted in `main.py`.
- [ ] `app/templates/dashboard.html` designed with a high-fidelity dark-mode UI and loaded with HTMX.
- [ ] `GET /dashboard` endpoint implemented in `app/routers/dashboard.py` returning the dashboard page.
- [ ] `GET /dashboard/sessions` implemented to render the active sessions HTML fragment.
- [ ] `POST /telephony/outbound` implemented to call Exotel and return ringing status swaps.
- [ ] MongoDB call logs timeline implemented to visualize completed call latency breakdowns.
- [ ] Codebase is structurally ready for Phase 6.
