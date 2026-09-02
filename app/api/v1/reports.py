"""Personal weekly report endpoints.

Access summary:
    * ``POST /reports/``         - any authenticated user; creates a DRAFT owned by
                                   the caller.
    * ``GET  /reports/``         - Manager / Admin only; every team member's
                                   reports once they leave DRAFT (the review
                                   dashboard), paginated and filterable by
                                   status / user / project.
    * ``GET  /reports/me``       - the caller's own report history, newest week
                                   first (paginated, optional status filter).
    * ``GET  /reports/{id}``     - the owner always; a Manager / Admin only once
                                   the report has been submitted (a DRAFT is
                                   private to its author -> ``403`` otherwise).
    * ``GET  /reports/{id}/versions`` - past versions of that week's report, same
                                       access as ``GET /reports/{id}``.
    * ``PUT  /reports/{id}``     - the owner only, and only while the report is in
                                   DRAFT or NEEDS_CORRECTION (else ``400``).
    * ``POST /reports/{id}/submit``          - the owner; DRAFT / NEEDS_CORRECTION
                                               -> SUBMITTED.
    * ``POST /reports/{id}/approve``         - Manager / Admin; SUBMITTED ->
                                               APPROVED.
    * ``POST /reports/{id}/request-changes`` - Manager / Admin; SUBMITTED ->
                                               NEEDS_CORRECTION, with one comment.

Managers never edit a team member's report *content* - they only drive its
status and leave review comments through the workflow routes below.

The controller stays thin: it delegates to
:class:`~app.services.report_service.ReportService` and lets the domain errors
raised there be turned into ``400`` / ``403`` / ``404`` responses by the
exception handlers registered in :mod:`app.main`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, require_roles
from app.models.report import ReportStatus
from app.models.user import Role, User
from app.schemas.report import (
    ReportCreateRequest,
    ReportListItemResponse,
    ReportListResponse,
    ReportResponse,
    ReportUpdateRequest,
    ReportVersionResponse,
    RequestChangesRequest,
)
from app.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])

# Approve / request-changes are manager actions; the dependency raises 403 for a
# Team Member before the handler body runs.
ManagerOrAdmin = Annotated[User, Depends(require_roles(Role.MANAGER, Role.ADMIN))]


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
    "/",
    response_model=ReportListResponse,
    summary="List every team member's reports (Manager/Admin review dashboard)",
)
async def list_all_reports(
    _: ManagerOrAdmin,
    service: ReportSvc,
    status_filter: Annotated[
        ReportStatus | None,
        Query(alias="status", description="Optional status filter"),
    ] = None,
    user_id: Annotated[
        str | None, Query(description="Only reports owned by this user id")
    ] = None,
    project_id: Annotated[
        str | None, Query(description="Only reports for this project id")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ReportListResponse:
    """Return a paginated list of reports across all users, newest update first.

    Intended for the manager's review dashboard: pass ``status=SUBMITTED`` to see
    only what is currently awaiting review.

    Raises:
        HTTPException: ``403`` if the caller is a Team Member.
    """
    items, total = await service.list_all_reports(
        status=status_filter,
        user_id=user_id,
        project_id=project_id,
        page=page,
        page_size=page_size,
    )
    return ReportListResponse(
        items=[ReportListItemResponse.from_report(report) for report in items],
        total=total,
        page=page,
        page_size=page_size,
    )


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
    summary="Get one report in full (owner; Manager/Admin once submitted)",
)
async def get_report(
    report_id: str,
    current_user: CurrentUser,
    service: ReportSvc,
) -> ReportResponse:
    """Return full detail of a report - child task entries, the latest manager
    comment, the full review-comment history and every past version.

    Raises:
        HTTPException: ``403`` if the caller is neither the owner nor (for a
            non-DRAFT report) a Manager / Admin; ``404`` if the id does not exist.
    """
    report = await service.get_report(current_user, report_id)
    return ReportResponse.from_report(report)


@router.get(
    "/{report_id}/versions",
    response_model=list[ReportVersionResponse],
    summary="List a report's past versions (owner or Manager/Admin)",
)
async def list_report_versions(
    report_id: str,
    current_user: CurrentUser,
    service: ReportSvc,
) -> list[ReportVersionResponse]:
    """Return every archived version of this week's report, oldest first.

    One entry is added each time a manager sends the report back for correction;
    each carries the content as it was then, its submission timestamp and its
    version number (which review comments reference via ``against_version``).

    Raises:
        HTTPException: ``403`` if a team member requests another user's report;
            ``404`` if the id does not exist.
    """
    versions = await service.get_report_versions(current_user, report_id)
    return [ReportVersionResponse.from_version(version) for version in versions]


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


@router.post(
    "/{report_id}/submit",
    response_model=ReportResponse,
    summary="Submit a report for manager review (owner)",
)
async def submit_report(
    report_id: str,
    current_user: CurrentUser,
    service: ReportSvc,
) -> ReportResponse:
    """Move a report from DRAFT / NEEDS_CORRECTION to SUBMITTED.

    Resubmitting after a correction cycle sends it back to the manager's
    dashboard for another review.

    Raises:
        HTTPException: ``400`` if the report is already SUBMITTED / APPROVED or
            its content is inconsistent; ``403`` if the caller is not the owner;
            ``404`` if the id does not exist.
    """
    report = await service.submit_report(current_user, report_id)
    return ReportResponse.from_report(report)


@router.post(
    "/{report_id}/approve",
    response_model=ReportResponse,
    summary="Approve a submitted report (Manager/Admin)",
)
async def approve_report(
    report_id: str,
    current_user: ManagerOrAdmin,
    service: ReportSvc,
) -> ReportResponse:
    """Move a SUBMITTED report to APPROVED; no further edits are expected.

    Raises:
        HTTPException: ``400`` if the report is not currently SUBMITTED; ``403``
            if the caller is a Team Member; ``404`` if the id does not exist.
    """
    report = await service.approve_report(current_user, report_id)
    return ReportResponse.from_report(report)


@router.post(
    "/{report_id}/request-changes",
    response_model=ReportResponse,
    summary="Send a submitted report back for correction (Manager/Admin)",
)
async def request_changes(
    report_id: str,
    payload: RequestChangesRequest,
    current_user: ManagerOrAdmin,
    service: ReportSvc,
) -> ReportResponse:
    """Move a SUBMITTED report to NEEDS_CORRECTION with one general comment.

    The reviewed content is snapshotted into the report's version history first,
    so the team member's resubmission does not overwrite what the manager saw.
    The comment is added to the report's review history and is visible to the
    owner on their report page.

    Raises:
        HTTPException: ``400`` if the report is not currently SUBMITTED; ``403``
            if the caller is a Team Member; ``404`` if the id does not exist;
            ``422`` if ``comment`` is missing or empty.
    """
    report = await service.request_changes(
        current_user, report_id, payload.comment
    )
    return ReportResponse.from_report(report)
