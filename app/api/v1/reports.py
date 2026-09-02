"""Personal weekly report endpoints.

Access summary:
    * ``POST /reports/``        - any authenticated user; creates a DRAFT owned by
                                  the caller.
    * ``GET  /reports/me``      - the caller's own report history (paginated).
    * ``GET  /reports/{id}``    - the owner, or any Manager / Admin. A team member
                                  asking for someone else's report gets ``403``.
    * ``PUT  /reports/{id}``    - the owner only, and only while the report is in
                                  DRAFT or NEEDS_CORRECTION (else ``400``).

The controller stays thin: it delegates to
:class:`~app.services.report_service.ReportService` and lets the domain errors
raised there be turned into ``400`` / ``403`` / ``404`` responses by the
exception handlers registered in :mod:`app.main`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser
from app.models.report import ReportStatus
from app.schemas.report import (
    ReportCreateRequest,
    ReportListItemResponse,
    ReportListResponse,
    ReportResponse,
    ReportUpdateRequest,
)
from app.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])


def get_report_service() -> ReportService:
    """FastAPI dependency provider - swappable in tests."""
    return ReportService()


ReportSvc = Annotated[ReportService, Depends(get_report_service)]


@router.post(
    "/",
    response_model=ReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a weekly report (DRAFT)",
)
async def create_report(
    payload: ReportCreateRequest,
    current_user: CurrentUser,
    service: ReportSvc,
) -> ReportResponse:
    """Create a new report in ``DRAFT`` status for the authenticated user.

    Raises:
        HTTPException: ``400`` if ``project_id`` is unknown; ``422`` if the body
            fails structural validation.
    """
    report = await service.create_report(current_user, payload)
    return ReportResponse.from_report(report)


@router.get(
    "/me",
    response_model=ReportListResponse,
    summary="List the current user's report history",
)
async def list_my_reports(
    current_user: CurrentUser,
    service: ReportSvc,
    status_filter: Annotated[
        ReportStatus | None,
        Query(alias="status", description="Optional status filter"),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ReportListResponse:
    """Return a paginated list of the caller's reports, newest update first."""
    items, total = await service.list_my_reports(
        current_user, status=status_filter, page=page, page_size=page_size
    )
    return ReportListResponse(
        items=[ReportListItemResponse.from_report(report) for report in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{report_id}",
    response_model=ReportResponse,
    summary="Get one report in full (owner or Manager/Admin)",
)
async def get_report(
    report_id: str,
    current_user: CurrentUser,
    service: ReportSvc,
) -> ReportResponse:
    """Return full detail of a report, including its child task entries.

    Raises:
        HTTPException: ``403`` if a team member requests another user's report;
            ``404`` if the id does not exist.
    """
    report = await service.get_report(current_user, report_id)
    return ReportResponse.from_report(report)


@router.put(
    "/{report_id}",
    response_model=ReportResponse,
    summary="Update a report's content (owner; DRAFT / NEEDS_CORRECTION only)",
)
async def update_report(
    report_id: str,
    payload: ReportUpdateRequest,
    current_user: CurrentUser,
    service: ReportSvc,
) -> ReportResponse:
    """Update the content of an existing report the caller owns.

    Raises:
        HTTPException: ``400`` if the report is ``SUBMITTED`` / ``APPROVED`` or the
            merged state is invalid; ``403`` if the caller is not the owner;
            ``404`` if the id does not exist.
    """
    report = await service.update_report(current_user, report_id, payload)
    return ReportResponse.from_report(report)
