#!/usr/bin/env python3
"""
Create Admin User Script.

Creates a new admin user or promotes an existing user to admin role.
Admin users have view-only access to the admin panel.

Usage:
    uv run python scripts/create_admin.py --email admin@example.com --password SecurePass123!

    # Or with environment variables:
    ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD=SecurePass123! uv run python scripts/create_admin.py
"""

import argparse
import asyncio
import os
import sys


# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def create_admin(email: str, password: str) -> None:
    """Create or update an admin user."""
    import bcrypt
    from sqlalchemy import select, update
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from backend.core.conf.settings import SETTINGS

    # Import IKP models
    from identity_plan_kit.auth.models.user import UserModel
    from identity_plan_kit.rbac.models.role import RoleModel

    # Create engine
    engine = create_async_engine(
        str(SETTINGS.DATABASE.DATABASE_URL),
        echo=False,
    )

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        # Find or create 'admin' role
        role_stmt = select(RoleModel).where(RoleModel.code == "admin")
        role_result = await session.execute(role_stmt)
        admin_role = role_result.scalar_one_or_none()

        if not admin_role:
            # Create admin role
            admin_role = RoleModel(code="admin", name="Admin")
            session.add(admin_role)
            await session.flush()
            print("Created 'admin' role")

        # Hash password using bcrypt (passlib-compatible $2b$ format)
        password_hash = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")

        # Check if user exists
        user_stmt = select(UserModel).where(UserModel.email == email.lower())
        user_result = await session.execute(user_stmt)
        user = user_result.scalar_one_or_none()

        if user:
            # Update existing user to admin role with password
            await session.execute(
                update(UserModel)
                .where(UserModel.id == user.id)
                .values(
                    role_id=admin_role.id,
                    password_hash=password_hash,
                    is_active=True,
                )
            )
            print(f"Updated user '{email}' to admin role with password")
        else:
            # Create new admin user
            user = UserModel(
                email=email.lower(),
                role_id=admin_role.id,
                password_hash=password_hash,
                is_active=True,
                is_verified=True,
            )
            session.add(user)
            print(f"Created new admin user '{email}'")

        await session.commit()

    await engine.dispose()
    print("Done!")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or update an admin user for the admin panel")
    parser.add_argument(
        "--email",
        type=str,
        help="Admin email address",
    )
    parser.add_argument(
        "--password",
        type=str,
        help="Admin password (will be hashed)",
    )

    args = parser.parse_args()

    if not args.email:
        print("Error: --email is required")
        sys.exit(1)

    if not args.password:
        print("Error: --password is required")
        sys.exit(1)

    if len(args.password) < 8:
        print("Error: Password must be at least 8 characters")
        sys.exit(1)

    # Check if email matches superadmin email from env
    superadmin_email = os.getenv("ADMIN_EMAIL", "").lower()
    if args.email.lower() == superadmin_email:
        print(f"Error: Cannot create DB admin with superadmin email '{args.email}'")
        print(f"       Superadmin already uses this email (from ADMIN_EMAIL env var)")
        print(f"       Use a different email for DB admin users")
        sys.exit(1)

    asyncio.run(create_admin(args.email, args.password))


if __name__ == "__main__":
    main()
