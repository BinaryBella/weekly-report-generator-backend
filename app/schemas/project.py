"""Pydantic v2 request/response models for project management.

Validation rules (shared by create & update):
    * ``name``        - required on create, non-empty, <= 100 characters.
    * ``description`` - optional, <= 500 characters.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.models.project import Project


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class ProjectCreateRequest(BaseModel):
    """Payload for ``POST /projects``."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100, examples=["Apollo"])
    description: str | None = Field(
        default=None, max_length=500, examples=["Billing platform rebuild"]
    )


class ProjectUpdateRequest(BaseModel):
    """Payload for ``PUT /projects/{id}``.

    Every field is optional; only the fields present in the request body are
    applied to the stored document.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class ProjectMembersUpdateRequest(BaseModel):
    """Payload for ``PUT /projects/{id}/members`` - replaces the full member list."""

    member_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class ProjectResponse(BaseModel):
    """Public representation of a :class:`~app.models.project.Project` document."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    is_active: bool
    member_ids: list[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_project(cls, project: "Project") -> "ProjectResponse":
        """Build a response model from a persisted project document."""
        return cls(
            id=str(project.id),
            name=project.name,
            description=project.description,
            is_active=project.is_active,
            member_ids=project.member_ids,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )


class ProjectDeleteResponse(BaseModel):
    """Result envelope for ``DELETE /projects/{id}``.

    ``soft_deleted`` is ``True`` when the project was kept but deactivated
    (because reports reference it) and ``False`` when the row was removed
    outright. ``project`` is populated only for a soft delete.
    """

    detail: str
    soft_deleted: bool
    project: ProjectResponse | None = None
