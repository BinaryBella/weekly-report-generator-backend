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

Team dashboard (Section 4) - Manager / Admin only:
    * ``GET /reports/dashboard/status``          - per-member submission status for
                                                   a selected week (incl. NOT_STARTED).
    * ``GET /reports/dashboard/section/{name}``  - one report section across the
                                                   whole team for a selected week.

Insights dashboard (Section 6) - Manager / Admin only:
    * ``GET /reports/dashboard/summary``                       - headline metrics.
    * ``GET /reports/dashboard/charts/tasks-completed-trend``  - completed-tasks
                                                                 trend over time.
    * ``GET /reports/dashboard/charts/status-by-member``       - status split per
                                                                 team member.
    * ``GET /reports/dashboard/charts/workload-by-project``    - tasks / hours by
                                                                 project.
    * ``GET /reports/dashboard/charts/hours-by-type``          - team-wide hours by
                                                                 activity type.
    * ``GET /reports/dashboard/activity``                      - recent submissions
                                                                 & review actions.

Managers never edit a team member's report *content* - they only drive its
status and leave review comments through the workflow routes below.

The controller stays thin: it delegates to
:class:`~app.services.report_service.ReportService` and lets the domain errors
raised there be turned into ``400`` / ``403`` / ``404`` responses by the
exception handlers registered in :mod:`app.main`.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, require_roles
from app.models.report import ReportStatus
from app.models.user import Role, User
from app.schemas.auth import UserResponse
from app.schemas.report import (
    ActivityFeedResponse,
    DashboardSummaryResponse,
    HoursByTypeResponse,
    MemberProfileResponse,
    MemberStats,
    ReportCreateRequest,
    ReportListItemResponse,
    ReportListResponse,
    ReportResponse,
    ReportSection,
    ReportUpdateRequest,
    ReportVersionResponse,
    RequestChangesRequest,
    StatusByMemberResponse,
    TasksCompletedTrendResponse,
    TeamReportStatus,
    TeamSectionEntry,
    TeamSectionResponse,
    TeamStatusResponse,
    TeamStatusRow,
    WorkloadByProjectResponse,
    section_content,
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
        Query(alias="status", description="Filter by report status"),
    ] = None,
    user_id: Annotated[
        str | None, Query(description="Filter by team member (user id)")
    ] = None,
    project_id: Annotated[
        str | None, Query(description="Filter by project / category id")
    ] = None,
    week_start_date: Annotated[
        date | None,
        Query(description="Only the selected week (exact week-start date)"),
    ] = None,
    date_from: Annotated[
        date | None,
        Query(description="Range start - reports whose week starts on/after this"),
    ] = None,
    date_to: Annotated[
        date | None,
        Query(description="Range end - reports whose week starts on/before this"),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ReportListResponse:
    """Team dashboard: every team member's reports, newest week first.

    All filters are optional and AND-combined - by status, team member
    (``user_id``), project/category (``project_id``), a single selected week
    (``week_start_date``) or a date range (``date_from`` / ``date_to``). Pass
    ``status=SUBMITTED`` to see only what is currently awaiting review. Private
    drafts are never listed here.

    Raises:
        HTTPException: ``403`` if the caller is a Team Member.
    """
    items, total = await service.list_all_reports(
        status=status_filter,
        user_id=user_id,
        project_id=project_id,
        week_start_date=week_start_date,
        date_from=date_from,
        date_to=date_to,
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
    "/dashboard/status",
    response_model=TeamStatusResponse,
    summary="Track each team member's submission status for a week (Manager/Admin)",
)
async def team_status_overview(
    _: ManagerOrAdmin,
    service: ReportSvc,
    week_start_date: Annotated[
        date, Query(description="The selected week (exact week-start date)")
    ],
    project_id: Annotated[
        str | None,
        Query(
            description="Limit the roster to this project's members "
            "(default: every active team member)"
        ),
    ] = None,
) -> TeamStatusResponse:
    """One row per team member for the selected week, with their report status.

    The status is one of ``DRAFT`` / ``SUBMITTED`` / ``NEEDS_CORRECTION`` /
    ``APPROVED``, or ``NOT_STARTED`` for a member who has no report for that week
    yet. Draft *content* is not disclosed - only that a draft exists.

    Raises:
        HTTPException: ``403`` if the caller is a Team Member; ``400`` if
            ``project_id`` is given but unknown.
    """
    pairs = await service.team_week_reports(
        week_start_date=week_start_date, project_id=project_id
    )
    rows = [TeamStatusRow.build(user, report) for user, report in pairs]
    counts = {member_status.value: 0 for member_status in TeamReportStatus}
    for row in rows:
        counts[row.status.value] += 1
    return TeamStatusResponse(
        week_start_date=week_start_date,
        project_id=project_id,
        total_members=len(rows),
        status_counts=counts,
        rows=rows,
    )


@router.get(
    "/dashboard/section/{section}",
    response_model=TeamSectionResponse,
    summary="View one report section across the whole team for a week (Manager/Admin)",
)
async def team_section_overview(
    section: ReportSection,
    _: ManagerOrAdmin,
    service: ReportSvc,
    week_start_date: Annotated[
        date, Query(description="The selected week (exact week-start date)")
    ],
    project_id: Annotated[
        str | None,
        Query(description="Limit the roster to this project's members"),
    ] = None,
) -> TeamSectionResponse:
    """Line one section (e.g. ``blockers`` or ``achievements``) up across the team.

    Returns one entry per team member for the selected week, carrying just that
    section's content - so a manager can compare the whole team side by side
    without opening each report. ``content`` is ``null`` for a member who has not
    started, or whose report is still a private ``DRAFT``.

    Raises:
        HTTPException: ``403`` if the caller is a Team Member; ``400`` if
            ``project_id`` is given but unknown; ``422`` for an unknown section.
    """
    pairs = await service.team_week_reports(
        week_start_date=week_start_date, project_id=project_id
    )
    entries = [
        TeamSectionEntry(
            user_id=str(user.id),
            user_name=user.name,
            status=TeamReportStatus.of(report),
            report_id=str(report.id) if report is not None else None,
            content=(
                section_content(report, section)
                if report is not None and report.status is not ReportStatus.DRAFT
                else None
            ),
        )
        for user, report in pairs
    ]
    return TeamSectionResponse(
        week_start_date=week_start_date,
        section=section,
        project_id=project_id,
        entries=entries,
    )


# ---------------------------------------------------------------------------
# Section 6 - Dashboard & visual insights (Manager/Admin only)
# ---------------------------------------------------------------------------
@router.get(
    "/dashboard/summary",
    response_model=DashboardSummaryResponse,
    summary="Headline metrics for the selected week (Manager/Admin)",
)
async def dashboard_summary(
    _: ManagerOrAdmin,
    service: ReportSvc,
    week_start_date: Annotated[
        date, Query(description="The selected week (exact week-start date)")
    ],
    project_id: Annotated[
        str | None, Query(description="Limit to this project's members / reports")
    ] = None,
) -> DashboardSummaryResponse:
    """Return the four summary metrics for the selected week.

    Total reports submitted, submission compliance (submitted / pending /
    on-time / late), reports currently in NEEDS_CORRECTION, and open blockers
    across the team.

    Raises:
        HTTPException: ``403`` if the caller is a Team Member; ``400`` if
            ``project_id`` is given but unknown.
    """
    data = await service.dashboard_summary(
        week_start_date=week_start_date, project_id=project_id
    )
    return DashboardSummaryResponse.model_validate(data)


@router.get(
    "/dashboard/charts/tasks-completed-trend",
    response_model=TasksCompletedTrendResponse,
    summary="Completed-tasks trend over time (Manager/Admin)",
)
async def tasks_completed_trend(
    _: ManagerOrAdmin,
    service: ReportSvc,
    weeks: Annotated[int, Query(ge=1, le=52, description="Trailing weeks to include")] = 8,
    group_by: Annotated[
        Literal["team", "user"],
        Query(description="One team-wide series, or one per person"),
    ] = "team",
    project_id: Annotated[str | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> TasksCompletedTrendResponse:
    """Completed tasks per report-week, team-wide or per team member.

    Raises:
        HTTPException: ``403`` if the caller is a Team Member.
    """
    data = await service.tasks_completed_trend(
        weeks=weeks,
        group_by=group_by,
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
    )
    return TasksCompletedTrendResponse.model_validate(data)


@router.get(
    "/dashboard/charts/status-by-member",
    response_model=StatusByMemberResponse,
    summary="Report submission / approval status by team member (Manager/Admin)",
)
async def status_by_member(
    _: ManagerOrAdmin,
    service: ReportSvc,
    week_start_date: Annotated[
        date | None, Query(description="A single selected week (enables NOT_STARTED)")
    ] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    project_id: Annotated[str | None, Query()] = None,
) -> StatusByMemberResponse:
    """Per-member counts of reports by status, for a week or a date range.

    Raises:
        HTTPException: ``403`` if the caller is a Team Member; ``400`` if
            ``project_id`` is given but unknown.
    """
    data = await service.status_by_member(
        week_start_date=week_start_date,
        date_from=date_from,
        date_to=date_to,
        project_id=project_id,
    )
    return StatusByMemberResponse.model_validate(data)


@router.get(
    "/dashboard/charts/workload-by-project",
    response_model=WorkloadByProjectResponse,
    summary="Workload / task distribution by project (Manager/Admin)",
)
async def workload_by_project(
    _: ManagerOrAdmin,
    service: ReportSvc,
    week_start_date: Annotated[date | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> WorkloadByProjectResponse:
    """Reports, task counts and planned-vs-spent hours grouped by project.

    Raises:
        HTTPException: ``403`` if the caller is a Team Member.
    """
    data = await service.workload_by_project(
        week_start_date=week_start_date, date_from=date_from, date_to=date_to
    )
    return WorkloadByProjectResponse.model_validate(data)


@router.get(
    "/dashboard/charts/hours-by-type",
    response_model=HoursByTypeResponse,
    summary="Time spent by task type, team-wide (Manager/Admin)",
)
async def hours_by_type(
    _: ManagerOrAdmin,
    service: ReportSvc,
    week_start_date: Annotated[date | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    project_id: Annotated[str | None, Query()] = None,
) -> HoursByTypeResponse:
    """Team-wide sum of the hours-worked breakdown (development / testing / …).

    Raises:
        HTTPException: ``403`` if the caller is a Team Member.
    """
    data = await service.hours_by_type(
        week_start_date=week_start_date,
        date_from=date_from,
        date_to=date_to,
        project_id=project_id,
    )
    return HoursByTypeResponse.model_validate(data)


@router.get(
    "/dashboard/activity",
    response_model=ActivityFeedResponse,
    summary="Recent reports & review actions feed (Manager/Admin)",
)
async def activity_feed(
    _: ManagerOrAdmin,
    service: ReportSvc,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    project_id: Annotated[str | None, Query()] = None,
) -> ActivityFeedResponse:
    """Newest-first feed of submissions, approvals and change requests.

    Raises:
        HTTPException: ``403`` if the caller is a Team Member.
    """
    data = await service.activity_feed(limit=limit, project_id=project_id)
    return ActivityFeedResponse.model_validate(data)


@router.get(
    "/dashboard/member/{user_id}",
    response_model=MemberProfileResponse,
    summary="Team member profile: identity, basic stats & history (Manager/Admin)",
)
async def member_profile(
    user_id: str,
    _: ManagerOrAdmin,
    service: ReportSvc,
    limit: Annotated[
        int, Query(ge=1, le=50, description="How many recent reports to include")
    ] = 5,
) -> MemberProfileResponse:
    """Powers the "click a team member" profile page.

    Identity (name / email / role / status), basic all-time stats (report
    counts by status, approval rate, tasks completed, hours logged, last
    submission) and the most recent reports. Only counts reports that have left
    DRAFT - a member's drafts stay private even to their manager.

    Raises:
        HTTPException: ``403`` if the caller is a Team Member; ``404`` if
            ``user_id`` does not exist.
    """
    data = await service.member_profile(user_id, limit=limit)
    return MemberProfileResponse(
        user=UserResponse.from_user(data["user"]),
        stats=MemberStats(**data["stats"]),
        recent_reports=[
            ReportListItemResponse.from_report(r) for r in data["recent_reports"]
        ],
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
