"""Reusable FastAPI dependencies for authentication and role-based access control."""

from __future__ import annotations

from typing import Annotated, Callable

from bson.errors import InvalidId
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.config import settings
from app.core.security import TokenError, TokenPayload, TokenType, decode_token
from app.models.user import RevokedToken, Role, User, UserStatus

# ``tokenUrl`` is only used by Swagger UI to build the "Authorize" form.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login")

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_access_token_payload(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> TokenPayload:
    """Decode the bearer token, enforce ``type == access`` and check the denylist.

    Raises:
        HTTPException: ``401`` if the token is missing, malformed, expired, of the
            wrong type, or has been revoked via ``/auth/logout``.
    """
    try:
        payload = decode_token(token, expected_type=TokenType.ACCESS)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if await RevokedToken.find_one(RevokedToken.jti == payload.jti) is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


async def get_current_user(
    payload: Annotated[TokenPayload, Depends(get_access_token_payload)],
) -> User:
    """Resolve the authenticated :class:`~app.models.user.User`.

    Raises:
        HTTPException: ``401`` if the subject no longer maps to a user;
            ``403`` if the account has been disabled.
    """
    try:
        user = await User.get(payload.sub)
    except (InvalidId, ValueError) as exc:
        raise _CREDENTIALS_EXC from exc
    if user is None:
        raise _CREDENTIALS_EXC

    if user.status is UserStatus.DISABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    return user


# Alias kept for readability at call sites that want to stress "active".
get_current_active_user = get_current_user

CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role) -> Callable[[User], User]:
    """Build a dependency that permits only users holding one of *roles*.

    Example:
        ``Depends(require_roles(Role.MANAGER, Role.ADMIN))``

    Raises:
        HTTPException: ``403`` if the current user's role is not in *roles*.
    """
    allowed = set(roles)

    async def _dependency(current_user: CurrentUser) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role for this operation",
            )
        return current_user

    return _dependency
