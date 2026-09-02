"""FastAPI application factory and process lifecycle wiring."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.auth import router as auth_router
from app.api.v1.projects import router as projects_router
from app.api.v1.reports import router as reports_router
from app.api.v1.users import router as users_router
from app.core.config import settings
from app.core.security import TokenError
from app.db.session import close_db, init_db
from app.models.user import Role, User
from app.services.project_service import (
    DuplicateProjectNameError,
    InvalidMemberIdsError,
    ProjectNotFoundError,
)
from app.services.report_service import (
    MemberNotFoundError,
    ReportAccessDeniedError,
    ReportNotEditableError,
    ReportNotFoundError,
    ReportTransitionError,
    ReportValidationError,
)

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

    @app.exception_handler(ProjectNotFoundError)
    async def _project_not_found_handler(
        _: Request, __: ProjectNotFoundError
    ) -> JSONResponse:
        """Translate an unknown project id into ``404 Not Found``."""
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "Project not found"},
        )

    @app.exception_handler(DuplicateProjectNameError)
    async def _duplicate_project_handler(
        _: Request, exc: DuplicateProjectNameError
    ) -> JSONResponse:
        """Translate a project-name collision into ``400 Bad Request``."""
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(InvalidMemberIdsError)
    async def _invalid_member_ids_handler(
        _: Request, exc: InvalidMemberIdsError
    ) -> JSONResponse:
        """Translate unknown member/user ids into ``400 Bad Request``."""
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ReportNotFoundError)
    async def _report_not_found_handler(
        _: Request, __: ReportNotFoundError
    ) -> JSONResponse:
        """Translate an unknown report id into ``404 Not Found``."""
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "Report not found"},
        )

    @app.exception_handler(ReportAccessDeniedError)
    async def _report_access_denied_handler(
        _: Request, exc: ReportAccessDeniedError
    ) -> JSONResponse:
        """Translate a cross-user report access attempt into ``403 Forbidden``."""
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ReportNotEditableError)
    async def _report_not_editable_handler(
        _: Request, exc: ReportNotEditableError
    ) -> JSONResponse:
        """Translate an edit against a locked report into ``400 Bad Request``."""
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ReportValidationError)
    async def _report_validation_handler(
        _: Request, exc: ReportValidationError
    ) -> JSONResponse:
        """Translate an inconsistent report payload into ``400 Bad Request``."""
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ReportTransitionError)
    async def _report_transition_handler(
        _: Request, exc: ReportTransitionError
    ) -> JSONResponse:
        """Translate an illegal review-workflow transition into ``400``."""
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

<<<<<<< Updated upstream
=======
    @app.exception_handler(ReportTransitionError)
    async def _report_transition_handler(
        _: Request, exc: ReportTransitionError
    ) -> JSONResponse:
        """Translate an illegal review-workflow transition into ``400``."""
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(MemberNotFoundError)
    async def _member_not_found_handler(
        _: Request, __: MemberNotFoundError
    ) -> JSONResponse:
        """Translate an unknown team-member id into ``404 Not Found``."""
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "Team member not found"},
        )

>>>>>>> Stashed changes
    app.include_router(auth_router, prefix=settings.api_v1_prefix)
    app.include_router(users_router, prefix=settings.api_v1_prefix)
    app.include_router(projects_router, prefix=settings.api_v1_prefix)
    app.include_router(reports_router, prefix=settings.api_v1_prefix)

    @app.get("/health", tags=["health"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        """Return a static OK payload for load balancers / uptime checks."""
        return {"status": "ok"}

    return app


app = create_app()
