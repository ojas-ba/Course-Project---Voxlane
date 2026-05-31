# Phase 3 Walkthrough — Logging, Schemas, and Finalization

## Summary

Phase 3 adds structured logging, typed persistence schemas, latency normalization, call-finalization helpers, MongoDB persistence wiring, and observer metric callbacks. No telephony route changes were made.

---

## Files Changed

### New Files

| File | Purpose |
|------|---------|
| [schemas.py](file:///c:/NovaAutomata_Internship/VoiceAgent_Pipecat/app/models/schemas.py) | Pydantic models: `LatencyEvent`, `TurnMetrics`, `CallMetrics`, `CallLog` |
| [logging_config.py](file:///c:/NovaAutomata_Internship/VoiceAgent_Pipecat/app/logging_config.py) | Dual-sink loguru setup (console + JSON file) |
| [metrics_logger.py](file:///c:/NovaAutomata_Internship/VoiceAgent_Pipecat/app/utils/metrics_logger.py) | `normalize_latency_breakdown()`, `build_turn_metrics()`, `finalize_call_log()` |

### Modified Files

| File | What Changed |
|------|-------------|
| [database.py](file:///c:/NovaAutomata_Internship/VoiceAgent_Pipecat/app/database.py) | Replaced Phase 0 placeholder with Motor `AsyncIOMotorClient`, `init_database()`, `persist_call_log()`, `close_database()` |
| [runtime.py](file:///c:/NovaAutomata_Internship/VoiceAgent_Pipecat/app/runtime.py) | Added `update_session_first_latency()` and `append_session_turn_metrics()` helpers |
| [pipecat_pipeline.py](file:///c:/NovaAutomata_Internship/VoiceAgent_Pipecat/app/services/pipecat_pipeline.py) | Registered observer callbacks for `on_latency_measured`, `on_latency_breakdown`, `on_first_bot_speech_latency` |
| [main.py](file:///c:/NovaAutomata_Internship/VoiceAgent_Pipecat/main.py) | Added FastAPI `lifespan` with `setup_logging()` + `init_database()` / `close_database()` |

### Documentation Updated

| File | What Changed |
|------|-------------|
| [PHASE_PROGRESS.md](file:///c:/NovaAutomata_Internship/VoiceAgent_Pipecat/PHASE_PROGRESS.md) | Phase 3 marked `DONE` |
| [PHASE_3_LOGGING.md](file:///c:/NovaAutomata_Internship/VoiceAgent_Pipecat/PHASE_3_LOGGING.md) | All checklist items marked `[x]` |
| [NOTES.md](file:///c:/NovaAutomata_Internship/VoiceAgent_Pipecat/NOTES.md) | Phase 3 decisions recorded, next steps updated |

---

## Key Design Decisions

### Latency Normalization

`LatencyBreakdown.chronological_events()` returns `list[str]` — it discards timestamps. The project requires `{"time": float, "event": str}`. So `normalize_latency_breakdown()` extracts directly from the breakdown's typed sub-fields (`.ttfb`, `.text_aggregation`, `.user_turn_start_time`, `.user_turn_secs`, `.function_calls`), preserving both `start_time` and the formatted label.

### Observer Callback Pairing

`on_latency_measured` fires immediately before `on_latency_breakdown` in Pipecat's `_handle_bot_started_speaking()`. Both create separate `asyncio.Task`s via `_call_event_handler()`. A shared `_pending_latency` dict passes the latency value from the first callback to the second. CPython asyncio drains tasks in FIFO order.

### Telephony Route Not Changed

The Phase 3 placeholder comments in [telephony.py](file:///c:/NovaAutomata_Internship/VoiceAgent_Pipecat/app/routers/telephony.py) are preserved. The `finalize_call_log()` → `persist_call_log()` path exists as code but is not yet wired into the route. This is Phase 4 integration scope.

---

## What Was Verified

| Check | Result |
|-------|--------|
| All 7 module imports | ✅ Pass |
| `LatencyEvent` shape `{"time": float, "event": str}` | ✅ Pass |
| `TurnMetrics`, `CallMetrics`, `CallLog` instantiation | ✅ Pass |
| `CallLog` round-trip (`model_dump()` → constructor) | ✅ Pass |
| JSON serialization (MongoDB-safe) | ✅ Pass |
| `normalize_latency_breakdown()` with mock breakdown | ✅ Pass (6 events, sorted, correct shape) |
| `normalize_latency_breakdown()` with empty breakdown | ✅ Pass (returns `[]`) |
| `build_turn_metrics()` with/without breakdown | ✅ Pass |
| `finalize_call_log()` normal + disconnect cases | ✅ Pass |
| `update_session_first_latency()` once-only behavior | ✅ Pass |
| `append_session_turn_metrics()` accumulation | ✅ Pass |
| Missing session guards (no-op, no raise) | ✅ Pass |
| `setup_logging()` + `logger.bind(call_sid=...)` | ✅ Pass |
| Full app import chain (`from main import app`) | ✅ Pass |
| Route preservation (`/telephony/exotel`) | ✅ Pass |

---

## What Was NOT Tested (Human-Owned)

- Real MongoDB connection and document persistence
- Real Exotel call producing actual latency data
- Live latency realism and voice quality
- Operational logging under production load

---

## Deferred Items

1. **Telephony route wiring** — `finalize_call_log()` + `persist_call_log()` at the boundary points → Phase 4
2. **Transcript capture** — `UserBotLatencyObserver` doesn't emit text → needs a separate mechanism
3. **Motor deprecation** — Motor 3.7.1 deprecated May 2026 → post-v1 migration to PyMongo async
4. **User action** — Set `MONGO_DB_NAME=voice_agent_dev` in local `.env` for Atlas
