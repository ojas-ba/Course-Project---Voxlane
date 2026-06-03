"""Application entrypoint for the local voice agent."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from app.config import get_settings
from app.database import close_database, init_database
from app.logging_config import setup_logging
from app.routers.dashboard import router as dashboard_router
from app.routers.telephony import router as telephony_router
from app.services.exotel_client import ExotelOutboundClient
from app.services.runtime_config import seed_runtime_config_if_missing


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager for startup and shutdown resources."""

    # Initialize logging first so all subsequent startup logs are structured
    setup_logging()

    # Initialize database client. MongoDB is REQUIRED in V3 — fail clearly if
    # it is unavailable rather than serving calls with no runtime config.
    settings = get_settings()
    await init_database(settings)

    # Seed/load the single global runtime config into the process cache.
    try:
        await seed_runtime_config_if_missing(settings)
    except Exception:
        logger.exception("Failed to seed/load runtime config at startup")

    # Initialize Exotel outbound client (optional — server starts without it)
    try:
        exotel_client = ExotelOutboundClient(settings)
        app.state.exotel_client = exotel_client
        logger.info("Exotel outbound client attached to app.state")
    except ValueError as exc:
        app.state.exotel_client = None
        logger.warning(
            "Exotel outbound client not initialized (inbound-only mode): {err}",
            err=str(exc),
        )

    yield

    # Shutdown
    exotel_client = getattr(app.state, "exotel_client", None)
    if exotel_client is not None:
        await exotel_client.close()
    await close_database()


def create_app() -> FastAPI:
    """Create the FastAPI application instance for local development."""

    app = FastAPI(
        title="VoiceAgent Pipecat",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(telephony_router)
    app.include_router(dashboard_router)
    return app


app = create_app()
