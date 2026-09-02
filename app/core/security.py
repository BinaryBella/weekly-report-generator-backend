"""Password hashing and JWT creation / verification helpers.

This module is intentionally free of FastAPI imports so it can be unit-tested in
isolation. Callers (dependencies / routers) are responsible for translating a
:class:`TokenError` into the appropriate ``HTTPException``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Final

import jwt
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict

from app.core.config import settings

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
pwd_context: Final[CryptContext] = CryptContext(schemes=["bcrypt"], deprecated="auto")

# bcrypt only consumes the first 72 bytes of a password; longer inputs raise on
# some backends, so we truncate defensively and consistently.
_BCRYPT_MAX_BYTES: Final[int] = 72


def _prepare(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(raw_password: str) -> str:
    """Return a salted bcrypt hash for *raw_password*."""
    return pwd_context.hash(_prepare(raw_password))


def verify_password(raw_password: str, hashed_password: str) -> bool:
    """Return ``True`` when *raw_password* matches *hashed_password*."""
    try:
        return pwd_context.verify(_prepare(raw_password), hashed_password)
    except (ValueError, TypeError):
        # Malformed hash in the database - treat as a non-match rather than 500.
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
class TokenType(str, Enum):
    """Discriminator stored in the ``type`` claim of every issued token."""

    ACCESS = "access"
    REFRESH = "refresh"


class TokenError(Exception):
    """Raised when a token cannot be decoded, is expired, or has the wrong type."""


class TokenPayload(BaseModel):
    """Validated representation of a decoded JWT body."""

    model_config = ConfigDict(extra="ignore")

    sub: str
    type: TokenType
    jti: str
    iat: datetime
    exp: datetime


class IssuedToken(BaseModel):
    """A freshly minted token plus the metadata needed to revoke it later."""

    token: str
    jti: str
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def create_token(
    subject: str,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> IssuedToken:
    """Create a signed JWT for *subject*.

    Args:
        subject: Value placed in the ``sub`` claim (the user id).
        token_type: Access or refresh - stored in the ``type`` claim.
        expires_delta: Lifetime of the token relative to now.
        extra_claims: Optional additional claims to embed.

    Returns:
        An :class:`IssuedToken` bundling the encoded string, its ``jti`` and its
        absolute expiry (used to size the revocation TTL entry).
    """
    issued_at = _now()
    expires_at = issued_at + expires_delta
    jti = uuid.uuid4().hex
    claims: dict[str, Any] = {
        "sub": subject,
        "type": token_type.value,
        "jti": jti,
        "iat": issued_at,
        "exp": expires_at,
    }
    if extra_claims:
        claims.update(extra_claims)

    encoded = jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return IssuedToken(token=encoded, jti=jti, expires_at=expires_at)


def create_access_token(user_id: str) -> IssuedToken:
    """Mint a short-lived access token for *user_id*."""
    return create_token(
        subject=user_id,
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: str) -> IssuedToken:
    """Mint a long-lived refresh token for *user_id*."""
    return create_token(
        subject=user_id,
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str, expected_type: TokenType) -> TokenPayload:
    """Decode and validate *token*.

    Args:
        token: The raw JWT string.
        expected_type: The token ``type`` claim the caller requires.

    Returns:
        The validated :class:`TokenPayload`.

    Raises:
        TokenError: If the signature is invalid, the token has expired, required
            claims are missing, or the ``type`` claim does not match
            *expected_type*.
    """
    try:
        raw = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Could not validate credentials") from exc

    try:
        payload = TokenPayload.model_validate(raw)
    except ValueError as exc:
        raise TokenError("Malformed token payload") from exc

    if payload.type is not expected_type:
        raise TokenError(f"Expected a {expected_type.value} token")

    return payload
