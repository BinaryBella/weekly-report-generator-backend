"""Authentication endpoints: register, login, refresh, logout, me."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pymongo.errors import DuplicateKeyError

from app.api.deps import CurrentUser, get_access_token_payload
from app.core.config import settings
from app.core.security import (
    IssuedToken,
    TokenError,
    TokenPayload,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import RevokedToken, Role, User, UserStatus
from app.schemas.auth import (
    AccessTokenResponse,
    MessageResponse,
    RefreshRequest,
    SignupRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password",
    headers={"WWW-Authenticate": "Bearer"},
)


async def _revoke(token: IssuedToken | TokenPayload, *, user_id: str, token_type: str) -> None:
    """Insert a token ``jti`` into the denylist, ignoring an already-present entry."""
    try:
        await RevokedToken(
            jti=token.jti,
            user_id=user_id,
            token_type=token_type,
            expires_at=token.expires_at if isinstance(token, IssuedToken) else token.exp,
        ).insert()
    except DuplicateKeyError:
        # Token was already revoked - logout stays idempotent.
        pass


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(payload: SignupRequest) -> UserResponse:
    """Create a new account.

    The new user is always assigned the ``Team Member`` role unless their email
    appears in ``BOOTSTRAP_ADMIN_EMAILS`` (the seed for the first privileged
    account), in which case they are made a ``Manager``. Promotion to
    ``Manager`` afterwards is a Manager-only operation.

    Raises:
        HTTPException: ``400`` if the email is already registered.
    """
    email = payload.email.lower()
    if await User.find_one(User.email == email) is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already exists",
        )

    role = (
        Role.MANAGER if settings.is_bootstrap_admin(email) else Role.TEAM_MEMBER
    )
    user = User(
        name=payload.name,
        email=email,
        hashed_password=hash_password(payload.password),
        role=role,
        status=UserStatus.ACTIVE,
    )
    try:
        await user.insert()
    except DuplicateKeyError as exc:  # race between the check above and insert
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already exists",
        ) from exc

    return UserResponse.from_user(user)


@router.post("/login", response_model=TokenResponse, summary="Obtain access & refresh tokens")
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenResponse:
    """Authenticate with email + password and receive a JWT pair.

    The OAuth2 password form is used so the interactive docs "Authorize" button
    works; send the email address in the ``username`` field. A JSON body matching
    :class:`~app.schemas.auth.LoginRequest` is the programmatic equivalent.

    Raises:
        HTTPException: ``401`` if the credentials are invalid;
            ``403`` if the account is disabled.
    """
    user = await User.find_one(User.email == form_data.username.lower())
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise _CREDENTIALS_EXC

    if user.status is UserStatus.DISABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    access = create_access_token(str(user.id))
    refresh = create_refresh_token(str(user.id))
    return TokenResponse(
        access_token=access.token,
        refresh_token=refresh.token,
        expires_in=settings.access_token_expire_seconds,
    )


@router.post("/refresh", response_model=AccessTokenResponse, summary="Exchange a refresh token")
async def refresh_access_token(payload: RefreshRequest) -> AccessTokenResponse:
    """Issue a fresh access token from a still-valid refresh token.

    Refresh tokens are single-purpose and are **not** rotated here (out of scope
    for Requirement 1); the same refresh token may be redeemed until it expires
    or is revoked via ``/auth/logout``.

    Raises:
        HTTPException: ``401`` if the refresh token is invalid, expired, revoked,
            or its owner no longer exists / is disabled.
    """
    try:
        token = decode_token(payload.refresh_token, expected_type=TokenType.REFRESH)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if await RevokedToken.find_one(RevokedToken.jti == token.jti) is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await User.get(token.sub)
    if user is None or user.status is UserStatus.DISABLED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is no longer active",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access = create_access_token(str(user.id))
    return AccessTokenResponse(
        access_token=access.token,
        expires_in=settings.access_token_expire_seconds,
    )


@router.post("/logout", response_model=MessageResponse, summary="Revoke the current tokens")
async def logout(
    current_user: CurrentUser,
    access_payload: Annotated[TokenPayload, Depends(get_access_token_payload)],
    body: Annotated[RefreshRequest | None, Body()] = None,
) -> MessageResponse:
    """Revoke the presented access token (and an optional refresh token).

    Because JWT verification is stateless, "logout" means adding the token's
    ``jti`` to a server-side denylist that :func:`get_access_token_payload`
    consults on every request. Denylist rows self-expire via a MongoDB TTL index.

    Re-submitting an already-revoked access token is rejected with ``401`` by the
    auth dependency before this handler runs; the denylist write itself is
    idempotent (a duplicate ``jti`` is ignored).
    """
    await _revoke(access_payload, user_id=str(current_user.id), token_type="access")

    if body is not None and body.refresh_token:
        try:
            refresh = decode_token(body.refresh_token, expected_type=TokenType.REFRESH)
        except TokenError:
            # A bad refresh token should not fail the logout of a valid access token.
            pass
        else:
            if refresh.sub == str(current_user.id):
                await _revoke(refresh, user_id=str(current_user.id), token_type="refresh")

    return MessageResponse(detail="Successfully logged out")


@router.get("/me", response_model=UserResponse, summary="Get the current user's profile")
async def read_me(current_user: CurrentUser) -> UserResponse:
    """Return the authenticated user's own profile record."""
    return UserResponse.from_user(current_user)
