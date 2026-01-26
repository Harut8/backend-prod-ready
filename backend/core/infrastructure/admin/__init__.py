"""
Admin Panel Infrastructure.

Provides SQLAdmin integration with identity-plan-kit models.
"""

from backend.core.infrastructure.admin.auth_backend import AdminAuthBackend
from backend.core.infrastructure.admin.setup import setup_admin_panel


__all__ = [
    "AdminAuthBackend",
    "setup_admin_panel",
]
