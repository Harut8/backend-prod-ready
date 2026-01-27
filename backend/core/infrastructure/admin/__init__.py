"""
Admin Panel Infrastructure.

Provides SQLAdmin integration with identity-plan-kit models.

Admin roles (from IKP):
- Superadmin: Full permissions (create, edit, delete) - from ADMIN_EMAIL/PASSWORD
- Admin: View-only permissions - users with 'admin' role in database
"""

from identity_plan_kit.admin import AdminRole

from backend.core.infrastructure.admin.auth_backend import AdminAuthBackend
from backend.core.infrastructure.admin.setup import setup_admin_panel


__all__ = [
    "AdminAuthBackend",
    "AdminRole",
    "setup_admin_panel",
]
