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
from typing import TYPE_CHECKING, Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.report import ReportStatus, TaskPriority, TaskStatus

if TYPE_CHECKING:
    from app.models.report import Report


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
