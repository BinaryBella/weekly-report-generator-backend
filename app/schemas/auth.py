"""Pydantic v2 request/response models for authentication and user management."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import Role, UserStatus

if TYPE_CHECKING:
    from app.models.user import User


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class SignupRequest(BaseModel):
    """Payload for ``POST /auth/register``.

    ``role`` is deliberately **not** accepted here: every self-registration
    creates a ``Team Member`` (see :func:`app.core.config.Settings.is_bootstrap_admin`
    for the one exception).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120, examples=["Ada Lovelace"])
    email: EmailStr = Field(examples=["ada@example.com"])
    password: str = Field(min_length=8, max_length=128, examples=["s3cret-passphrase"])


class LoginRequest(BaseModel):
    """JSON alternative to the OAuth2 form used by ``POST /auth/login``.

    The endpoint itself consumes ``OAuth2PasswordRequestForm`` (so the Swagger
    *Authorize* button works); this model documents the equivalent JSON body.
    """

    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    """Payload for ``POST /auth/refresh`` and the optional body of ``/auth/logout``."""

    refresh_token: str = Field(min_length=1)


class RoleUpdateRequest(BaseModel):
    """Payload for ``PATCH /users/{user_id}/role`` (Admin only)."""

    role: Role


class StatusUpdateRequest(BaseModel):
    """Payload for ``PATCH /users/{user_id}/status`` (Manager/Admin only)."""

    status: UserStatus


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class TokenResponse(BaseModel):
    """Access + refresh token pair returned by ``POST /auth/login``."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access-token lifetime in seconds.")


class AccessTokenResponse(BaseModel):
    """Single access token returned by ``POST /auth/refresh``."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access-token lifetime in seconds.")


class UserResponse(BaseModel):
    """Public representation of a :class:`~app.models.user.User` document."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: EmailStr
    role: Role
    status: UserStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_user(cls, user: "User") -> "UserResponse":
        """Build a response model from a persisted user document."""
        return cls(
            id=str(user.id),
            name=user.name,
            email=user.email,
            role=user.role,
            status=user.status,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )


class MessageResponse(BaseModel):
    """Generic ``{"detail": "..."}`` envelope (used by ``/auth/logout``)."""

    detail: str
