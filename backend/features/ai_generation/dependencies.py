"""
Dependency Injection Container for AI Generation Feature.

Provides service wiring following Clean Architecture principles.
"""

from dependency_injector import containers, providers

from backend.features.ai_generation.services.generation_service import (
    AiGenerationService,
)


class AiGenerationContainer(containers.DeclarativeContainer):
    """Dependency injection container for AI generation feature."""

    # AI generation service (stateless - use Singleton for performance)
    generation_service: providers.Singleton[AiGenerationService] = providers.Singleton(
        AiGenerationService,
    )
