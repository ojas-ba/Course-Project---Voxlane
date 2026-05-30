"""Router for rendering the local voice agent telemetry dashboard using Jinja2 & HTMX."""

import os
import re
import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from bson import ObjectId
from loguru import logger

from app.database import get_database
from app.runtime import ACTIVE_SESSIONS

router = APIRouter()

# Resolve templates directory absolutely relative to the file location
BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


async def _fetch_recent_logs(limit: int = 50) -> tuple[list[dict[str, Any]], bool]:
    """Helper to fetch recent call logs and parse object IDs."""
    logs: list[dict[str, Any]] = []
    db_connected = True
    try:
        db = get_database()
        cursor = db["call_logs"].find().sort("_id", -1).limit(limit)
        logs = await cursor.to_list(length=limit)
        for log in logs:
            if isinstance(log.get("_id"), ObjectId):
                log["created_at"] = log["_id"].generation_time.strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                )
            else:
                log["created_at"] = "Unknown UTC"
    except Exception as exc:
        logger.warning("MongoDB not available for dashboard query: {err}", err=exc)
        db_connected = False
    return logs, db_connected


@router.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard(request: Request) -> Response:
    """Render the main single-page dashboard."""

    logs, db_connected = await _fetch_recent_logs()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "part": "full",
            "logs": logs,
            "active_count": len(ACTIVE_SESSIONS),
            "db_connected": db_connected,
        },
    )


@router.get("/dashboard/sessions", response_class=HTMLResponse)
async def get_active_sessions(request: Request) -> Response:
    """HTMX endpoint to retrieve currently active pipeline sessions."""

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "part": "sessions",
            "sessions": list(ACTIVE_SESSIONS.values()),
        },
    )


@router.get("/dashboard/dialer", response_class=HTMLResponse)
async def get_dialer(request: Request) -> Response:
    """HTMX endpoint to retrieve the clean outbound dialer form."""

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "part": "dialer",
        },
    )


@router.post("/telephony/outbound", response_class=HTMLResponse)
async def post_outbound(request: Request) -> Response:
    """HTMX endpoint to trigger an outbound call via Exotel. Fails if client is unconfigured."""

    form_data = await request.form()
    to_number = form_data.get("to_number", "")
    if not isinstance(to_number, str):
        to_number = ""
    to_number = to_number.strip()

    if not to_number or not re.match(r"^\+?[1-9]\d{1,14}$", to_number):
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "part": "outbound_status",
                "success": False,
                "error": "Valid E.164 phone number is required (e.g., +919876543210)",
            },
        )

    client = getattr(request.app.state, "exotel_client", None)
    if client is None:
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "part": "outbound_status",
                "success": False,
                "error": "Exotel outbound client not initialized. Check your environment configuration.",
            },
        )

    try:
        async with asyncio.timeout(10.0):
            result = await client.make_call(to_number)
            
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "part": "outbound_status",
                "success": result.success,
                "to_number": to_number,
                "call_sid": result.call_sid,
                "error": result.error,
            },
        )
    except TimeoutError:
        logger.error("Outbound call timed out after 10 seconds")
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "part": "outbound_status",
                "success": False,
                "error": "Outbound call request timed out. Please try again.",
            },
        )
    except Exception as exc:
        logger.exception("Failed to trigger outbound call via client")
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "part": "outbound_status",
                "success": False,
                "error": f"Internal outbound client error: {exc}",
            },
        )


@router.get("/dashboard/logs-list", response_class=HTMLResponse)
async def get_logs_list(request: Request) -> Response:
    """HTMX endpoint to return the completed logs list fragment (for refresh)."""

    logs, db_connected = await _fetch_recent_logs()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "part": "logs_list",
            "logs": logs,
            "db_connected": db_connected,
        },
    )


@router.get("/dashboard/logs/{call_sid}", response_class=HTMLResponse)
async def get_log_detail(request: Request, call_sid: str) -> Response:
    """HTMX endpoint to return the timeline visualization and details of a completed call."""

    try:
        db = get_database()
        log = await db["call_logs"].find_one({"call_sid": call_sid})
        if not log:
            return Response(
                content="<div class='error-message'>Call log not found</div>",
                status_code=200,
            )

        if isinstance(log.get("_id"), ObjectId):
            log["created_at"] = log["_id"].generation_time.strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
        else:
            log["created_at"] = "Unknown UTC"

        turns = log.get("metrics", {}).get("turns", [])
        latency_values = [
            t.get("user_bot_latency_secs")
            for t in turns
            if t.get("user_bot_latency_secs") is not None
        ]
        avg_latency = (
            sum(latency_values) / len(latency_values) if latency_values else None
        )

        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "part": "timeline",
                "log": log,
                "avg_latency": avg_latency,
            },
        )
    except Exception as exc:
        logger.exception("Failed to query call log details")
        return Response(
            content=f"<div class='error-message'>Error querying database: {exc}</div>",
            status_code=200,
        )
