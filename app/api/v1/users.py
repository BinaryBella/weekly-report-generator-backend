"""User-management endpoints with role-based access control.

The system has two roles: Team Member and Manager. Manager is the fully
privileged ("admin") role.

Access summary:
    * ``GET  /users/``               - Manager only.
    * ``POST /users/``               - Manager only; directly creates ("invites") a
                                       team member account.
    * ``GET  /users/{id}``           - own record for Team Members; any record for a Manager.
    * ``PATCH /users/{id}/role``     - Manager only.
    * ``PATCH /users/{id}/status``   - Manager only; disabling a user is
                                       this app's equivalent of "removing" them -
                                       their past reports/projects stay intact.
    * ``DELETE /users/{id}``         - Manager only; "remove" a team member. Hard-deletes
                                       when nothing references the account, otherwise
                                       disables it (same soft/hard pattern as projects).
"""

from __future__ import annotations

import secrets
from typing import Annotated

from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo.errors import DuplicateKeyError

from app.api.deps import CurrentUser, require_roles
from app.core.security import hash_password
from app.models.project import Project
from app.models.report import Report
from app.models.user import Role, User, UserStatus
from app.schemas.auth import (
    RoleUpdateRequest,
    StatusUpdateRequest,
    UserCreateRequest,
    UserCreateResponse,
    UserDeleteResponse,
    UserResponse,
)
from app.services.email_service import send_invite_email

router = APIRouter(prefix="/users", tags=["users"])

ManagerOnly = Annotated[User, Depends(require_roles(Role.MANAGER))]


async def _get_user_or_404(user_id: str) -> User:
    """Fetch a user by id, raising ``404`` for both unknown and malformed ids."""
    try:
        user = await User.get(user_id)
    except (InvalidId, ValueError):
        user = None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


@router.get("/", response_model=list[UserResponse], summary="List all users (Manager only)")
async def list_users(
    _: ManagerOnly,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    role: Annotated[Role | None, Query(description="Optional role filter")] = None,
) -> list[UserResponse]:
    """Return a paginated list of user records.

    Only a Manager may enumerate users; a Team Member receives ``403``.
    """
    query = User.find() if role is None else User.find(User.role == role)
    users = await query.sort("+created_at").skip(skip).limit(limit).to_list()
    return [UserResponse.from_user(user) for user in users]


@router.post(
    "/",
    response_model=UserCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a team member account (Manager only)",
)
async def create_user(
    payload: UserCreateRequest,
    _: ManagerOnly,
) -> UserCreateResponse:
    """Create a user account and email them their sign-in credentials.

    Assigns *role* (default Team Member) immediately - no separate role-update
    call is needed. When *password* is omitted, a random temporary password is
    generated. The credentials are emailed to the new account when SMTP is
    configured (see ``UserCreateResponse.email_sent``); the temporary password
    is also returned once in the response either way, so the Manager can share
    it out of band if email delivery isn't configured or fails.

    Raises:
        HTTPException: ``400`` if the email is already registered.
    """
    email = payload.email.lower()
    if await User.find_one(User.email == email) is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already exists",
        )

    temporary_password = payload.password or secrets.token_urlsafe(12)
    user = User(
        name=payload.name,
        email=email,
        hashed_password=hash_password(temporary_password),
        role=payload.role,
        status=UserStatus.ACTIVE,
    )
    try:
        await user.insert()
    except DuplicateKeyError as exc:  # race between the check above and insert
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already exists",
        ) from exc

    email_sent = await send_invite_email(
        to_email=user.email,
        to_name=user.name,
        temporary_password=temporary_password,
        role=user.role.value,
    )

    return UserCreateResponse(
        user=UserResponse.from_user(user),
        temporary_password=None if payload.password else temporary_password,
        email_sent=email_sent,
    )


@router.get("/{user_id}", response_model=UserResponse, summary="Get a single user")
async def get_user(user_id: str, current_user: CurrentUser) -> UserResponse:
    """Return one user record.

    A Team Member may only read their own record (``403`` otherwise). A Manager
    may read any record.

    Raises:
        HTTPException: ``403`` if a Team Member requests someone else's record;
            ``404`` if the id does not exist.
    """
    is_privileged = current_user.has_any_role(Role.MANAGER)
    if not is_privileged and str(current_user.id) != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only access your own profile",
        )

    user = await _get_user_or_404(user_id)
    return UserResponse.from_user(user)


@router.patch(
    "/{user_id}/role",
    response_model=UserResponse,
    summary="Update a user's role (Manager only)",
)
async def update_user_role(
    user_id: str,
    payload: RoleUpdateRequest,
    manager: ManagerOnly,
) -> UserResponse:
    """Assign a new role to a user.

    Raises:
        HTTPException: ``400`` if the caller targets their own account (guards
            against accidental self-lockout); ``404`` if the user does not exist.
    """
    if str(manager.id) == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own role",
        )

    user = await _get_user_or_404(user_id)
    if user.role is not payload.role:
        user.role = payload.role
        user.touch()
        await user.save()
    return UserResponse.from_user(user)


@router.patch(
    "/{user_id}/status",
    response_model=UserResponse,
    summary="Enable or disable a user (Manager)",
)
async def update_user_status(
    user_id: str,
    payload: StatusUpdateRequest,
    actor: ManagerOnly,
) -> UserResponse:
    """Activate or disable a user account.

    Raises:
        HTTPException: ``400`` if the caller targets their own account;
            ``404`` if the user does not exist.
    """
    if str(actor.id) == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own account status",
        )

    user = await _get_user_or_404(user_id)
    if user.status is not payload.status:
        user.status = payload.status
        user.touch()
        await user.save()
    return UserResponse.from_user(user)


@router.delete(
    "/{user_id}",
    response_model=UserDeleteResponse,
    summary="Remove a team member (Manager only)",
)
async def delete_user(user_id: str, actor: ManagerOnly) -> UserDeleteResponse:
    """Remove a user account.

    Hard-deletes the record outright when nothing references it (no reports,
    not assigned to any project). Otherwise the account is disabled instead -
    this app's equivalent of "removing" someone who has history - so past
    reports and project assignments stay intact. Since the caller must be an
    active Manager other than the target (see the self-removal guard below),
    at least one active Manager always remains after this call.

    Raises:
        HTTPException: ``400`` if the caller targets their own account;
            ``404`` if the user does not exist.
    """
    if str(actor.id) == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot remove your own account",
        )

    user = await _get_user_or_404(user_id)

    has_reports = await Report.find_one(Report.user_id == user_id) is not None
    is_project_member = await Project.find_one({"member_ids": user_id}) is not None

    if not has_reports and not is_project_member:
        await user.delete()
        return UserDeleteResponse(detail="User deleted", soft_deleted=False, user=None)

    if user.status is not UserStatus.DISABLED:
        user.status = UserStatus.DISABLED
        user.touch()
        await user.save()
    return UserDeleteResponse(
        detail=(
            "User has existing reports or project assignments, so the "
            "account was disabled instead of deleted"
        ),
        soft_deleted=True,
        user=UserResponse.from_user(user),
    )
