"""User-management endpoints with role-based access control.

Access summary:
    * ``GET /users/``               - Manager or Admin only.
    * ``GET /users/{id}``           - own record for Team Members; anyone for Manager/Admin.
    * ``PATCH /users/{id}/role``    - Admin only.
    * ``PATCH /users/{id}/status``  - Manager or Admin only.
"""

from __future__ import annotations

from typing import Annotated

from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, require_roles
from app.models.user import Role, User
from app.schemas.auth import RoleUpdateRequest, StatusUpdateRequest, UserResponse

router = APIRouter(prefix="/users", tags=["users"])

ManagerOrAdmin = Annotated[User, Depends(require_roles(Role.MANAGER, Role.ADMIN))]
AdminOnly = Annotated[User, Depends(require_roles(Role.ADMIN))]


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


@router.get("/", response_model=list[UserResponse], summary="List all users (Manager/Admin)")
async def list_users(
    _: ManagerOrAdmin,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    role: Annotated[Role | None, Query(description="Optional role filter")] = None,
) -> list[UserResponse]:
    """Return a paginated list of user records.

    Only Managers and Admins may enumerate users; a Team Member receives ``403``.
    """
    query = User.find() if role is None else User.find(User.role == role)
    users = await query.sort("+created_at").skip(skip).limit(limit).to_list()
    return [UserResponse.from_user(user) for user in users]


@router.get("/{user_id}", response_model=UserResponse, summary="Get a single user")
async def get_user(user_id: str, current_user: CurrentUser) -> UserResponse:
    """Return one user record.

    A Team Member may only read their own record (``403`` otherwise). Managers and
    Admins may read any record.

    Raises:
        HTTPException: ``403`` if a Team Member requests someone else's record;
            ``404`` if the id does not exist.
    """
    is_privileged = current_user.has_any_role(Role.MANAGER, Role.ADMIN)
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
    summary="Update a user's role (Admin only)",
)
async def update_user_role(
    user_id: str,
    payload: RoleUpdateRequest,
    admin: AdminOnly,
) -> UserResponse:
    """Assign a new role to a user.

    Raises:
        HTTPException: ``400`` if an Admin targets their own account (guards
            against accidental self-lockout); ``404`` if the user does not exist.
    """
    if str(admin.id) == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot change their own role",
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
    summary="Enable or disable a user (Manager/Admin)",
)
async def update_user_status(
    user_id: str,
    payload: StatusUpdateRequest,
    actor: ManagerOrAdmin,
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
