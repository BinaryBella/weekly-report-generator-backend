"""Project (a.k.a. category) endpoints with role-based access control.

Access summary:
    * ``GET  /projects/``       - any authenticated user (Team Member or Manager).
    * ``GET  /projects/{id}``   - any authenticated user.
    * ``POST /projects/``       - Manager only.
    * ``PUT  /projects/{id}``   - Manager only.
    * ``PUT  /projects/{id}/members`` - Manager only.
    * ``DELETE /projects/{id}`` - Manager only.

The controller stays thin: it delegates to :class:`~app.services.project_service.ProjectService`
and lets the domain errors raised there be turned into ``404`` / ``400`` responses
by the exception handlers registered in :mod:`app.main`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, require_roles
from app.models.user import Role, User
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectDeleteResponse,
    ProjectMembersUpdateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
)
from app.services.project_service import DeleteMode, ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])

# Write operations are restricted to Manager; the dependency raises 403
# for a Team Member before the handler body runs.
ManagerOnly = Annotated[User, Depends(require_roles(Role.MANAGER))]


def get_project_service() -> ProjectService:
    """FastAPI dependency provider - swappable in tests."""
    return ProjectService()


ProjectSvc = Annotated[ProjectService, Depends(get_project_service)]


@router.get(
    "/",
    response_model=list[ProjectResponse],
    summary="List projects (any authenticated user)",
)
async def list_projects(
    _: CurrentUser,
    service: ProjectSvc,
    active_only: Annotated[
        bool, Query(description="Return only projects with is_active = true")
    ] = False,
) -> list[ProjectResponse]:
    """Return all projects, or only the active ones when ``active_only=true``."""
    projects = await service.list_projects(active_only=active_only)
    return [ProjectResponse.from_project(project) for project in projects]


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Get a single project (any authenticated user)",
)
async def get_project(
    project_id: str,
    _: CurrentUser,
    service: ProjectSvc,
) -> ProjectResponse:
    """Return one project by id.

    Raises:
        HTTPException: ``404`` if the id does not exist.
    """
    project = await service.get_project(project_id)
    return ProjectResponse.from_project(project)


@router.post(
    "/",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project (Manager)",
)
async def create_project(
    payload: ProjectCreateRequest,
    _: ManagerOnly,
    service: ProjectSvc,
) -> ProjectResponse:
    """Create a new project.

    Raises:
        HTTPException: ``400`` if the name is already taken; ``403`` for a
            Team Member; ``422`` if the body fails validation.
    """
    project = await service.create_project(payload)
    return ProjectResponse.from_project(project)


@router.put(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Update a project (Manager)",
)
async def update_project(
    project_id: str,
    payload: ProjectUpdateRequest,
    _: ManagerOnly,
    service: ProjectSvc,
) -> ProjectResponse:
    """Update an existing project. Only the fields sent in the body are changed.

    Raises:
        HTTPException: ``404`` if the id does not exist; ``400`` if the new name
            collides with another project; ``403`` for a Team Member.
    """
    project = await service.update_project(project_id, payload)
    return ProjectResponse.from_project(project)


@router.put(
    "/{project_id}/members",
    response_model=ProjectResponse,
    summary="Assign team members to a project (Manager)",
)
async def assign_project_members(
    project_id: str,
    payload: ProjectMembersUpdateRequest,
    _: ManagerOnly,
    service: ProjectSvc,
) -> ProjectResponse:
    """Replace the full set of team members assigned to a project.

    Raises:
        HTTPException: ``404`` if the project id does not exist; ``400`` if any
            member id doesn't match an existing user; ``403`` for a Team Member.
    """
    project = await service.assign_members(project_id, payload.member_ids)
    return ProjectResponse.from_project(project)


@router.delete(
    "/{project_id}",
    response_model=ProjectDeleteResponse,
    summary="Delete a project (Manager)",
)
async def delete_project(
    project_id: str,
    _: ManagerOnly,
    service: ProjectSvc,
) -> ProjectDeleteResponse:
    """Delete a project.

    Hard-deletes the project when nothing references it; otherwise deactivates
    it (``is_active = false``) and returns the updated record.

    Raises:
        HTTPException: ``404`` if the id does not exist; ``403`` for a Team Member.
    """
    project, mode = await service.delete_project(project_id)
    if mode == DeleteMode.SOFT:
        return ProjectDeleteResponse(
            detail="Project is referenced by reports; deactivated instead of deleted",
            soft_deleted=True,
            project=ProjectResponse.from_project(project) if project else None,
        )
    return ProjectDeleteResponse(detail="Project deleted", soft_deleted=False)
