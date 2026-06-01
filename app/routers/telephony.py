"""Telephony router for Exotel WebSocket transport and pipeline execution."""

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from loguru import logger
from pipecat.runner.utils import parse_telephony_websocket

from app.config import get_settings
from app.database import persist_call_log
from app.runtime import (
    create_session,
    delete_session,
    cleanup_expired_pending_outbound_contexts,
    get_pending_outbound_context,
    get_session,
    pop_pending_outbound_context,
)
from app.services.pipecat_pipeline import run_pipeline
from app.services.transport_factory import build_exotel_transport
from app.utils.metrics_logger import finalize_call_log

POLICY_VIOLATION_CODE = 1008
UNSUPPORTED_PROVIDER_REASON = "unsupported_provider"
MISSING_IDENTITY_REASON = "missing_exotel_identity"
DUPLICATE_CALL_SID_REASON = "duplicate_call_sid"

router = APIRouter(prefix="/telephony", tags=["telephony"])


@router.get("/webhook")
async def exotel_webhook(request: Request) -> JSONResponse:
    """Handle Exotel Voicebot Applet dynamic URL callback.

    When an outbound call is answered and reaches the Voicebot Applet,
    Exotel sends a GET request to this endpoint. We return a JSON
    response containing the WebSocket URL for bidirectional audio streaming.

    Query params from Exotel: CallSid, CallFrom, CallTo, Direction, etc.
    """
    settings = get_settings()
    call_sid = request.query_params.get("CallSid", "unknown")

    log = logger.bind(call_sid=call_sid)
    log.info(
        "Exotel webhook received",
        call_from=request.query_params.get("CallFrom"),
        call_to=request.query_params.get("CallTo"),
        direction=request.query_params.get("Direction"),
    )

    clean_ngrok = settings.public_ngrok_url.replace("https://", "").replace("http://", "").strip("/")
    wss_url = f"wss://{clean_ngrok}/telephony/exotel?sample-rate=16000"

    log.info("Returning WebSocket URL to Exotel", wss_url=wss_url)
    return JSONResponse(content={"url": wss_url})


def _resolve_query_identity(websocket: WebSocket) -> tuple[str | None, str | None]:
    """Read Exotel identity values from the initial query string."""

    return (
        websocket.query_params.get("CallSid"),
        websocket.query_params.get("StreamSid"),
    )


def _remap_exotel_identity(call_data: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Normalize Pipecat Exotel call data into repo-standard naming."""

    call_sid = call_data.get("call_id")
    stream_sid = call_data.get("stream_id")
    if isinstance(call_sid, str) and isinstance(stream_sid, str):
        return call_sid, stream_sid
    return None, None


@router.websocket("/exotel")
async def exotel_websocket(websocket: WebSocket) -> None:
    """Handle Exotel WebSocket transport bootstrap and pipeline execution.

    Lifecycle:
    1. Accept WebSocket
    2. Resolve call_sid and stream_sid
    3. Create runtime session with immutable config_snapshot
    4. Build Exotel transport
    5. Hand off to pipeline service for execution
    6. On disconnect: cancel task -> unwind runner -> finalize boundary -> cleanup
    """

    created_call_sid: str | None = None
    await websocket.accept()

    try:
        call_sid, stream_sid = _resolve_query_identity(websocket)

        if not call_sid or not stream_sid:
            try:
                transport_type, call_data = await parse_telephony_websocket(websocket)
            except ValueError:
                await websocket.close(
                    code=POLICY_VIOLATION_CODE,
                    reason=MISSING_IDENTITY_REASON,
                )
                return

            if transport_type != "exotel":
                await websocket.close(
                    code=POLICY_VIOLATION_CODE,
                    reason=UNSUPPORTED_PROVIDER_REASON,
                )
                return

            call_sid, stream_sid = _remap_exotel_identity(call_data)

        if not call_sid or not stream_sid:
            await websocket.close(
                code=POLICY_VIOLATION_CODE,
                reason=MISSING_IDENTITY_REASON,
            )
            return

        settings = get_settings()
        cleanup_expired_pending_outbound_contexts(settings.pending_outbound_ttl_secs)
        pending_context = get_pending_outbound_context(call_sid)
        config_snapshot = (
            pending_context["config_snapshot"]
            if pending_context is not None
            else None
        )

        try:
            session = create_session(
                call_sid=call_sid,
                stream_sid=stream_sid,
                settings=settings,
                config_snapshot=config_snapshot,
            )
        except ValueError:
            await websocket.close(
                code=POLICY_VIOLATION_CODE,
                reason=DUPLICATE_CALL_SID_REASON,
            )
            return

        created_call_sid = call_sid
        if pending_context is not None:
            pop_pending_outbound_context(call_sid)

        log = logger.bind(call_sid=call_sid, stream_sid=stream_sid)
        log.info("Session created, building transport")

        transport = build_exotel_transport(
            websocket=websocket,
            stream_sid=stream_sid,
            call_sid=call_sid,
        )

        log.info("Transport ready, handing off to pipeline service")

        # --- Phase 2: Pipeline execution replaces Phase 1 bootstrap close ---
        result = await run_pipeline(
            transport=transport,
            call_sid=call_sid,
            stream_sid=stream_sid,
            snapshot=session["config_snapshot"],
            settings=settings,
            prepared_greeting_text=(
                pending_context["greeting_text"]
                if pending_context is not None
                else None
            ),
            opening_mode="outbound" if pending_context is not None else "default",
            greeting_start_delay_ms=settings.outbound_greeting_start_delay_ms,
        )

        if result.error:
            log.warning("Pipeline completed with error: {error}", error=result.error)
        else:
            log.info("Pipeline completed normally")

        # --- Finalization: persist call metrics to MongoDB ---
        session = get_session(call_sid)
        if session is not None:
            call_log = finalize_call_log(
                call_sid=call_sid,
                stream_sid=stream_sid,
                config_snapshot=session["config_snapshot"],
                metrics=session["metrics"],
                transcript=session["transcript"],
                completed=result.completed,
                error=result.error,
            )
            try:
                doc_id = await persist_call_log(call_log)
                log.info("Call log persisted", document_id=doc_id)
            except Exception:
                log.exception("Failed to persist call log to MongoDB")

    except WebSocketDisconnect:
        # This branch only fires for disconnects that occur during identity
        # resolution or session creation, before the pipeline starts. Disconnects
        # during active pipeline execution are handled by the transport
        # on_client_disconnected callback inside run_pipeline(), which calls
        # task.cancel() and lets the runner unwind before returning normally.
        if created_call_sid:
            log = logger.bind(call_sid=created_call_sid)
            log.info("WebSocket disconnected before pipeline execution started")

            # --- Finalization: persist partial call log for pre-pipeline disconnect ---
            session = get_session(created_call_sid)
            if session is not None:
                call_log = finalize_call_log(
                    call_sid=created_call_sid,
                    stream_sid=session["stream_sid"],
                    config_snapshot=session["config_snapshot"],
                    metrics=session["metrics"],
                    transcript=session["transcript"],
                    completed=False,
                    error="WebSocket disconnected before pipeline execution",
                )
                try:
                    doc_id = await persist_call_log(call_log)
                    log.info("Call log persisted after pre-pipeline disconnect", document_id=doc_id)
                except Exception:
                    log.exception("Failed to persist call log to MongoDB")
    except Exception:
        if created_call_sid:
            log = logger.bind(call_sid=created_call_sid)
            log.exception("Unexpected error in telephony route")

            # --- Finalization: persist call log for unexpected errors ---
            session = get_session(created_call_sid)
            if session is not None:
                call_log = finalize_call_log(
                    call_sid=created_call_sid,
                    stream_sid=session["stream_sid"],
                    config_snapshot=session["config_snapshot"],
                    metrics=session["metrics"],
                    transcript=session["transcript"],
                    completed=False,
                    error="Unexpected error during call handling",
                )
                try:
                    doc_id = await persist_call_log(call_log)
                    log.info("Call log persisted after unexpected error", document_id=doc_id)
                except Exception:
                    log.exception("Failed to persist call log to MongoDB")
        raise
    finally:
        if created_call_sid is not None:
            delete_session(created_call_sid)
            logger.bind(call_sid=created_call_sid).info("Session cleaned up from ACTIVE_SESSIONS")
