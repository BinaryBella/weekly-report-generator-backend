"""FastAPI application factory and process lifecycle wiring."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.auth import router as auth_router
from app.api.v1.users import router as users_router
from app.core.config import settings
from app.core.security import TokenError
from app.db.session import close_db, init_db
from app.models.user import Role, User

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


async def ensure_bootstrap_admins() -> None:
    """Promote any already-registered users whose email is a bootstrap admin.

    Registration handles the common case; this pass covers users who signed up
    before their address was added to ``BOOTSTRAP_ADMIN_EMAILS``.
    """
    for email in settings.bootstrap_admin_emails:
        user = await User.find_one(User.email == email)
        if user is not None and user.role is not Role.ADMIN:
            user.role = Role.ADMIN
            user.touch()
            await user.save()
            logger.info("Promoted bootstrap admin: %s", email)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Open the database on startup, close it on shutdown."""
    await init_db()
    await ensure_bootstrap_admins()
    try:
        yield
    finally:
        await close_db()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Requirement 1 - User Authentication & Roles.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(TokenError)
    async def _token_error_handler(_: Request, exc: TokenError) -> JSONResponse:
        """Last-resort translation of an un-caught token failure into ``401``."""
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc) or "Could not validate credentials"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    app.include_router(auth_router, prefix=settings.api_v1_prefix)
    app.include_router(users_router, prefix=settings.api_v1_prefix)

    @app.get("/health", tags=["health"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        """Return a static OK payload for load balancers / uptime checks."""
        return {"status": "ok"}

    return app


app = create_app()
