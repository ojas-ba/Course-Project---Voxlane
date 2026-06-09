"""Dashboard router: runtime-config page, greeting-first outbound, call logs.

V3 Phase 1: a single global runtime config (no bot profiles). Outbound calls
generate the LLM greeting BEFORE dialing and are not placed if greeting
generation (or config load) fails — there is no static/canned fallback.
"""

import os
import re
import asyncio
import math
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from bson import ObjectId
from loguru import logger

from app.database import get_database, persist_outbound_attempt
from app.config import get_settings
from app.models.runtime_config_models import RuntimeConfig
from app.runtime import (
    ACTIVE_SESSIONS,
    attach_pending_call_sid,
    cleanup_expired_pending_outbound_contexts,
    create_pending_outbound_context,
)
from app.services.conversation_controller import generate_outbound_greeting_text
from app.services.runtime_config import (
    build_effective_snapshot,
    get_cached_runtime_config,
    load_runtime_config,
    save_runtime_config,
)

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

E164_RE = re.compile(r"^\+?[1-9]\d{1,14}$")


async def _fetch_recent_logs(limit: int = 50) -> tuple[list[dict[str, Any]], bool]:
    """Fetch recent call logs and stamp a created_at from the ObjectId."""
    logs: list[dict[str, Any]] = []
    db_connected = True
    try:
        db = get_database()
        cursor = db["call_logs"].find().sort("_id", -1).limit(limit)
        logs = await cursor.to_list(length=limit)
        for log in logs:
            if isinstance(log.get("_id"), ObjectId):
                log["created_at"] = log["_id"].generation_time.strftime("%Y-%m-%d %H:%M:%S UTC")
            else:
                log["created_at"] = "Unknown UTC"
    except Exception as exc:
        logger.warning("MongoDB not available for dashboard query: {err}", err=exc)
        db_connected = False
    return logs, db_connected


async def _load_config_or_default() -> RuntimeConfig:
    """Best-effort config load for read-only rendering (never raises)."""
    settings = get_settings()
    try:
        return await load_runtime_config(settings)
    except Exception:
        logger.warning("Runtime config load failed; rendering cached/default config")
        return get_cached_runtime_config() or RuntimeConfig.default(settings)


# --- Config page -----------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
@router.get("/config", response_class=HTMLResponse)
async def get_config(request: Request) -> HTMLResponse:
    """Render the runtime configuration + outbound-call page.

    Loads the live config from Mongo. If that fails, render defaults but show a
    visible warning so a fallback is never mistaken for a saved Mongo config.
    """
    settings = get_settings()
    warning = None
    try:
        config = await load_runtime_config(settings)
    except Exception as exc:
        logger.warning("Runtime config load failed for config page: {err}", err=exc)
        config = get_cached_runtime_config() or RuntimeConfig.default(settings)
        warning = (
            "Could not load runtime config from MongoDB; showing the last-known/"
            f"default config (NOT saved). Fix the database before dialing. ({exc})"
        )
    return templates.TemplateResponse(
        request=request,
        name="config.html",
        context={"config": config, "saved": False, "error": warning},
    )


def _parse_config_form(form: Any, base: RuntimeConfig) -> RuntimeConfig:
    """Build a RuntimeConfig from posted form fields, falling back to base."""

    def s(key: str, default: str) -> str:
        val = form.get(key)
        return val.strip() if isinstance(val, str) and val.strip() else default

    def f(key: str, default: float) -> float:
        try:
            return float(form.get(key))
        except (TypeError, ValueError):
            return default

    def i(key: str, default: int) -> int:
        try:
            return int(float(form.get(key)))
        except (TypeError, ValueError):
            return default

    def b(key: str, default: bool) -> bool:
        val = form.get(key)
        if not isinstance(val, str):
            return default
        return val.strip().lower() in {"true", "1", "yes", "on"}

    hints_raw = s("stt_language_hints", ",".join(base.stt.language_hints))
    hints = [h.strip() for h in hints_raw.split(",") if h.strip()]

    data = base.model_dump()
    data["stt"] = {
        "model": s("stt_model", base.stt.model),
        "language_hints": hints,
        "eot_threshold": f("stt_eot_threshold", base.stt.eot_threshold),
        "eot_timeout_ms": i("stt_eot_timeout_ms", base.stt.eot_timeout_ms),
        "defer_connect_on_start": b("stt_defer_connect_on_start", base.stt.defer_connect_on_start),
        "turn_stop_timeout_ms": i("stt_turn_stop_timeout_ms", base.stt.turn_stop_timeout_ms),
        "enable_eager_eot": b("stt_enable_eager_eot", base.stt.enable_eager_eot),
        "eager_eot_threshold": f("stt_eager_eot_threshold", base.stt.eager_eot_threshold),
    }
    data["llm"] = {
        "provider": "cerebras",
        "model": s("llm_model", base.llm.model),
        "temperature": f("llm_temperature", base.llm.temperature),
        "max_tokens": i("llm_max_tokens", base.llm.max_tokens),
        "request_timeout_seconds": f("llm_request_timeout_seconds", base.llm.request_timeout_seconds),
        "enable_speculative_llm": b("llm_enable_speculative_llm", base.llm.enable_speculative_llm),
    }
    data["tts"] = {
        "provider": "cartesia",
        "model": s("tts_model", base.tts.model),
        "voice_id": s("tts_voice_id", base.tts.voice_id),
        "language_mode": s("tts_language_mode", base.tts.language_mode),
    }
    data["prompt"] = {
        "system_prompt": s("system_prompt", base.prompt.system_prompt),
        "greeting_prompt": s("greeting_prompt", base.prompt.greeting_prompt),
    }
    data["memory"] = {
        **base.memory.model_dump(),
        "recent_turn_pairs": i("mem_recent_turn_pairs", base.memory.recent_turn_pairs),
        "enable_running_summary_after_turn": i(
            "mem_enable_running_summary_after_turn", base.memory.enable_running_summary_after_turn
        ),
        "summary_update_every_turns": i(
            "mem_summary_update_every_turns", base.memory.summary_update_every_turns
        ),
        "recall_min_overlap": i("mem_recall_min_overlap", base.memory.recall_min_overlap),
        "max_memory_block_tokens": i(
            "mem_max_memory_block_tokens", base.memory.max_memory_block_tokens
        ),
    }
    data["ops"] = {"debug_log_level": s("debug_log_level", base.ops.debug_log_level)}
    return RuntimeConfig.model_validate(data)


@router.post("/config", response_class=HTMLResponse)
async def post_config(request: Request) -> HTMLResponse:
    """Persist edited runtime config to Mongo (single global doc)."""
    form = await request.form()
    base = await _load_config_or_default()
    try:
        updated = _parse_config_form(form, base)
        config = await save_runtime_config(updated)
        return templates.TemplateResponse(
            request=request,
            name="config.html",
            context={"config": config, "saved": True, "error": None},
        )
    except Exception as exc:
        logger.exception("Failed to save runtime config")
        return templates.TemplateResponse(
            request=request,
            name="config.html",
            context={"config": base, "saved": False, "error": f"Save failed: {exc}"},
        )


# --- Greeting-first outbound -----------------------------------------------


def _outbound_status(request: Request, **ctx: Any) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="_outbound_status.html",
        context=ctx,
    )


async def _persist_failure(
    *, reason: str, to_number: str, error_message: str | None,
    config_snapshot: dict[str, Any] | None = None,
) -> None:
    """Best-effort persistence of a not-dialed outbound attempt (never raises)."""
    doc: dict[str, Any] = {
        "type": "outbound_attempt",
        "status": "not_dialed",
        "reason": reason,
        "to_number": to_number,
        "created_at": time.time(),
        "error_message": error_message,
    }
    if config_snapshot is not None:
        doc["config_snapshot"] = config_snapshot
    await persist_outbound_attempt(doc)


@router.post("/telephony/outbound", response_class=HTMLResponse)
async def post_outbound(request: Request) -> HTMLResponse:
    """Trigger an outbound call. Generates the LLM greeting BEFORE dialing;
    the call is NOT placed if config load or greeting generation fails."""

    form = await request.form()
    to_number = form.get("to_number")
    to_number = to_number.strip() if isinstance(to_number, str) else ""

    if not to_number or not E164_RE.match(to_number):
        return _outbound_status(
            request, success=False,
            error="Valid E.164 phone number is required (e.g., +919876543210)",
        )

    client = getattr(request.app.state, "exotel_client", None)
    if client is None:
        return _outbound_status(
            request, success=False,
            error="Exotel outbound client not initialized. Check environment configuration.",
        )

    settings = get_settings()
    cleanup_expired_pending_outbound_contexts(settings.pending_outbound_ttl_secs)

    # 1. Load runtime config (Mongo required). Failure => no call.
    try:
        config = await load_runtime_config(settings)
    except Exception as exc:
        logger.exception("Runtime config load failed; not placing call")
        # Mongo is likely the thing that's down, so this persist is best-effort
        # and will usually only structured-log.
        await _persist_failure(reason="config_load_failed", to_number=to_number, error_message=str(exc))
        return _outbound_status(
            request, success=False,
            error=f"Could not load runtime config; call not placed. ({exc})",
        )

    snapshot = build_effective_snapshot(config)

    # 2. Generate LLM greeting BEFORE dialing. Any failure/timeout => no call.
    greeting_started_at = time.time()
    try:
        async with asyncio.timeout(config.llm.request_timeout_seconds):
            greeting_text = await generate_outbound_greeting_text(
                snapshot=snapshot, settings=settings, to_number=to_number,
            )
    except TimeoutError:
        logger.warning("Outbound greeting timed out; not placing call")
        await _persist_failure(reason="greeting_timeout", to_number=to_number,
                               error_message="greeting generation timed out", config_snapshot=snapshot)
        return _outbound_status(
            request, success=False,
            error="Greeting generation timed out; call not placed.",
        )
    except Exception as exc:
        logger.warning("Outbound greeting failed; not placing call: {err}", err=str(exc))
        await _persist_failure(reason="greeting_failed", to_number=to_number,
                               error_message=str(exc), config_snapshot=snapshot)
        return _outbound_status(
            request, success=False,
            error=f"Greeting generation failed; call not placed. ({exc})",
        )
    greeting_completed_at = time.time()

    greeting_metrics = {
        "greeting_generation_started_at": greeting_started_at,
        "greeting_generation_completed_at": greeting_completed_at,
        "greeting_llm_ttfb": None,
        "greeting_llm_total_latency": greeting_completed_at - greeting_started_at,
        "greeting_text_ready_before_dial": True,
        "greeting_text_source": "llm",
    }

    pending_context = create_pending_outbound_context(
        to_number=to_number,
        config_snapshot=snapshot,
        greeting_text=greeting_text,
        greeting_metrics=greeting_metrics,
    )

    # 3. Greeting is ready -> place the call. (pending_context is only registered
    # in PENDING_OUTBOUND_CALLS via attach_pending_call_sid on success, so a
    # make_call failure leaves nothing to clean up.)
    try:
        async with asyncio.timeout(10.0):
            result = await client.make_call(to_number)
    except TimeoutError:
        logger.error("Outbound call timed out after 10 seconds")
        await _persist_failure(reason="exotel_make_call_timeout", to_number=to_number,
                               error_message="outbound call request timed out", config_snapshot=snapshot)
        return _outbound_status(request, success=False, error="Outbound call request timed out.")
    except Exception as exc:
        logger.exception("Failed to trigger outbound call via client")
        await _persist_failure(reason="exotel_make_call_failed", to_number=to_number,
                               error_message=str(exc), config_snapshot=snapshot)
        return _outbound_status(request, success=False, error=f"Internal outbound client error: {exc}")

    outbound_error = result.error
    if result.success and not result.call_sid:
        outbound_error = "Exotel did not return a CallSid."
    outbound_success = result.success and bool(result.call_sid)

    if outbound_success and result.call_sid:
        attach_pending_call_sid(pending_context, call_sid=result.call_sid)
    else:
        await _persist_failure(reason="exotel_make_call_failed", to_number=to_number,
                               error_message=outbound_error, config_snapshot=snapshot)

    return _outbound_status(
        request, success=outbound_success, to_number=to_number,
        call_sid=result.call_sid, error=outbound_error, greeting_text=greeting_text,
    )


# --- Call logs (list/detail enhanced in Batch 3) ---------------------------


def _num(v: Any) -> float | None:
    return v if isinstance(v, (int, float)) else None


def _stat_block(xs: list[float]) -> dict[str, Any]:
    """avg/median/p95/max + measured count for a latency series (None when empty)."""
    if not xs:
        return {"avg": None, "median": None, "p95": None, "max": None, "n": 0}
    return {
        "avg": round(sum(xs) / len(xs), 1),
        "median": _median(xs),
        "p95": _p95(xs),
        "max": max(xs),
        "n": len(xs),
    }


def _latency_summary(turn_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Two distinct per-call latencies, each with avg/median/p95/max + measured count.

    - ``responsiveness`` = time from the user's LAST utterance to first audio. This is
      the felt latency: it excludes both the endpointing window (system confirming the
      turn ended) AND any mid-thought pause on a merged turn. Uses the latest-anchored
      turn-release value, falling back to the release/primary value for older records.
    - ``total`` = the VAD-anchored "true silence -> first audio" (``primary_latency_ms``):
      physically honest but includes ~600-900ms of endpointing the caller does not
      perceive as waiting.

    Null-latency turns are excluded; the measured count (``n``) is reported so the UI can
    show ``median over n of total`` instead of hiding the gap.
    """
    total_vals: list[float] = []
    resp_vals: list[float] = []
    for r in turn_records:
        total = _num(r.get("primary_latency_ms"))
        if total is not None:
            total_vals.append(total)
        resp = _num(r.get("latest_user_stop_to_first_audio_ms"))
        if resp is None:
            resp = _num(r.get("user_stop_to_first_audio_release_ms"))
        if resp is None:
            resp = total
        if resp is not None:
            resp_vals.append(resp)
    return {
        "total_turns": len(turn_records),
        "responsiveness": _stat_block(resp_vals),
        "total": _stat_block(total_vals),
    }


def _badge_class(ms: float | None, *, warn: float, crit: float) -> str:
    """Latency → badge class. Thresholds live here (UI helper), not in templates."""
    if ms is None:
        return "neutral"
    if ms > crit:
        return "crit"
    if ms > warn:
        return "warn"
    return "ok"


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return round((s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2), 1)


def _p95(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    # nearest-rank p95 (index ceil(0.95*n)-1)
    idx = max(0, math.ceil(0.95 * len(s)) - 1)
    return round(s[idx], 1)


def _eager_probe_summary(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Derive the call-level EagerEOT telemetry rollup from per-turn eager probes.

    Returns None when no turn carried a probe (eager telemetry was off), so the UI
    only shows the summary for calls that actually ran the probe.
    """
    probes = [r.get("eager_probe") for r in records if isinstance(r.get("eager_probe"), dict)]
    if not probes:
        return None
    total = len(records)
    eager = [p for p in probes if p.get("eager_eot_seen")]
    eligible = [p for p in probes if p.get("verdict") == "eligible"]
    # Count resumes only among eager turns so the numerator can never exceed the
    # denominator (a TurnResumed only matters as the cancel signal for an eager).
    resumed = [p for p in eager if p.get("turn_resumed_seen")]
    mismatch = [p for p in probes if p.get("verdict") == "discarded_transcript_mismatch"]
    matched = [p for p in eager if p.get("eager_final_match") is True]
    # Only positive head starts (drop the degenerate eager-at/after-EOT cases that
    # store a <=0 head_start) so the median/p95 reflect real head room.
    head_starts = [
        p["head_start_ms"] for p in eager
        if isinstance(p.get("head_start_ms"), (int, float)) and p["head_start_ms"] > 0
    ]
    saved = [p["estimated_saved_ms"] for p in eligible if isinstance(p.get("estimated_saved_ms"), (int, float))]

    def _rate(num: int, den: int) -> float:
        return round(num / den, 3) if den else 0.0

    summary = {
        "total_turns": total,
        "eager_eot_turns": len(eager),
        "eligible_turns": len(eligible),
        "turn_resumed_turns": len(resumed),
        "transcript_mismatch_turns": len(mismatch),
        "eager_eot_rate": _rate(len(eager), total),
        "eligible_rate": _rate(len(eligible), total),
        "turn_resumed_rate": _rate(len(resumed), len(eager)),
        "eager_final_match_rate": _rate(len(matched), len(eager)),
        "median_head_start_ms": _median(head_starts),
        "p95_head_start_ms": _p95(head_starts),
        "median_estimated_saved_ms": _median(saved),
        "p95_estimated_saved_ms": _p95(saved),
        "total_estimated_saved_ms": round(sum(saved), 1) if saved else 0.0,
        "avg_estimated_saved_ms_per_turn": round(sum(saved) / total, 1) if total else 0.0,
    }

    # --- Stage-2 speculative-draft rollup (probes carry draft_* only when on) ---
    draft_started = [p for p in probes if p.get("draft_started")]
    if draft_started:
        draft_released = [p for p in probes if p.get("draft_released")]
        # A turn that resumed-then-redrafted-then-released carries BOTH draft_cancelled
        # and draft_released; release wins so it is not double-counted (defensive — the
        # controller also clears the stale cancel marks when the redraft starts).
        draft_cancelled = [p for p in probes if p.get("draft_cancelled") and not p.get("draft_released")]
        normal_used = [p for p in probes if p.get("normal_llm_used")]
        draft_errors = [p for p in probes if p.get("draft_error")]
        extra_calls = sum(int(p.get("extra_llm_call") or 0) for p in probes)
        # MEASURED draft head start over RELEASED turns (not the Stage-1 estimate).
        headstarts = [
            p["draft_headstart_ms"] for p in draft_released
            if isinstance(p.get("draft_headstart_ms"), (int, float))
        ]
        # Empirical end-to-end comparison: median user-stop->first-audio for released
        # vs normal turns (the honest "real saving" signal across the call). Use the
        # turn-release anchor (defined identically for EVERY turn) rather than the
        # headline user_stop_to_first_audio_ms, which is VAD-anchored on some turns and
        # release-anchored on others — mixing the two would make released-vs-normal an
        # apples-to-oranges comparison (F1 review). Fall back to the headline for old
        # records persisted before the release field existed.
        def _release_fa(r: dict[str, Any]) -> float | None:
            v = r.get("user_stop_to_first_audio_release_ms")
            if not isinstance(v, (int, float)):
                v = r.get("user_stop_to_first_audio_ms")
            return v if isinstance(v, (int, float)) else None

        released_fa = [
            fa for r in records
            if isinstance(r.get("eager_probe"), dict) and r["eager_probe"].get("draft_released")
            for fa in [_release_fa(r)] if fa is not None
        ]
        normal_fa = [
            fa for r in records
            if isinstance(r.get("eager_probe"), dict) and r["eager_probe"].get("normal_llm_used")
            for fa in [_release_fa(r)] if fa is not None
        ]
        med_released, med_normal = _median(released_fa), _median(normal_fa)
        summary.update({
            "speculation_on": True,
            "draft_start_count": len(draft_started),
            "draft_release_count": len(draft_released),
            "draft_cancel_count": len(draft_cancelled),
            "draft_error_count": len(draft_errors),
            "normal_llm_used_count": len(normal_used),
            "draft_use_rate": _rate(len(draft_released), len(draft_started)),
            "cancel_rate": _rate(len(draft_cancelled), len(draft_started)),
            "additional_llm_call_rate": _rate(extra_calls, total),
            "median_draft_headstart_ms": _median(headstarts),
            "p95_draft_headstart_ms": _p95(headstarts),
            "median_user_stop_to_first_audio_released_ms": med_released,
            "median_user_stop_to_first_audio_normal_ms": med_normal,
            "empirical_saved_ms_population": (
                round(med_normal - med_released, 1)
                if med_normal is not None and med_released is not None else None
            ),
        })
    return summary


def _memory_summary(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Call-level rollup of the per-turn memory snapshots (recall/context size).

    Returns None when no turn carried a memory snapshot (feature off / no user
    turns), so the UI only shows the card for calls that actually have data.
    """
    mems = [r.get("memory") for r in records if isinstance(r.get("memory"), dict)]
    if not mems:
        return None
    total = len(records)
    recall_used = [m for m in mems if m.get("recall_used")]
    triggered = [m for m in mems if m.get("recall_triggered")]
    ctx_counts = [
        m["context_message_count"] for m in mems
        if isinstance(m.get("context_message_count"), (int, float))
    ]
    toks = [
        m["approx_memory_tokens"] for m in mems
        if isinstance(m.get("approx_memory_tokens"), (int, float))
    ]
    total_snippets = sum(len(m.get("recall_snippets") or []) for m in mems)

    def _rate(num: int, den: int) -> float:
        return round(num / den, 3) if den else 0.0

    return {
        "total_turns": total,
        "memory_turns": len(mems),
        "recall_used_turns": len(recall_used),
        "recall_triggered_turns": len(triggered),
        "recall_use_rate": _rate(len(recall_used), len(mems)),
        "total_recall_snippets": total_snippets,
        "median_context_messages": _median([float(c) for c in ctx_counts]),
        "median_approx_memory_tokens": _median([float(t) for t in toks]),
        "max_approx_memory_tokens": max(toks) if toks else None,
    }


@router.get("/calls", response_class=HTMLResponse)
async def get_calls_list(request: Request) -> HTMLResponse:
    """Render the completed-calls list with per-call latency stats."""
    logs, db_connected = await _fetch_recent_logs()
    for log in logs:
        records = log.get("metrics", {}).get("turn_records", [])
        lat = _latency_summary(records)
        resp, total = lat["responsiveness"], lat["total"]
        # Responsiveness (felt latency) is the headline column; total (incl. endpointing)
        # is shown muted alongside. Median, not avg — a few slow-LLM turns skew the mean.
        log["resp_ms"] = resp["median"]
        log["resp_badge"] = _badge_class(resp["median"], warn=800, crit=1500)
        log["total_ms"] = total["median"]
        log["turn_count"] = log.get("metrics", {}).get("turn_count", len(records))
        log["measured_latency_turns"] = resp["n"]
    return templates.TemplateResponse(
        request=request,
        name="calls.html",
        context={"logs": logs, "db_connected": db_connected},
    )


@router.get("/calls/{call_sid}", response_class=HTMLResponse)
async def get_call_detail(request: Request, call_sid: str) -> HTMLResponse:
    """Render the detail page for one completed call."""
    try:
        db = get_database()
        log = await db["call_logs"].find_one({"call_sid": call_sid})
    except Exception as exc:
        logger.warning("Call detail query failed: {err}", err=exc)
        log = None
    if not log:
        return HTMLResponse("<p class='status err'>Call log not found.</p>", status_code=200)

    if isinstance(log.get("_id"), ObjectId):
        log["created_at"] = log["_id"].generation_time.strftime("%Y-%m-%d %H:%M:%S UTC")
    records = log.get("metrics", {}).get("turn_records", [])
    return templates.TemplateResponse(
        request=request,
        name="call_detail.html",
        context={
            "log": log,
            "turn_records": records,
            "latency": _latency_summary(records),
            "eager_summary": _eager_probe_summary(records),
            "memory_summary": _memory_summary(records),
        },
    )
