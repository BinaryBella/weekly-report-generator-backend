"""Business rules for personal weekly reports.

The service raises framework-agnostic domain errors; the API layer
(:mod:`app.main`) registers exception handlers that translate them into
``400`` / ``403`` / ``404`` responses.

Transactional boundary
----------------------
A report and its nested items (completed tasks, blockers, achievements, hours
breakdown) are one embedded document, so :meth:`ReportRepository.add` /
:meth:`~ReportRepository.save` is a single atomic write - the relational
equivalent of wrapping the parent insert and every child insert in one
``@Transactional`` method.
"""

from __future__ import annotations

from datetime import datetime, timezone

from bson.errors import InvalidId

from app.models.project import Project
from app.models.report import (
    Achievement,
    Blocker,
    HoursWorkedBreakdown,
    Report,
    ReportStatus,
    ReportTask,
    ReportVersion,
    ReviewComment,
    can_transition,
)
from app.models.user import Role, User
from app.repositories.report_repository import ReportRepository
from app.schemas.report import (
    ReportCreateRequest,
    ReportUpdateRequest,
    ensure_single_key_flag,
    ensure_week_range,
)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Domain errors
# ---------------------------------------------------------------------------
class ReportServiceError(Exception):
    """Base class for report domain errors."""


class ReportNotFoundError(ReportServiceError):
    """Raised when a report id does not resolve to a stored document."""

    def __init__(self, report_id: str) -> None:
        super().__init__(f"Report '{report_id}' was not found")
        self.report_id = report_id


class ReportAccessDeniedError(ReportServiceError):
    """Raised when the caller may not view / modify another user's report."""

    def __init__(self, report_id: str) -> None:
        super().__init__("You do not have access to this report")
        self.report_id = report_id


class ReportNotEditableError(ReportServiceError):
    """Raised when an update targets a report that is no longer editable."""

    def __init__(self, status: ReportStatus) -> None:
        super().__init__(
            f"A report in '{status.value}' status cannot be edited; it may only "
            "be changed while in DRAFT or NEEDS_CORRECTION"
        )
        self.status = status


class ReportValidationError(ReportServiceError):
    """Raised when a (possibly partial) update leaves the report inconsistent."""


class ReportTransitionError(ReportServiceError):
    """Raised when a review-workflow action is illegal from the current status."""

    def __init__(self, current: ReportStatus, action: str) -> None:
        super().__init__(
            f"Cannot {action} a report in '{current.value}' status"
        )
        self.current = current
        self.action = action


# ---------------------------------------------------------------------------
# DTO -> embedded model mapping
# ---------------------------------------------------------------------------
def _to_tasks(items) -> list[ReportTask]:
    return [ReportTask(**item.model_dump()) for item in items]


def _to_blockers(items) -> list[Blocker]:
    return [Blocker(**item.model_dump()) for item in items]


def _to_achievements(items) -> list[Achievement]:
    return [Achievement(**item.model_dump()) for item in items]


def _to_hours(value) -> HoursWorkedBreakdown | None:
    return HoursWorkedBreakdown(**value.model_dump()) if value is not None else None


def _assert_invariants(report: Report) -> None:
    """Re-check the cross-field rules on the final state of a report."""
    try:
        ensure_week_range(report.week_start_date, report.week_end_date)
        ensure_single_key_flag(report.blockers, "is_key_issue", "blocker")
        ensure_single_key_flag(
            report.achievements, "is_key_achievement", "achievement"
        )
    except ValueError as exc:
        raise ReportValidationError(str(exc)) from exc


class ReportService:
    """Coordinates ownership, editability and nested-item mapping for reports."""

    def __init__(self, repository: ReportRepository | None = None) -> None:
        self._repo = repository or ReportRepository()

    # -- Commands ---------------------------------------------------------------
    async def create_report(
        self, current_user: User, data: ReportCreateRequest
    ) -> Report:
        """Create a new report in ``DRAFT`` for the authenticated user.

        Raises:
            ReportValidationError: if ``project_id`` does not resolve to a project
                or the payload is internally inconsistent.
        """
        await self._assert_project_exists(data.project_id)

        report = Report(
            user_id=str(current_user.id),
            project_id=data.project_id,
            week_start_date=data.week_start_date,
            week_end_date=data.week_end_date,
            status=ReportStatus.DRAFT,
            tasks_planned_next_week=data.tasks_planned_next_week,
            tasks_completed=_to_tasks(data.tasks_completed),
            blockers=_to_blockers(data.blockers),
            achievements=_to_achievements(data.achievements),
            hours_worked_breakdown=_to_hours(data.hours_worked_breakdown),
            notes_or_links=data.notes_or_links,
        )
        _assert_invariants(report)
        return await self._repo.add(report)

    async def update_report(
        self, current_user: User, report_id: str, data: ReportUpdateRequest
    ) -> Report:
        """Apply a partial content update to a report the caller owns.

        Raises:
            ReportNotFoundError: if the id is unknown.
            ReportAccessDeniedError: if the caller is not the report's owner.
            ReportNotEditableError: if the report is ``SUBMITTED`` or ``APPROVED``.
            ReportValidationError: if the merged state breaks a cross-field rule
                or ``project_id`` is changed to an unknown project.
        """
        report = await self._load(report_id)
        if report.user_id != str(current_user.id):
            raise ReportAccessDeniedError(report_id)
        if not report.is_editable:
            raise ReportNotEditableError(report.status)

        changed = data.model_fields_set
        if "project_id" in changed and data.project_id != report.project_id:
            await self._assert_project_exists(data.project_id)

        _mappers = {
            "tasks_completed": lambda v: _to_tasks(v or []),
            "blockers": lambda v: _to_blockers(v or []),
            "achievements": lambda v: _to_achievements(v or []),
            "hours_worked_breakdown": _to_hours,
        }
        for field in changed:
            raw = getattr(data, field)
            setattr(report, field, _mappers.get(field, lambda v: v)(raw))

        _assert_invariants(report)
        report.touch()
        return await self._repo.save(report)

    # -- Review workflow (Section 3) ---------------------------------------
    async def submit_report(self, current_user: User, report_id: str) -> Report:
        """Hand a report to the manager for review.

        Legal from ``DRAFT`` (first submission) and ``NEEDS_CORRECTION``
        (resubmission after edits); both move the report to ``SUBMITTED``.

        Raises:
            ReportNotFoundError: if the id is unknown.
            ReportAccessDeniedError: if the caller is not the report's owner.
            ReportTransitionError: if the report is already ``SUBMITTED`` or
                ``APPROVED``.
            ReportValidationError: if the report's content is inconsistent.
        """
        report = await self._load(report_id)
        if report.user_id != str(current_user.id):
            raise ReportAccessDeniedError(report_id)
        if not can_transition(report.status, ReportStatus.SUBMITTED):
            raise ReportTransitionError(report.status, "submit")

        _assert_invariants(report)
        report.status = ReportStatus.SUBMITTED
        report.submitted_at = _utcnow()
        report.touch()
        return await self._repo.save(report)

    async def approve_report(self, current_user: User, report_id: str) -> Report:
        """Mark a submitted report as approved; no further edits are expected.

        The caller must be a Manager / Admin (enforced by the route). Only a
        ``SUBMITTED`` report can be approved.

        Raises:
            ReportNotFoundError: if the id is unknown.
            ReportTransitionError: if the report is not currently ``SUBMITTED``.
        """
        report = await self._load(report_id)
        if not can_transition(report.status, ReportStatus.APPROVED):
            raise ReportTransitionError(report.status, "approve")

        report.status = ReportStatus.APPROVED
        report.reviewed_at = _utcnow()
        report.touch()
        return await self._repo.save(report)

    async def request_changes(
        self, current_user: User, report_id: str, comment: str
    ) -> Report:
        """Send a submitted report back with one general correction comment.

        Snapshots the reviewed content into :attr:`Report.version_history` so the
        version the manager saw stays visible after the team member edits and
        resubmits, appends *comment* to the review history, and moves the report
        to ``NEEDS_CORRECTION`` (editable again by its owner).

        The caller must be a Manager / Admin (enforced by the route).

        Raises:
            ReportNotFoundError: if the id is unknown.
            ReportTransitionError: if the report is not currently ``SUBMITTED``.
        """
        report = await self._load(report_id)
        if not can_transition(report.status, ReportStatus.NEEDS_CORRECTION):
            raise ReportTransitionError(report.status, "request changes on")

        snapshot = report.snapshot()
        report.version_history.append(snapshot)
        report.review_comments.append(
            ReviewComment(
                comment=comment,
                manager_id=str(current_user.id),
                manager_name=current_user.name,
                against_version=snapshot.version,
            )
        )
        report.status = ReportStatus.NEEDS_CORRECTION
        report.reviewed_at = _utcnow()
        report.touch()
        return await self._repo.save(report)

    # -- Queries --------------------------------------------------------------
    async def get_report(self, current_user: User, report_id: str) -> Report:
        """Return one report in full.

        Access: the owning team member always; a Manager / Admin only once the
        report has left ``DRAFT`` (a draft is "only visible to them" until it is
        submitted for review).

        Raises:
            ReportNotFoundError: if the id is unknown.
            ReportAccessDeniedError: if the caller may not see this report.
        """
        report = await self._load(report_id)
        if not self._may_view(current_user, report):
            raise ReportAccessDeniedError(report_id)
        return report

    async def get_report_versions(
        self, current_user: User, report_id: str
    ) -> list[ReportVersion]:
        """Return the archived past versions of a report, oldest first.

        Same access rules as :meth:`get_report`.
        """
        report = await self.get_report(current_user, report_id)
        return list(report.version_history)

    async def list_my_reports(
        self,
        current_user: User,
        *,
        status: ReportStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Report], int]:
        """Return one page of the caller's own reports plus the total count."""
        skip = (page - 1) * page_size
        items = await self._repo.list_for_user(
            str(current_user.id), status=status, skip=skip, limit=page_size
        )
        total = await self._repo.count_for_user(str(current_user.id), status=status)
        return items, total

    async def list_all_reports(
        self,
        *,
        status: ReportStatus | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Report], int]:
        """Return one page of every team member's reports (manager dashboard).

        Access is restricted to Manager / Admin at the route; this method assumes
        the caller has already been authorised. Private drafts are never
        included - a report only reaches the dashboard once it is submitted.
        """
        if status is ReportStatus.DRAFT:
            return [], 0
        skip = (page - 1) * page_size
        items = await self._repo.list_all(
            status=status,
            user_id=user_id,
            project_id=project_id,
            skip=skip,
            limit=page_size,
        )
        total = await self._repo.count_all(
            status=status, user_id=user_id, project_id=project_id
        )
        return items, total

    # -- Internals ----------------------------------------------------------
    async def _load(self, report_id: str) -> Report:
        report = await self._repo.get(report_id)
        if report is None:
            raise ReportNotFoundError(report_id)
        return report

    @staticmethod
    def _may_view(user: User, report: Report) -> bool:
        if report.user_id == str(user.id):
            return True
        # Managers / Admins see everyone's reports, but not private drafts -
        # a draft only becomes visible to them once it is submitted.
        return (
            user.has_any_role(Role.MANAGER, Role.ADMIN)
            and report.status is not ReportStatus.DRAFT
        )

    @staticmethod
    async def _assert_project_exists(project_id: str) -> None:
        try:
            project = await Project.get(project_id)
        except (InvalidId, ValueError):
            project = None
        if project is None:
            raise ReportValidationError(
                f"Project '{project_id}' does not exist"
            )
