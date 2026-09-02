"""Beanie document models for the ``users`` and ``token_denylist`` collections."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

import pymongo
from beanie import Document, Indexed
from pydantic import EmailStr, Field
from pymongo import IndexModel


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class Role(str, Enum):
    """Application roles, ordered from least to most privileged."""

    TEAM_MEMBER = "Team Member"
    MANAGER = "Manager"
    ADMIN = "Admin"


class UserStatus(str, Enum):
    """Account lifecycle state. Disabled accounts cannot authenticate."""

    ACTIVE = "active"
    DISABLED = "disabled"


class User(Document):
    """A registered user of the Weekly Report Generator.

    Passwords are never stored in the clear - only ``hashed_password`` (bcrypt)
    is persisted.
    """

    name: str = Field(min_length=1, max_length=120)
    email: Annotated[EmailStr, Indexed(unique=True)]
    hashed_password: str
    role: Role = Role.TEAM_MEMBER
    status: UserStatus = UserStatus.ACTIVE
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "users"

    def touch(self) -> None:
        """Bump :attr:`updated_at` to the current UTC time."""
        self.updated_at = _utcnow()

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE

    def has_any_role(self, *roles: Role) -> bool:
        return self.role in roles


class RevokedToken(Document):
    """A token ``jti`` that has been explicitly invalidated via ``/auth/logout``.

    A TTL index on :attr:`expires_at` lets MongoDB purge entries automatically
    once the underlying token would have expired on its own.
    """

    jti: Annotated[str, Indexed(unique=True)]
    user_id: str
    token_type: str
    expires_at: datetime
    revoked_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "token_denylist"
        indexes = [
            IndexModel(
                [("expires_at", pymongo.ASCENDING)],
                name="ttl_expires_at",
                expireAfterSeconds=0,
            ),
        ]


__beanie_models__ = [User, RevokedToken]
