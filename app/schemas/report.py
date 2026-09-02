"""Pydantic v2 request/response models for personal weekly reports.

Validation policy
-----------------
* **Fixed structure.** Every request model sets ``extra="forbid"``; a payload
  carrying any field that is not declared here (an attempt at a dynamic custom
  field) is rejected with ``422`` before it reaches the service.
* **Key flags.** When ``blockers`` are supplied, *exactly one* must carry
  ``is_key_issue=true``; likewise *exactly one* ``achievements`` entry must carry
  ``is_key_achievement=true``.
* **Week range.** ``week_start_date`` must fall strictly before ``week_end_date``.
* **Task entries.** ``task_name`` is non-empty, percentages are ``0..100`` and
  hour figures are non-negative.

Status is never accepted on create or update - it moves only through dedicated
workflow transitions - so it simply is not a field on the request models.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.report import ReportStatus, TaskPriority, TaskStatus
from app.schemas.auth import UserResponse

if TYPE_CHECKING:
    from app.models.report import Report
    from app.models.user import User


# ---------------------------------------------------------------------------
# Shared validation helpers
# ---------------------------------------------------------------------------
def ensure_single_key_flag(items: Iterable[object], attr: str, label: str) -> None:
    """Raise ``ValueError`` unless exactly one *item* has *attr* set truthy.

    A no-op when *items* is empty - the "key" flag is only meaningful once at
    least one blocker / achievement exists.
    """
    materialised = list(items)
    if not materialised:
        return
    flagged = sum(1 for item in materialised if getattr(item, attr, False))
    if flagged != 1:
        raise ValueError(
            f"exactly one {label} must have {attr}=true (found {flagged})"
        )


def ensure_week_range(start: date | None, end: date | None) -> None:
    """Raise ``ValueError`` if both dates are present and out of order."""
    if start is not None and end is not None and start >= end:
        raise ValueError("week_start_date must be before week_end_date")


# ---------------------------------------------------------------------------
# Nested request models
# ---------------------------------------------------------------------------
class ReportTaskInput(BaseModel):
    """One completed-task row on a create/update request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task_name: str = Field(min_length=1, max_length=200)
    priority: TaskPriority = TaskPriority.MEDIUM
    planned_percentage: int = Field(default=0, ge=0, le=100)
    actual_percentage: int = Field(default=0, ge=0, le=100)
    status: TaskStatus = TaskStatus.NOT_STARTED
    time_planned_hours: float = Field(default=0.0, ge=0)
    time_spent_hours: float = Field(default=0.0, ge=0)
    output_deliverable: str | None = Field(default=None, max_length=2000)


class BlockerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=2000)
    is_key_issue: bool = False


class AchievementInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=2000)
    is_key_achievement: bool = False


class HoursWorkedBreakdownInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    development: float = Field(default=0.0, ge=0)
    testing: float = Field(default=0.0, ge=0)
    meetings: float = Field(default=0.0, ge=0)
    documentation: float = Field(default=0.0, ge=0)
    other: float = Field(default=0.0, ge=0)


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class ReportCreateRequest(BaseModel):
    """Payload for ``POST /reports`` - always creates a ``DRAFT`` for the caller."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_id: str = Field(min_length=1)
    week_start_date: date
    week_end_date: date
    tasks_planned_next_week: str = Field(min_length=1, max_length=5000)
    tasks_completed: list[ReportTaskInput] = Field(default_factory=list)
    blockers: list[BlockerInput] = Field(default_factory=list)
    achievements: list[AchievementInput] = Field(default_factory=list)
    hours_worked_breakdown: HoursWorkedBreakdownInput | None = None
    notes_or_links: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _check_structure(self) -> "ReportCreateRequest":
        ensure_week_range(self.week_start_date, self.week_end_date)
        ensure_single_key_flag(self.blockers, "is_key_issue", "blocker")
        ensure_single_key_flag(self.achievements, "is_key_achievement", "achievement")
        return self


class ReportUpdateRequest(BaseModel):
    """Payload for ``PUT /reports/{id}`` - partial; only supplied fields change.

    ``status`` and ``user_id`` are intentionally absent: a team member cannot
    reassign a report or drive its status from here.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_id: str | None = Field(default=None, min_length=1)
    week_start_date: date | None = None
    week_end_date: date | None = None
    tasks_planned_next_week: str | None = Field(default=None, min_length=1, max_length=5000)
    tasks_completed: list[ReportTaskInput] | None = None
    blockers: list[BlockerInput] | None = None
    achievements: list[AchievementInput] | None = None
    hours_worked_breakdown: HoursWorkedBreakdownInput | None = None
    notes_or_links: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _check_structure(self) -> "ReportUpdateRequest":
        ensure_week_range(self.week_start_date, self.week_end_date)
        if self.blockers is not None:
            ensure_single_key_flag(self.blockers, "is_key_issue", "blocker")
        if self.achievements is not None:
            ensure_single_key_flag(
                self.achievements, "is_key_achievement", "achievement"
            )
        return self


# ---------------------------------------------------------------------------
# Nested response models
# ---------------------------------------------------------------------------
class ReportTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_name: str
    priority: TaskPriority
    planned_percentage: int
    actual_percentage: int
    status: TaskStatus
    time_planned_hours: float
    time_spent_hours: float
    output_deliverable: str | None


class BlockerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str
    is_key_issue: bool


class AchievementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str
    is_key_achievement: bool


class HoursWorkedBreakdownResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    development: float
    testing: float
    meetings: float
    documentation: float
    other: float


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class ReportResponse(BaseModel):
    """Full detail of a single report, including every child task entry."""

    id: str
    user_id: str
    project_id: str
    week_start_date: date
    week_end_date: date
    status: ReportStatus
    tasks_planned_next_week: str
    tasks_completed: list[ReportTaskResponse]
    blockers: list[BlockerResponse]
    achievements: list[AchievementResponse]
    hours_worked_breakdown: HoursWorkedBreakdownResponse | None
    notes_or_links: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_report(cls, report: "Report") -> "ReportResponse":
        return cls(
            id=str(report.id),
            user_id=report.user_id,
            project_id=report.project_id,
            week_start_date=report.week_start_date,
            week_end_date=report.week_end_date,
            status=report.status,
            tasks_planned_next_week=report.tasks_planned_next_week,
            tasks_completed=[
                ReportTaskResponse.model_validate(task)
                for task in report.tasks_completed
            ],
            blockers=[
                BlockerResponse.model_validate(blocker)
                for blocker in report.blockers
            ],
            achievements=[
                AchievementResponse.model_validate(achievement)
                for achievement in report.achievements
            ],
            hours_worked_breakdown=(
                HoursWorkedBreakdownResponse.model_validate(
                    report.hours_worked_breakdown
                )
                if report.hours_worked_breakdown is not None
                else None
            ),
            notes_or_links=report.notes_or_links,
            created_at=report.created_at,
            updated_at=report.updated_at,
        )


class ReportListItemResponse(BaseModel):
    """One row in the current user's report-history list."""

    id: str
    project_id: str
    week_start_date: date
    week_end_date: date
    status: ReportStatus
    updated_at: datetime

    @classmethod
    def from_report(cls, report: "Report") -> "ReportListItemResponse":
        return cls(
            id=str(report.id),
            project_id=report.project_id,
            week_start_date=report.week_start_date,
            week_end_date=report.week_end_date,
            status=report.status,
            updated_at=report.updated_at,
        )


class ReportListResponse(BaseModel):
    """Paginated envelope returned by ``GET /reports/me``."""

    items: list[ReportListItemResponse]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Section 4 - Team dashboard (manager view)
# ---------------------------------------------------------------------------
class ReportSection(str, Enum):
    """A single named part of a report, for the "one section across the team" view."""

    TASKS_COMPLETED = "tasks_completed"
    TASKS_PLANNED_NEXT_WEEK = "tasks_planned_next_week"
    BLOCKERS = "blockers"
    ACHIEVEMENTS = "achievements"
    HOURS_WORKED_BREAKDOWN = "hours_worked_breakdown"
    NOTES_OR_LINKS = "notes_or_links"


class TeamReportStatus(str, Enum):
    """Per-member submission state for a given week on the team dashboard.

    The four :class:`~app.models.report.ReportStatus` values plus
    :attr:`NOT_STARTED` for a team member who has no report for that week yet.
    """

    NOT_STARTED = "NOT_STARTED"
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    NEEDS_CORRECTION = "NEEDS_CORRECTION"
    APPROVED = "APPROVED"

    @classmethod
    def of(cls, report: "Report | None") -> "TeamReportStatus":
        return cls.NOT_STARTED if report is None else cls(report.status.value)


def section_content(report: "Report", section: ReportSection) -> Any:
    """Return the JSON-ready value of one *section* of *report*.

    Used to line a single section up across the whole team without shipping the
    rest of each report.
    """
    if section is ReportSection.TASKS_COMPLETED:
        return [t.model_dump(mode="json") for t in report.tasks_completed]
    if section is ReportSection.BLOCKERS:
        return [b.model_dump(mode="json") for b in report.blockers]
    if section is ReportSection.ACHIEVEMENTS:
        return [a.model_dump(mode="json") for a in report.achievements]
    if section is ReportSection.HOURS_WORKED_BREAKDOWN:
        return (
            report.hours_worked_breakdown.model_dump(mode="json")
            if report.hours_worked_breakdown is not None
            else None
        )
    if section is ReportSection.TASKS_PLANNED_NEXT_WEEK:
        return report.tasks_planned_next_week
    return report.notes_or_links  # ReportSection.NOTES_OR_LINKS


class TeamStatusRow(BaseModel):
    """One team member's submission state for the selected week."""

    user_id: str
    user_name: str
    user_email: str
    status: TeamReportStatus
    report_id: str | None
    project_id: str | None
    week_end_date: date | None
    submitted_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def build(cls, user: "User", report: "Report | None") -> "TeamStatusRow":
        return cls(
            user_id=str(user.id),
            user_name=user.name,
            user_email=user.email,
            status=TeamReportStatus.of(report),
            report_id=str(report.id) if report is not None else None,
            project_id=report.project_id if report is not None else None,
            week_end_date=report.week_end_date if report is not None else None,
            submitted_at=report.submitted_at if report is not None else None,
            updated_at=report.updated_at if report is not None else None,
        )


class TeamStatusResponse(BaseModel):
    """Submission tracking across the team for one week."""

    week_start_date: date
    project_id: str | None
    total_members: int
    status_counts: dict[str, int]
    rows: list[TeamStatusRow]


class TeamSectionEntry(BaseModel):
    """One team member's copy of the requested section for the selected week."""

    user_id: str
    user_name: str
    status: TeamReportStatus
    report_id: str | None
    # The section payload. ``None`` when the member has not started, or when the
    # report is still a private DRAFT (its content is not disclosed to managers).
    content: Any = None


class TeamSectionResponse(BaseModel):
    """A single report section lined up across the whole team for one week."""

    week_start_date: date
    section: ReportSection
    project_id: str | None
    entries: list[TeamSectionEntry]


# ---------------------------------------------------------------------------
# Section 6 - Dashboard & visual insights (manager view)
# ---------------------------------------------------------------------------
class SubmissionCompliance(BaseModel):
    """Submitted vs pending vs late, for the selected week's roster."""

    submitted: int
    pending: int  # not started yet, or still a private draft
    on_time: int  # submitted on/before that report's week-end date
    late: int  # submitted after that report's week-end date
    compliance_rate: float  # submitted / total_members  (0..1)
    on_time_rate: float  # on_time / total_members   (0..1)


class DashboardSummaryResponse(BaseModel):
    """The four headline metrics for the selected week."""

    week_start_date: date
    project_id: str | None
    total_members: int
    total_submitted_this_week: int
    submission_compliance: SubmissionCompliance
    needs_correction_count: int
    open_blockers: int
    open_key_issues: int


class TrendPoint(BaseModel):
    week_start_date: date
    reports: int
    completed_tasks: int
    total_tasks: int


class TrendSeries(BaseModel):
    key: str  # "team", or a user id when group_by=user
    label: str  # "Team-wide", or the member's name
    points: list[TrendPoint]


class TasksCompletedTrendResponse(BaseModel):
    """Completed-tasks trend over time, team-wide or per person."""

    group_by: str  # "team" | "user"
    project_id: str | None
    series: list[TrendSeries]


class StatusByMemberRow(BaseModel):
    user_id: str
    user_name: str
    not_started: int
    draft: int
    submitted: int
    needs_correction: int
    approved: int


class StatusByMemberResponse(BaseModel):
    """Report submission / approval status broken down by team member."""

    week_start_date: date | None
    date_from: date | None
    date_to: date | None
    project_id: str | None
    rows: list[StatusByMemberRow]


class WorkloadByProjectRow(BaseModel):
    project_id: str
    project_name: str
    reports: int
    tasks: int
    planned_hours: float
    spent_hours: float


class WorkloadByProjectResponse(BaseModel):
    """Workload / task distribution across projects."""

    week_start_date: date | None
    date_from: date | None
    date_to: date | None
    rows: list[WorkloadByProjectRow]


class HoursByTypeResponse(BaseModel):
    """Team-wide time split across activity types (hours)."""

    week_start_date: date | None
    date_from: date | None
    date_to: date | None
    project_id: str | None
    reports_counted: int
    development: float
    testing: float
    meetings: float
    documentation: float
    other: float
    total: float


class ActivityEvent(BaseModel):
    """One entry in the recent-activity feed."""

    type: str  # SUBMITTED | APPROVED | CHANGES_REQUESTED
    at: datetime
    report_id: str
    week_start_date: date
    project_id: str
    author_id: str  # the report's owner
    author_name: str
    actor_id: str | None  # the manager, for review actions
    actor_name: str | None
    comment: str | None  # the general comment, for CHANGES_REQUESTED


class ActivityFeedResponse(BaseModel):
    events: list[ActivityEvent]


# ---------------------------------------------------------------------------
# Team member profile (manager view)
# ---------------------------------------------------------------------------
class MemberStats(BaseModel):
    """Basic at-a-glance stats for one team member, all-time.

    Counts only reports that have left DRAFT (a member's private drafts are
    never disclosed to a manager), consistent with the rest of the dashboard.
    """

    total_reports: int
    submitted_count: int
    needs_correction_count: int
    approved_count: int
    approval_rate: float  # approved / (approved + needs_correction), 0 if none reviewed
    total_tasks_completed: int
    total_hours_logged: float
    last_submitted_at: datetime | None


class MemberProfileResponse(BaseModel):
    """Team member profile page: identity, basic stats and recent report history."""

    user: UserResponse
    stats: MemberStats
    recent_reports: list[ReportListItemResponse]
