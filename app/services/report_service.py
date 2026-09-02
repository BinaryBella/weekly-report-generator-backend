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

<<<<<<< Updated upstream
=======
from datetime import date, datetime, timezone

from beanie.operators import In
>>>>>>> Stashed changes
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
<<<<<<< Updated upstream
=======
    ReportVersion,
    ReviewComment,
    TaskStatus,
    can_transition,
>>>>>>> Stashed changes
)
from app.models.user import Role, User, UserStatus
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


<<<<<<< Updated upstream
=======
class ReportTransitionError(ReportServiceError):
    """Raised when a review-workflow action is illegal from the current status."""

    def __init__(self, current: ReportStatus, action: str) -> None:
        super().__init__(
            f"Cannot {action} a report in '{current.value}' status"
        )
        self.current = current
        self.action = action


class MemberNotFoundError(ReportServiceError):
    """Raised when a team-member profile is requested for an unknown user id."""

    def __init__(self, user_id: str) -> None:
        super().__init__(f"Team member '{user_id}' was not found")
        self.user_id = user_id


>>>>>>> Stashed changes
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

<<<<<<< Updated upstream
=======
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
        report.reviewed_by_id = str(current_user.id)
        report.reviewed_by_name = current_user.name
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
        report.reviewed_by_id = str(current_user.id)
        report.reviewed_by_name = current_user.name
        report.touch()
        return await self._repo.save(report)

>>>>>>> Stashed changes
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

<<<<<<< Updated upstream
=======
    async def list_all_reports(
        self,
        *,
        status: ReportStatus | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        week_start_date: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Report], int]:
        """Return one page of every team member's reports (manager dashboard).

        Access is restricted to Manager / Admin at the route; this method assumes
        the caller has already been authorised. Private drafts are never
        included - a report only reaches the dashboard once it is submitted.

        Filters (all optional, AND-combined): ``status``, ``user_id`` (a single
        team member), ``project_id`` (a project / category), ``week_start_date``
        (an exact selected week) and ``date_from`` / ``date_to`` (a range over the
        reports' week-start dates).
        """
        if status is ReportStatus.DRAFT:
            return [], 0
        skip = (page - 1) * page_size
        filters = dict(
            status=status,
            user_id=user_id,
            project_id=project_id,
            week_start_date=week_start_date,
            date_from=date_from,
            date_to=date_to,
        )
        items = await self._repo.list_all(skip=skip, limit=page_size, **filters)
        total = await self._repo.count_all(**filters)
        return items, total

    # -- Team dashboard (Section 4) ---------------------------------------
    async def team_week_reports(
        self, *, week_start_date: date, project_id: str | None = None
    ) -> list[tuple[User, Report | None]]:
        """Line up every team member against their report for *week_start_date*.

        The roster is the members of *project_id* when given, otherwise every
        active Team Member. Each entry pairs the user with their report for that
        week, or ``None`` when they have not started one ("not yet started").

        Callers must treat a paired report that is still ``DRAFT`` as
        status-only: its content stays private to the author.
        """
        roster = await self._resolve_roster(project_id)
        roster_ids = [str(user.id) for user in roster]

        reports = await self._repo.list_for_week(
            week_start_date, user_ids=roster_ids or None
        )
        by_user: dict[str, Report] = {}
        for report in reports:
            # If a member somehow has two reports for one week, surface the one
            # furthest along the workflow / most recently touched.
            existing = by_user.get(report.user_id)
            if existing is None or report.updated_at > existing.updated_at:
                by_user[report.user_id] = report

        return [(user, by_user.get(str(user.id))) for user in roster]

    async def _resolve_roster(self, project_id: str | None) -> list[User]:
        """Return the ordered list of team members the dashboard covers."""
        if project_id is None:
            return (
                await User.find(
                    User.role == Role.TEAM_MEMBER,
                    User.status == UserStatus.ACTIVE,
                )
                .sort("+name")
                .to_list()
            )

        project = await self._get_project_or_error(project_id)
        members: list[User] = []
        seen: set[str] = set()
        for member_id in project.member_ids:
            if member_id in seen:
                continue
            seen.add(member_id)
            try:
                user = await User.get(member_id)
            except (InvalidId, ValueError):
                user = None
            if user is not None and user.status is UserStatus.ACTIVE:
                members.append(user)
        members.sort(key=lambda u: u.name.lower())
        return members

    # -- Insights dashboard (Section 6) ----------------------------------
    async def dashboard_summary(
        self, *, week_start_date: date, project_id: str | None = None
    ) -> dict:
        """The four headline metrics for the selected week.

        * total reports submitted this week
        * submission compliance (submitted / pending / on-time / late)
        * reports currently in NEEDS_CORRECTION
        * open blockers across the team
        """
        pairs = await self.team_week_reports(
            week_start_date=week_start_date, project_id=project_id
        )
        total_members = len(pairs)
        submitted = on_time = late = pending = 0
        for _user, report in pairs:
            if report is None or report.status is ReportStatus.DRAFT:
                pending += 1
                continue
            submitted += 1
            if (
                report.submitted_at is not None
                and report.submitted_at.date() > report.week_end_date
            ):
                late += 1
            else:
                on_time += 1

        week_reports = await self._repo.fetch(
            project_id=project_id, week_start_date=week_start_date
        )
        needs_correction = sum(
            1 for r in week_reports if r.status is ReportStatus.NEEDS_CORRECTION
        )
        open_blockers = sum(len(r.blockers) for r in week_reports)
        open_key_issues = sum(
            1
            for r in week_reports
            for b in r.blockers
            if b.is_key_issue
        )

        def _rate(part: int) -> float:
            return round(part / total_members, 4) if total_members else 0.0

        return {
            "week_start_date": week_start_date,
            "project_id": project_id,
            "total_members": total_members,
            "total_submitted_this_week": len(week_reports),
            "submission_compliance": {
                "submitted": submitted,
                "pending": pending,
                "on_time": on_time,
                "late": late,
                "compliance_rate": _rate(submitted),
                "on_time_rate": _rate(on_time),
            },
            "needs_correction_count": needs_correction,
            "open_blockers": open_blockers,
            "open_key_issues": open_key_issues,
        }

    async def tasks_completed_trend(
        self,
        *,
        weeks: int = 8,
        project_id: str | None = None,
        group_by: str = "team",
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict:
        """Completed-tasks trend, grouped by report week, team-wide or per person."""
        reports = await self._repo.fetch(
            project_id=project_id, date_from=date_from, date_to=date_to
        )
        recent_weeks = sorted({r.week_start_date for r in reports})[-weeks:]
        keep = set(recent_weeks)
        reports = [r for r in reports if r.week_start_date in keep]

        # bucket -> week -> [reports]
        buckets: dict[str, dict[date, list[Report]]] = {}
        labels: dict[str, str] = {}
        if group_by == "user":
            names = await self._names_for({r.user_id for r in reports})
            for r in reports:
                labels[r.user_id] = names.get(r.user_id, r.user_id)
                buckets.setdefault(r.user_id, {}).setdefault(
                    r.week_start_date, []
                ).append(r)
        else:
            labels["team"] = "Team-wide"
            for r in reports:
                buckets.setdefault("team", {}).setdefault(
                    r.week_start_date, []
                ).append(r)

        series = []
        for key, by_week in sorted(buckets.items(), key=lambda kv: labels[kv[0]]):
            points = []
            for week in recent_weeks:
                wk_reports = by_week.get(week, [])
                completed = sum(
                    1
                    for rep in wk_reports
                    for t in rep.tasks_completed
                    if t.status is TaskStatus.COMPLETED
                )
                total = sum(len(rep.tasks_completed) for rep in wk_reports)
                points.append(
                    {
                        "week_start_date": week,
                        "reports": len(wk_reports),
                        "completed_tasks": completed,
                        "total_tasks": total,
                    }
                )
            series.append({"key": key, "label": labels[key], "points": points})

        return {"group_by": group_by, "project_id": project_id, "series": series}

    async def status_by_member(
        self,
        *,
        week_start_date: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        project_id: str | None = None,
    ) -> dict:
        """Per-member counts of reports by status (submission / approval chart)."""
        roster = await self._resolve_roster(project_id)
        roster_ids = {str(u.id) for u in roster}
        reports = await self._repo.fetch(
            project_id=project_id,
            week_start_date=week_start_date,
            date_from=date_from,
            date_to=date_to,
            include_drafts=True,
        )

        tally: dict[str, dict[str, int]] = {
            str(u.id): {
                "not_started": 0,
                "draft": 0,
                "submitted": 0,
                "needs_correction": 0,
                "approved": 0,
            }
            for u in roster
        }
        seen_weeks: dict[str, set] = {str(u.id): set() for u in roster}
        for r in reports:
            if r.user_id not in tally:
                continue
            seen_weeks[r.user_id].add(r.week_start_date)
            tally[r.user_id][r.status.value.lower()] += 1

        # "not started" only makes sense for a single selected week.
        if week_start_date is not None:
            for uid in roster_ids:
                if week_start_date not in seen_weeks[uid]:
                    tally[uid]["not_started"] = 1

        rows = [
            {"user_id": str(u.id), "user_name": u.name, **tally[str(u.id)]}
            for u in roster
        ]
        return {
            "week_start_date": week_start_date,
            "date_from": date_from,
            "date_to": date_to,
            "project_id": project_id,
            "rows": rows,
        }

    async def workload_by_project(
        self,
        *,
        week_start_date: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict:
        """Task / hours distribution across projects for the selected period."""
        reports = await self._repo.fetch(
            week_start_date=week_start_date, date_from=date_from, date_to=date_to
        )
        agg: dict[str, dict] = {}
        for r in reports:
            row = agg.setdefault(
                r.project_id,
                {"reports": 0, "tasks": 0, "planned_hours": 0.0, "spent_hours": 0.0},
            )
            row["reports"] += 1
            row["tasks"] += len(r.tasks_completed)
            row["planned_hours"] += sum(t.time_planned_hours for t in r.tasks_completed)
            row["spent_hours"] += sum(t.time_spent_hours for t in r.tasks_completed)

        names = await self._project_names(set(agg))
        rows = [
            {
                "project_id": pid,
                "project_name": names.get(pid, "(unknown project)"),
                "reports": data["reports"],
                "tasks": data["tasks"],
                "planned_hours": round(data["planned_hours"], 2),
                "spent_hours": round(data["spent_hours"], 2),
            }
            for pid, data in agg.items()
        ]
        rows.sort(key=lambda row: row["spent_hours"], reverse=True)
        return {
            "week_start_date": week_start_date,
            "date_from": date_from,
            "date_to": date_to,
            "rows": rows,
        }

    async def hours_by_type(
        self,
        *,
        week_start_date: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        project_id: str | None = None,
    ) -> dict:
        """Team-wide sum of the hours-worked breakdown across activity types."""
        reports = await self._repo.fetch(
            project_id=project_id,
            week_start_date=week_start_date,
            date_from=date_from,
            date_to=date_to,
        )
        totals = {
            "development": 0.0,
            "testing": 0.0,
            "meetings": 0.0,
            "documentation": 0.0,
            "other": 0.0,
        }
        counted = 0
        for r in reports:
            if r.hours_worked_breakdown is None:
                continue
            counted += 1
            for key in totals:
                totals[key] += getattr(r.hours_worked_breakdown, key)

        totals = {k: round(v, 2) for k, v in totals.items()}
        return {
            "week_start_date": week_start_date,
            "date_from": date_from,
            "date_to": date_to,
            "project_id": project_id,
            "reports_counted": counted,
            "total": round(sum(totals.values()), 2),
            **totals,
        }

    async def activity_feed(
        self, *, limit: int = 20, project_id: str | None = None
    ) -> dict:
        """Recent submissions and review actions across the team, newest first."""
        reports = await self._repo.recent(limit=max(limit * 4, 50), project_id=project_id)
        names = await self._names_for({r.user_id for r in reports})

        events: list[dict] = []
        for r in reports:
            base = {
                "report_id": str(r.id),
                "week_start_date": r.week_start_date,
                "project_id": r.project_id,
                "author_id": r.user_id,
                "author_name": names.get(r.user_id, r.user_id),
            }
            if r.submitted_at is not None:
                events.append(
                    {
                        **base,
                        "type": "SUBMITTED",
                        "at": r.submitted_at,
                        "actor_id": r.user_id,
                        "actor_name": base["author_name"],
                        "comment": None,
                    }
                )
            for rc in r.review_comments:
                events.append(
                    {
                        **base,
                        "type": "CHANGES_REQUESTED",
                        "at": rc.created_at,
                        "actor_id": rc.manager_id,
                        "actor_name": rc.manager_name,
                        "comment": rc.comment,
                    }
                )
            if r.status is ReportStatus.APPROVED and r.reviewed_at is not None:
                events.append(
                    {
                        **base,
                        "type": "APPROVED",
                        "at": r.reviewed_at,
                        "actor_id": r.reviewed_by_id,
                        "actor_name": r.reviewed_by_name,
                        "comment": None,
                    }
                )

        events.sort(key=lambda e: e["at"], reverse=True)
        return {"events": events[:limit]}

    @staticmethod
    async def _names_for(user_ids: set[str]) -> dict[str, str]:
        """Resolve a set of user ids to display names (best effort)."""
        names: dict[str, str] = {}
        for uid in user_ids:
            try:
                user = await User.get(uid)
            except (InvalidId, ValueError):
                user = None
            if user is not None:
                names[uid] = user.name
        return names

    @staticmethod
    async def _project_names(project_ids: set[str]) -> dict[str, str]:
        names: dict[str, str] = {}
        for pid in project_ids:
            try:
                project = await Project.get(pid)
            except (InvalidId, ValueError):
                project = None
            if project is not None:
                names[pid] = project.name
        return names

    # -- Team member profile (manager view) --------------------------------
    async def member_profile(self, user_id: str, *, limit: int = 5) -> dict:
        """Identity + basic stats + recent history for one team member.

        Powers "click a team member, see their full report history and basic
        stats". Only reports that have left ``DRAFT`` are counted or listed - a
        draft stays private to its author even here.

        Raises:
            MemberNotFoundError: if *user_id* does not resolve to a user.
        """
        try:
            user = await User.get(user_id)
        except (InvalidId, ValueError):
            user = None
        if user is None:
            raise MemberNotFoundError(user_id)

        reports = await self._repo.fetch(user_id=user_id)
        submitted = sum(1 for r in reports if r.status is ReportStatus.SUBMITTED)
        needs_correction = sum(
            1 for r in reports if r.status is ReportStatus.NEEDS_CORRECTION
        )
        approved = sum(1 for r in reports if r.status is ReportStatus.APPROVED)
        reviewed = approved + needs_correction
        total_tasks_completed = sum(
            1
            for r in reports
            for t in r.tasks_completed
            if t.status is TaskStatus.COMPLETED
        )
        total_hours = sum(
            (
                h.development + h.testing + h.meetings + h.documentation + h.other
                for r in reports
                if (h := r.hours_worked_breakdown) is not None
            ),
            start=0.0,
        )
        submitted_ats = [r.submitted_at for r in reports if r.submitted_at is not None]

        recent = sorted(
            reports, key=lambda r: (r.week_start_date, r.updated_at), reverse=True
        )[:limit]

        return {
            "user": user,
            "stats": {
                "total_reports": len(reports),
                "submitted_count": submitted,
                "needs_correction_count": needs_correction,
                "approved_count": approved,
                "approval_rate": round(approved / reviewed, 4) if reviewed else 0.0,
                "total_tasks_completed": total_tasks_completed,
                "total_hours_logged": round(total_hours, 2),
                "last_submitted_at": max(submitted_ats) if submitted_ats else None,
            },
            "recent_reports": recent,
        }

>>>>>>> Stashed changes
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

    @classmethod
    async def _assert_project_exists(cls, project_id: str) -> None:
        await cls._get_project_or_error(project_id)

    @staticmethod
    async def _get_project_or_error(project_id: str) -> Project:
        try:
            project = await Project.get(project_id)
        except (InvalidId, ValueError):
            project = None
        if project is None:
            raise ReportValidationError(
                f"Project '{project_id}' does not exist"
            )
        return project
