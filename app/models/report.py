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

    Review cycle (Section 3)::

        DRAFT ──submit──▶ SUBMITTED ──approve──▶ APPROVED
                             │  ▲
                request-changes │  │ resubmit
                             ▼  │
                       NEEDS_CORRECTION
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

# Legal status transitions. A move not listed here is rejected by the service
# with a ``ReportTransitionError`` (translated to ``400`` by the API layer).
ALLOWED_TRANSITIONS: dict[ReportStatus, frozenset[ReportStatus]] = {
    ReportStatus.DRAFT: frozenset({ReportStatus.SUBMITTED}),
    ReportStatus.SUBMITTED: frozenset(
        {ReportStatus.APPROVED, ReportStatus.NEEDS_CORRECTION}
    ),
    ReportStatus.NEEDS_CORRECTION: frozenset({ReportStatus.SUBMITTED}),
    ReportStatus.APPROVED: frozenset(),
}


def can_transition(current: ReportStatus, target: ReportStatus) -> bool:
    """Whether *current* → *target* is a legal review-workflow move."""
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


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


class ReviewComment(BaseModel):
    """One "request changes" note left by a manager during the review cycle.

    The full list is kept per report (not just the latest) so the team member -
    and any manager reviewing later - can read the whole correction history.

    :attr:`against_version` ties the note to the :class:`ReportVersion` snapshot
    the manager was looking at when they wrote it, so it stays clear which
    version of the week's report a given comment was made against.
    """

    comment: str
    manager_id: str
    manager_name: str
    against_version: int
    created_at: datetime = Field(default_factory=_utcnow)


class ReportVersion(BaseModel):
    """A frozen copy of a report's content at the moment a manager sent it back.

    Each correction cycle (``NEEDS_CORRECTION`` -> edited -> resubmitted) leaves
    one of these behind, so every past version of that week's report stays
    visible instead of being overwritten by the resubmission.
    """

    version: int
    # When this content was archived (i.e. when the manager sent it back).
    snapshot_at: datetime = Field(default_factory=_utcnow)
    # When the team member submitted this particular version for review.
    submitted_at: datetime | None = None
    # Which status the report held when this snapshot was taken (always
    # ``SUBMITTED`` today - it is captured when the manager requests changes).
    status_at_snapshot: ReportStatus

    week_start_date: date
    week_end_date: date
    tasks_planned_next_week: str
    tasks_completed: list[ReportTask] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)
    achievements: list[Achievement] = Field(default_factory=list)
    hours_worked_breakdown: HoursWorkedBreakdown | None = None
    notes_or_links: str | None = None


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

    # -- Review workflow (Section 3) -----------------------------------------
    review_comments: list[ReviewComment] = Field(default_factory=list)
    version_history: list[ReportVersion] = Field(default_factory=list)
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    # Who took the most recent review action (approve / request-changes), for
    # the manager dashboard's activity feed.
    reviewed_by_id: str | None = None
    reviewed_by_name: str | None = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "reports"

    @property
    def is_editable(self) -> bool:
        """Whether the generic update route is allowed to mutate this report."""
        return self.status in EDITABLE_STATUSES

    @property
    def latest_review_comment(self) -> ReviewComment | None:
        """The most recent manager correction note, if the report has any."""
        return self.review_comments[-1] if self.review_comments else None

    @property
    def next_version_number(self) -> int:
        """The version number the next archived snapshot will carry."""
        return len(self.version_history) + 1

    def snapshot(self) -> ReportVersion:
        """Return a frozen copy of the current content for :attr:`version_history`."""
        return ReportVersion(
            version=self.next_version_number,
            submitted_at=self.submitted_at,
            status_at_snapshot=self.status,
            week_start_date=self.week_start_date,
            week_end_date=self.week_end_date,
            tasks_planned_next_week=self.tasks_planned_next_week,
            tasks_completed=[t.model_copy(deep=True) for t in self.tasks_completed],
            blockers=[b.model_copy(deep=True) for b in self.blockers],
            achievements=[a.model_copy(deep=True) for a in self.achievements],
            hours_worked_breakdown=(
                self.hours_worked_breakdown.model_copy(deep=True)
                if self.hours_worked_breakdown is not None
                else None
            ),
            notes_or_links=self.notes_or_links,
        )

    def touch(self) -> None:
        """Bump :attr:`updated_at` to the current UTC time."""
        self.updated_at = _utcnow()


__beanie_models__ = [Report]
