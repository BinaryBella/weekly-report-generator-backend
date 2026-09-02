"""Data-access layer for the ``reports`` collection.

This module is the only place that talks to Beanie/Mongo for reports; the service
layer depends on it through the small interface below so the business rules stay
storage-agnostic and unit-testable.
"""

from __future__ import annotations

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
        """Return one page of a user's reports, most recently updated first."""
        return (
            await self._user_query(user_id, status)
            .sort("-updated_at")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

    async def count_for_user(
        self, user_id: str, *, status: ReportStatus | None = None
    ) -> int:
        """Count a user's reports, optionally narrowed to a single status."""
        return await self._user_query(user_id, status).count()

    async def exists_for_project(self, project_id: str) -> bool:
        """Whether any report references *project_id* (used to guard project deletes)."""
        return await Report.find_one(Report.project_id == project_id) is not None

    @staticmethod
    def _user_query(user_id: str, status: ReportStatus | None):
        if status is None:
            return Report.find(Report.user_id == user_id)
        return Report.find(Report.user_id == user_id, Report.status == status)
