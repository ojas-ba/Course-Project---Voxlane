"""Dashboard router: runtime-config page, greeting-first outbound, call logs.

V3 Phase 1: a single global runtime config (no bot profiles). Outbound calls
generate the LLM greeting BEFORE dialing and are not placed if greeting
generation (or config load) fails — there is no static/canned fallback.
"""

import os
import re
import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from bson import ObjectId
from loguru import logger

from app.database import get_database
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
    """Render the runtime configuration + outbound-call page."""
    config = await _load_config_or_default()
    return templates.TemplateResponse(
        request=request,
        name="config.html",
        context={"config": config, "saved": False, "error": None},
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

    hints_raw = s("stt_language_hints", ",".join(base.stt.language_hints))
    hints = [h.strip() for h in hints_raw.split(",") if h.strip()]

    data = base.model_dump()
    data["stt"] = {
        "model": s("stt_model", base.stt.model),
        "language_hints": hints,
        "eot_threshold": f("stt_eot_threshold", base.stt.eot_threshold),
        "eot_timeout_ms": i("stt_eot_timeout_ms", base.stt.eot_timeout_ms),
    }
    data["llm"] = {
        "provider": "cerebras",
        "model": s("llm_model", base.llm.model),
        "temperature": f("llm_temperature", base.llm.temperature),
        "max_tokens": i("llm_max_tokens", base.llm.max_tokens),
        "request_timeout_seconds": f("llm_request_timeout_seconds", base.llm.request_timeout_seconds),
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
        name="config.html",
        context={"part": "outbound_status", **ctx},
    )


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
    except (TimeoutError, Exception) as exc:  # noqa: B014 - explicit: any failure = no call
        reason = "Greeting generation timed out" if isinstance(exc, TimeoutError) else str(exc)
        logger.warning("Outbound greeting failed; not placing call: {err}", err=reason)
        return _outbound_status(
            request, success=False,
            error=f"Greeting generation failed; call not placed. ({reason})",
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

    # 3. Greeting is ready -> place the call.
    try:
        async with asyncio.timeout(10.0):
            result = await client.make_call(to_number)
    except TimeoutError:
        logger.error("Outbound call timed out after 10 seconds")
        return _outbound_status(request, success=False, error="Outbound call request timed out.")
    except Exception as exc:
        logger.exception("Failed to trigger outbound call via client")
        return _outbound_status(request, success=False, error=f"Internal outbound client error: {exc}")

    outbound_error = result.error
    if result.success and not result.call_sid:
        outbound_error = "Exotel did not return a CallSid."
    outbound_success = result.success and bool(result.call_sid)

    if outbound_success and result.call_sid:
        attach_pending_call_sid(pending_context, call_sid=result.call_sid)

    return _outbound_status(
        request, success=outbound_success, to_number=to_number,
        call_sid=result.call_sid, error=outbound_error, greeting_text=greeting_text,
    )


# --- Call logs (list/detail enhanced in Batch 3) ---------------------------


@router.get("/calls", response_class=HTMLResponse)
async def get_calls_list(request: Request) -> HTMLResponse:
    """Render the completed-calls list."""
    logs, db_connected = await _fetch_recent_logs()
    return templates.TemplateResponse(
        request=request,
        name="config.html",
        context={"part": "logs_list", "logs": logs, "db_connected": db_connected},
    )
