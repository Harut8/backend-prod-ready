"""
AI Generation Feature.

Demonstrates identity-plan-kit integration with:
- Authentication (require_user)
- Plan-based feature access (require_feature)
- Quota tracking (track_usage)
"""

from backend.features.ai_generation.dependencies import AiGenerationContainer
from backend.features.ai_generation.handlers import router as ai_generation_router


__all__ = [
    "AiGenerationContainer",
    "ai_generation_router",
]
