"""Async MongoDB persistence layer using Motor.

Manages a singleton Motor client initialized once at application startup.
Provides persist_call_log() for writing finalized call data to the
call_logs collection.

The database and collection are auto-created by MongoDB on first insert.
No manual migration or collection creation steps are needed.
"""

from __future__ import annotations

from loguru import logger
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import Settings
from app.models.schemas import CallLog

# Module-level client reference — initialized once at startup
_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None

CALL_LOGS_COLLECTION = "call_logs"


async def init_database(settings: Settings) -> None:
    """Initialize the Motor client and database reference.

    Called once at application startup from main.py lifespan.
    Reuses a single client for the entire process lifetime.
    Also pings the database to ensure connection is valid.

    Args:
        settings: Application settings with mongo_uri and mongo_db_name.
    """

    global _client, _db
    _client = AsyncIOMotorClient(settings.mongo_uri)
    
    # Fail fast if connection string is invalid
    await _client.admin.command("ping")
    
    _db = _client[settings.mongo_db_name]
    logger.info(
        "MongoDB client initialized — database: {database}",
        database=settings.mongo_db_name,
    )


def get_database() -> AsyncIOMotorDatabase:
    """Return the initialized database instance.

    Raises:
        RuntimeError: If init_database() has not been called.
    """

    if _db is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _db


async def persist_call_log(call_log: CallLog) -> str:
    """Write a finalized CallLog to the call_logs collection.

    Args:
        call_log: The typed call log to persist.

    Returns:
        The inserted document's _id as a string.
    """

    db = get_database()
    collection = db[CALL_LOGS_COLLECTION]

    document = call_log.model_dump()
    result = await collection.insert_one(document)

    log = logger.bind(call_sid=call_log.call_sid)
    log.info(
        "Call log persisted to MongoDB",
        document_id=str(result.inserted_id),
        collection=CALL_LOGS_COLLECTION,
    )

    return str(result.inserted_id)


OUTBOUND_ATTEMPTS_COLLECTION = "outbound_attempts"


async def persist_outbound_attempt(doc: dict) -> str | None:
    """Persist a lightweight outbound-attempt record (e.g. a not-dialed failure).

    Best-effort: returns None and logs if the database is unavailable, so a
    failed outbound never raises on top of the original failure.
    """

    try:
        db = get_database()
        result = await db[OUTBOUND_ATTEMPTS_COLLECTION].insert_one(doc)
        return str(result.inserted_id)
    except Exception:
        logger.warning("Could not persist outbound attempt (db unavailable): {reason}", reason=doc.get("reason"))
        return None


async def close_database() -> None:
    """Close the Motor client. Called at application shutdown."""

    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None
        logger.info("MongoDB client closed")
