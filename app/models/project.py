"""Beanie document model for the ``projects`` collection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from beanie import Document, Indexed
from pydantic import Field


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class Project(Document):
    """A project / category that weekly report entries are filed under.

    Report entities (introduced by a later requirement) reference a project by
    its ``id``. Because of that forward relationship, removing a project that is
    already referenced by reports is a *soft* delete (``is_active = False``);
    an unreferenced project may be hard-deleted.
    """

    name: Annotated[str, Indexed(unique=True), Field(min_length=1, max_length=100)]
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    member_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "projects"

    def touch(self) -> None:
        """Bump :attr:`updated_at` to the current UTC time."""
        self.updated_at = _utcnow()


__beanie_models__ = [Project]
