"""Data-access layer for the ``projects`` collection.

This module is the only place that talks to Beanie/Mongo for projects; the
service layer depends on it through the small interface below so the business
rules stay storage-agnostic and unit-testable.
"""

from __future__ import annotations

from bson.errors import InvalidId

from app.models.project import Project


class ProjectRepository:
    """Thin CRUD wrapper around the :class:`~app.models.project.Project` document."""

    async def list(self, *, active_only: bool = False) -> list[Project]:
        """Return every project, oldest first, optionally only the active ones."""
        query = (
            Project.find(Project.is_active == True)  # noqa: E712 - Beanie needs ==
            if active_only
            else Project.find()
        )
        return await query.sort("+created_at").to_list()

    async def get(self, project_id: str) -> Project | None:
        """Fetch a project by id. Returns ``None`` for unknown or malformed ids."""
        try:
            return await Project.get(project_id)
        except (InvalidId, ValueError):
            return None

    async def get_by_name(self, name: str) -> Project | None:
        """Fetch a project by its exact (case-sensitive) name."""
        return await Project.find_one(Project.name == name)

    async def add(self, project: Project) -> Project:
        """Persist a new project document."""
        await project.insert()
        return project

    async def save(self, project: Project) -> Project:
        """Persist changes to an existing project document."""
        await project.save()
        return project

    async def delete(self, project: Project) -> None:
        """Remove a project document permanently."""
        await project.delete()
