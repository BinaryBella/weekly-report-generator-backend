"""Beanie document model for the ``reports`` collection.

A *personal weekly report* is stored as a single document. Its nested collections
- completed tasks, blockers, achievements and the hours breakdown - are embedded
sub-documents rather than separate collections. In a relational design these
would be child tables with ``CASCADE DELETE`` wrapped in one ``@Transactional``
write; embedding gives the same guarantees natively, because a single MongoDB
document is inserted / replaced atomically (parent and children persist together
or not at all, and deleting the report removes every child with it).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated

from beanie import Document, Indexed
from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class ReportStatus(str, Enum):
    """Lifecycle state of a weekly report.

    Team members only ever create reports in :attr:`DRAFT`; every other
    transition is owned by a dedicated workflow endpoint, never the generic
    update route.
    """

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    NEEDS_CORRECTION = "NEEDS_CORRECTION"
    APPROVED = "APPROVED"


class TaskPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class TaskStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


# Reports may only be edited by their owner while in one of these states.
EDITABLE_STATUSES: frozenset[ReportStatus] = frozenset(
    {ReportStatus.DRAFT, ReportStatus.NEEDS_CORRECTION}
)


# ---------------------------------------------------------------------------
# Embedded sub-documents
# ---------------------------------------------------------------------------
class ReportTask(BaseModel):
    """A single task the report's author worked on during the week."""

    task_name: str
    priority: TaskPriority = TaskPriority.MEDIUM
    planned_percentage: int = 0
    actual_percentage: int = 0
    status: TaskStatus = TaskStatus.NOT_STARTED
    time_planned_hours: float = 0.0
    time_spent_hours: float = 0.0
    output_deliverable: str | None = None


class Blocker(BaseModel):
    """An impediment the author hit. Exactly one is the *key* issue for the week."""

    text: str
    is_key_issue: bool = False


class Achievement(BaseModel):
    """Something the author delivered. Exactly one is the *key* achievement."""

    text: str
    is_key_achievement: bool = False


class HoursWorkedBreakdown(BaseModel):
    """How the author's week split across activity types (hours)."""

    development: float = 0.0
    testing: float = 0.0
    meetings: float = 0.0
    documentation: float = 0.0
    other: float = 0.0


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------
class Report(Document):
    """A team member's personal weekly report for one project."""

    user_id: Annotated[str, Indexed()]
    project_id: Annotated[str, Indexed()]
    week_start_date: date
    week_end_date: date
    status: ReportStatus = ReportStatus.DRAFT
    tasks_planned_next_week: str
    tasks_completed: list[ReportTask] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)
    achievements: list[Achievement] = Field(default_factory=list)
    hours_worked_breakdown: HoursWorkedBreakdown | None = None
    notes_or_links: str | None = None
<<<<<<< Updated upstream
=======

    # -- Review workflow (Section 3) -----------------------------------------
    review_comments: list[ReviewComment] = Field(default_factory=list)
    version_history: list[ReportVersion] = Field(default_factory=list)
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    # Who took the most recent review action (approve / request-changes), for
    # the manager dashboard's activity feed.
    reviewed_by_id: str | None = None
    reviewed_by_name: str | None = None

>>>>>>> Stashed changes
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "reports"

    @property
    def is_editable(self) -> bool:
        """Whether the generic update route is allowed to mutate this report."""
        return self.status in EDITABLE_STATUSES

    def touch(self) -> None:
        """Bump :attr:`updated_at` to the current UTC time."""
        self.updated_at = _utcnow()


__beanie_models__ = [Report]
