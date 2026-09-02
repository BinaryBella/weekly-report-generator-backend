"""Business rules for project management.

The service raises framework-agnostic domain errors (:class:`ProjectNotFoundError`,
:class:`DuplicateProjectNameError`); the API layer registers exception handlers
that translate those into ``404`` / ``400`` responses.
"""

from __future__ import annotations

from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from app.models.project import Project
from app.models.user import User
from app.repositories.project_repository import ProjectRepository
from app.repositories.report_repository import ReportRepository
from app.schemas.project import ProjectCreateRequest, ProjectUpdateRequest


class ProjectServiceError(Exception):
    """Base class for project domain errors."""


class ProjectNotFoundError(ProjectServiceError):
    """Raised when a project id does not resolve to a stored document."""

    def __init__(self, project_id: str) -> None:
        super().__init__(f"Project '{project_id}' was not found")
        self.project_id = project_id


class DuplicateProjectNameError(ProjectServiceError):
    """Raised when a create/update would collide with an existing project name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"A project named '{name}' already exists")
        self.name = name


class InvalidMemberIdsError(ProjectServiceError):
    """Raised when one or more member ids don't resolve to an existing user."""

    def __init__(self, member_ids: list[str]) -> None:
        super().__init__(f"Unknown user id(s): {', '.join(member_ids)}")
        self.member_ids = member_ids


class DeleteMode:
    """Outcome markers returned by :meth:`ProjectService.delete_project`."""

    SOFT = "soft"
    HARD = "hard"


class ProjectService:
    """Coordinates validation, uniqueness and delete semantics for projects."""

    def __init__(
        self,
        repository: ProjectRepository | None = None,
        report_repository: ReportRepository | None = None,
    ) -> None:
        self._repo = repository or ProjectRepository()
        self._report_repo = report_repository or ReportRepository()

    async def list_projects(self, *, active_only: bool = False) -> list[Project]:
        """Return all projects, or only active ones when *active_only* is set."""
        return await self._repo.list(active_only=active_only)

    async def get_project(self, project_id: str) -> Project:
        """Return a single project.

        Raises:
            ProjectNotFoundError: if no project has that id.
        """
        project = await self._repo.get(project_id)
        if project is None:
            raise ProjectNotFoundError(project_id)
        return project

    async def create_project(self, data: ProjectCreateRequest) -> Project:
        """Create a new project.

        Raises:
            DuplicateProjectNameError: if the name is already taken.
        """
        if await self._repo.get_by_name(data.name) is not None:
            raise DuplicateProjectNameError(data.name)

        project = Project(name=data.name, description=data.description)
        try:
            return await self._repo.add(project)
        except DuplicateKeyError as exc:  # race between the check above and insert
            raise DuplicateProjectNameError(data.name) from exc

    async def update_project(self, project_id: str, data: ProjectUpdateRequest) -> Project:
        """Apply a partial update to an existing project.

        Only the fields present in *data* are changed.

        Raises:
            ProjectNotFoundError: if the id is unknown.
            DuplicateProjectNameError: if the new name collides with another project.
        """
        project = await self.get_project(project_id)
        changes = data.model_dump(exclude_unset=True)

        new_name = changes.get("name")
        if new_name is not None and new_name != project.name:
            clash = await self._repo.get_by_name(new_name)
            if clash is not None and clash.id != project.id:
                raise DuplicateProjectNameError(new_name)

        if not changes:
            return project

        for field, value in changes.items():
            setattr(project, field, value)
        project.touch()

        try:
            return await self._repo.save(project)
        except DuplicateKeyError as exc:
            raise DuplicateProjectNameError(new_name or project.name) from exc

    async def assign_members(self, project_id: str, member_ids: list[str]) -> Project:
        """Replace a project's assigned team members.

        The full member list is replaced (not merged) with *member_ids*,
        de-duplicated while preserving order.

        Raises:
            ProjectNotFoundError: if the project id is unknown.
            InvalidMemberIdsError: if any member id doesn't match an existing user.
        """
        project = await self.get_project(project_id)

        unique_ids = list(dict.fromkeys(member_ids))
        invalid = [uid for uid in unique_ids if not await self._user_exists(uid)]
        if invalid:
            raise InvalidMemberIdsError(invalid)

        project.member_ids = unique_ids
        project.touch()
        return await self._repo.save(project)

    @staticmethod
    async def _user_exists(user_id: str) -> bool:
        try:
            return await User.get(user_id) is not None
        except (InvalidId, ValueError):
            return False

    async def delete_project(self, project_id: str) -> tuple[Project | None, str]:
        """Delete a project.

        Hard-deletes the document when nothing references it; otherwise performs
        a soft delete (``is_active = False``) and returns the updated project.

        Returns:
            ``(project, DeleteMode.SOFT)`` for a soft delete, where *project* is
            the deactivated document, or ``(None, DeleteMode.HARD)`` when the
            row was removed.

        Raises:
            ProjectNotFoundError: if the id is unknown.
        """
        project = await self.get_project(project_id)

        if await self._has_linked_reports(project):
            if project.is_active:
                project.is_active = False
                project.touch()
                await self._repo.save(project)
            return project, DeleteMode.SOFT

        await self._repo.delete(project)
        return None, DeleteMode.HARD

    async def _has_linked_reports(self, project: Project) -> bool:
        """Whether any report references *project* (soft-delete when it does)."""
        return await self._report_repo.exists_for_project(str(project.id))
