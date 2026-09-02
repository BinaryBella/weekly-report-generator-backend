"""MongoDB connection lifecycle and Beanie initialisation."""

from __future__ import annotations

import logging

import certifi
from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError

from app.core.config import settings
from app.models import document_models

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None


def _build_client() -> AsyncIOMotorClient:
    """Create a Motor client for the configured URI.

    For TLS connections (any ``mongodb+srv://`` URI, e.g. MongoDB Atlas, or a URI
    that explicitly opts into ``tls=true``) we pin the CA bundle shipped with
    ``certifi`` - this avoids the ``SSL: CERTIFICATE_VERIFY_FAILED`` handshake
    errors that Atlas commonly raises on stock Windows / macOS Python builds.
    """
    uri = settings.mongodb_uri
    kwargs: dict[str, object] = {
        "tz_aware": True,
        "serverSelectionTimeoutMS": settings.mongodb_server_selection_timeout_ms,
        "appname": settings.app_name,
    }
    if uri.startswith("mongodb+srv://") or "tls=true" in uri or "ssl=true" in uri:
        kwargs["tlsCAFile"] = certifi.where()

    return AsyncIOMotorClient(uri, **kwargs)


async def init_db(client: AsyncIOMotorClient | None = None) -> None:
    """Open the MongoDB connection and bind the Beanie document models.

    Args:
        client: Optional pre-built Motor client. Injected by the test-suite with
            an in-memory ``mongomock-motor`` client; in production it is created
            from :data:`settings`.

    Raises:
        RuntimeError: If the database cannot be reached, with a concise message
            instead of a raw driver traceback.
    """
    global _client

    _client = client or _build_client()

    if client is None:  # only ping a real server, not the in-memory test double
        try:
            await _client.admin.command("ping")
        except PyMongoError as exc:
            await close_db()
            raise RuntimeError(
                f"Cannot reach MongoDB at '{_redacted_uri()}'. "
                "Check MONGODB_URI, that your IP is on the Atlas access list, "
                f"and that the credentials are correct. Driver error: {exc}"
            ) from exc

    database = _client[settings.mongodb_db_name]
    await init_beanie(database=database, document_models=list(document_models))
    logger.info("Connected to MongoDB database '%s'", settings.mongodb_db_name)


async def close_db() -> None:
    """Close the MongoDB connection if one is open."""
    global _client

    if _client is not None:
        _client.close()
        _client = None
        logger.info("Closed MongoDB connection")


def _redacted_uri() -> str:
    """Return the configured URI with any inline password masked for logging."""
    uri = settings.mongodb_uri
    if "@" in uri and "://" in uri:
        scheme, rest = uri.split("://", 1)
        creds, host = rest.split("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return uri
