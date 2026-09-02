"""Data-access layer for the ``reports`` collection.

This module is the only place that talks to Beanie/Mongo for reports; the service
layer depends on it through the small interface below so the business rules stay
storage-agnostic and unit-testable.
"""

from __future__ import annotations

from datetime import date

from beanie.operators import In
from bson.errors import InvalidId

from app.models.report import Report, ReportStatus


class ReportRepository:
    """Thin CRUD wrapper around the :class:`~app.models.report.Report` document."""

    async def add(self, report: Report) -> Report:
        """Persist a new report document (parent + embedded children, atomically)."""
        await report.insert()
        return report

    async def save(self, report: Report) -> Report:
        """Persist changes to an existing report document."""
        await report.save()
        return report

    async def get(self, report_id: str) -> Report | None:
        """Fetch a report by id. Returns ``None`` for unknown or malformed ids."""
        try:
            return await Report.get(report_id)
        except (InvalidId, ValueError):
            return None

    async def list_for_user(
        self,
        user_id: str,
        *,
        status: ReportStatus | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> list[Report]:
        """Return one page of a user's report history, most recent week first.

        Ordered by ``week_start_date`` (then last update) so the list reads as a
        week-by-week history, as required by the personal report page.
        """
        return (
            await self._user_query(user_id, status)
            .sort("-week_start_date", "-updated_at")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

    async def count_for_user(
        self, user_id: str, *, status: ReportStatus | None = None
    ) -> int:
        """Count a user's reports, optionally narrowed to a single status."""
        return await self._user_query(user_id, status).count()

<<<<<<< Updated upstream
=======
    async def list_all(
        self,
        *,
        status: ReportStatus | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        week_start_date: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> list[Report]:
        """Return one page of reports across every user (manager dashboard)."""
        return (
            await self._dashboard_query(
                status, user_id, project_id, week_start_date, date_from, date_to
            )
            .sort("-week_start_date", "-updated_at")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

    async def count_all(
        self,
        *,
        status: ReportStatus | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        week_start_date: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> int:
        """Count reports across every user, honouring the same optional filters."""
        return await self._dashboard_query(
            status, user_id, project_id, week_start_date, date_from, date_to
        ).count()

    async def fetch(
        self,
        *,
        status: ReportStatus | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        week_start_date: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        include_drafts: bool = False,
    ) -> list[Report]:
        """Return every report matching the filters (no pagination).

        Used by the insights dashboard for aggregation. ``include_drafts`` is
        only set for *counting* purposes (compliance / status tracking); callers
        never expose draft content.
        """
        return (
            await self._dashboard_query(
                status,
                user_id,
                project_id,
                week_start_date,
                date_from,
                date_to,
                include_drafts,
            )
            .sort("-week_start_date", "-updated_at")
            .to_list()
        )

    async def recent(
        self, *, limit: int = 50, project_id: str | None = None
    ) -> list[Report]:
        """Return the most recently updated non-draft reports (activity feed)."""
        conditions = [Report.status != ReportStatus.DRAFT]
        if project_id is not None:
            conditions.append(Report.project_id == project_id)
        return (
            await Report.find(*conditions).sort("-updated_at").limit(limit).to_list()
        )

    async def list_for_week(
        self,
        week_start_date: date,
        *,
        user_ids: list[str] | None = None,
    ) -> list[Report]:
        """Return every report (any status) covering *week_start_date*.

        Used by the team dashboard to line reports up against the roster; unlike
        the review dashboard this deliberately includes drafts, because the
        dashboard tracks *status* per member (a draft is still "in progress").
        Callers must not expose draft *content*.
        """
        conditions = [Report.week_start_date == week_start_date]
        if user_ids is not None:
            conditions.append(In(Report.user_id, user_ids))
        return await Report.find(*conditions).to_list()

>>>>>>> Stashed changes
    async def list_all(
        self,
        *,
        status: ReportStatus | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> list[Report]:
        """Return one page of reports across every user (manager dashboard)."""
        return (
            await self._dashboard_query(status, user_id, project_id)
            .sort("-updated_at")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

    async def count_all(
        self,
        *,
        status: ReportStatus | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> int:
        """Count reports across every user, honouring the same optional filters."""
        return await self._dashboard_query(status, user_id, project_id).count()

    async def exists_for_project(self, project_id: str) -> bool:
        """Whether any report references *project_id* (used to guard project deletes)."""
        return await Report.find_one(Report.project_id == project_id) is not None

    @staticmethod
    def _user_query(user_id: str, status: ReportStatus | None):
        if status is None:
            return Report.find(Report.user_id == user_id)
        return Report.find(Report.user_id == user_id, Report.status == status)
<<<<<<< Updated upstream
=======

    @staticmethod
    def _dashboard_query(
        status: ReportStatus | None,
        user_id: str | None,
        project_id: str | None,
        week_start_date: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        include_drafts: bool = False,
    ):
        # Drafts stay private to their author - the dashboard only ever shows
        # reports that have entered the review workflow, unless a caller opts in
        # for counting purposes only (``include_drafts``).
        if status is not None:
            conditions = [Report.status == status]
        elif include_drafts:
            conditions = []
        else:
            conditions = [Report.status != ReportStatus.DRAFT]
        if user_id is not None:
            conditions.append(Report.user_id == user_id)
        if project_id is not None:
            conditions.append(Report.project_id == project_id)
        if week_start_date is not None:
            conditions.append(Report.week_start_date == week_start_date)
        if date_from is not None:
            conditions.append(Report.week_start_date >= date_from)
        if date_to is not None:
            conditions.append(Report.week_start_date <= date_to)
        return Report.find(*conditions)
>>>>>>> Stashed changes

    @staticmethod
    def _dashboard_query(
        status: ReportStatus | None,
        user_id: str | None,
        project_id: str | None,
    ):
        # Drafts stay private to their author - the dashboard only ever shows
        # reports that have entered the review workflow.
        if status is None:
            conditions = [Report.status != ReportStatus.DRAFT]
        else:
            conditions = [Report.status == status]
        if user_id is not None:
            conditions.append(Report.user_id == user_id)
        if project_id is not None:
            conditions.append(Report.project_id == project_id)
        return Report.find(*conditions)
