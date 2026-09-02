"""Create (or promote) an Admin user for local development.

Usage:
    uv run python scripts/seed_admin.py
    uv run python scripts/seed_admin.py --email dev.admin@example.com --password "S3cret-pass"

Behaviour:
    * If no user with the given email exists, one is created with the ``Admin``
      role and an ``active`` status.
    * If the user already exists, they are promoted to ``Admin`` (and re-enabled
      if disabled). The password is only reset when ``--reset-password`` is given.

Configuration falls back to the ``SEED_ADMIN_EMAIL`` / ``SEED_ADMIN_PASSWORD`` /
``SEED_ADMIN_NAME`` environment variables, then to the built-in dev defaults
below. The script talks to whatever database ``MONGODB_URI`` in ``.env`` points
at, so make sure that is the environment you intend to seed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Allow ``python scripts/seed_admin.py`` from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.security import hash_password  # noqa: E402
from app.db.session import close_db, init_db  # noqa: E402
from app.models.user import Role, User, UserStatus  # noqa: E402

DEFAULT_EMAIL = "admin@weeklyreport.dev"
DEFAULT_PASSWORD = "Admin@12345"
DEFAULT_NAME = "Dev Admin"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed or promote a development Admin user.")
    parser.add_argument(
        "--email",
        default=os.getenv("SEED_ADMIN_EMAIL", DEFAULT_EMAIL),
        help=f"Admin email (default: {DEFAULT_EMAIL})",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("SEED_ADMIN_PASSWORD", DEFAULT_PASSWORD),
        help="Admin password (used only when creating, or with --reset-password)",
    )
    parser.add_argument(
        "--name",
        default=os.getenv("SEED_ADMIN_NAME", DEFAULT_NAME),
        help=f"Display name (default: {DEFAULT_NAME!r})",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Also reset the password if the user already exists",
    )
    return parser.parse_args()


async def _seed(email: str, password: str, name: str, reset_password: bool) -> None:
    email = email.lower()
    await init_db()
    try:
        user = await User.find_one(User.email == email)

        if user is None:
            user = User(
                name=name,
                email=email,
                hashed_password=hash_password(password),
                role=Role.ADMIN,
                status=UserStatus.ACTIVE,
            )
            await user.insert()
            print(f"Created Admin user: {email}")
            print(f"Password: {password}")
            return

        changes: list[str] = []
        if user.role is not Role.ADMIN:
            user.role = Role.ADMIN
            changes.append("role -> Admin")
        if user.status is not UserStatus.ACTIVE:
            user.status = UserStatus.ACTIVE
            changes.append("status -> active")
        if reset_password:
            user.hashed_password = hash_password(password)
            changes.append("password reset")

        if changes:
            user.touch()
            await user.save()
            print(f"Updated existing user {email}: {', '.join(changes)}")
        else:
            print(f"User {email} is already an active Admin - nothing to do.")
        if reset_password:
            print(f"Password: {password}")
    finally:
        await close_db()


def main() -> None:
    args = _parse_args()
    if len(args.password) < 8:
        sys.exit("Password must be at least 8 characters.")
    asyncio.run(
        _seed(
            email=args.email,
            password=args.password,
            name=args.name,
            reset_password=args.reset_password,
        )
    )


if __name__ == "__main__":
    main()
