"""
AI Generation HTTP Handlers.

Demonstrates identity-plan-kit (IPK) integration:
- CurrentUser: Ensures user is authenticated
- requires_feature: Checks if user's plan includes this feature
- PlanService.check_and_consume_quota: Records feature usage against quotas
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request
import structlog

from backend.core.api.dtos.base import ResponseModel
from backend.core.security.ipk_dependencies import CurrentUser, requires_feature
from backend.core.security.rate_limiting import limiter
from backend.features.ai_generation.dependencies import AiGenerationContainer
from backend.features.ai_generation.domain import FeatureCode
from backend.features.ai_generation.dto.generation_dto import (
    GenerateTextRequestDto,
    GenerateTextResponseDto,
    GenerationUsageDto,
)
from backend.features.ai_generation.services.generation_service import (
    AiGenerationService,
)


logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/ai", tags=["AI Generation"])


@router.post(
    "/generate",
    response_model=ResponseModel[GenerateTextResponseDto],
    operation_id="ai_generate_text",
    summary="Generate text using AI",
    description="""
    Generate text based on a prompt.

    **Requires:**
    - Authentication (valid access token)
    - Plan feature: `ai_generation` enabled
    - Available quota for the feature

    **IPK Integration Demo:**
    - `CurrentUser`: Validates JWT and returns authenticated user
    - `requires_feature`: Checks if user's plan includes 'ai_generation'
    """,
)
@limiter.limit("30/minute")
@inject
async def generate_text(
    request: Request,  # noqa: ARG001
    body: GenerateTextRequestDto,
    # IPK: Require authenticated user (CurrentUser is Annotated[User, Depends(...)])
    user: CurrentUser,
    # IPK: Check feature access based on user's plan (requires_feature returns Depends)
    _feature_check: Annotated[None, requires_feature(FeatureCode.AI_GENERATION, 1)],
    # Service injection
    generation_service: Annotated[
        AiGenerationService,
        Depends(Provide[AiGenerationContainer.generation_service]),
    ],
) -> ResponseModel[GenerateTextResponseDto]:
    """
    Generate text from a prompt.

    This endpoint demonstrates the full IPK integration:
    1. User must be authenticated (CurrentUser)
    2. User's plan must include 'ai_generation' feature (requires_feature)
    """
    logger.info(
        "AI generation request",
        user_id=str(user.id),
        user_email=user.email,
        prompt_preview=body.prompt[:50] if len(body.prompt) > 50 else body.prompt,
    )

    # Generate text using the service
    result = await generation_service.generate_text(
        request=body,
        user_id=str(user.id),
    )

    # Map domain object to response DTO
    response_dto = GenerateTextResponseDto(
        text=result.text,
        model=result.model,
        usage=GenerationUsageDto(
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            total_tokens=result.usage.total_tokens,
        ),
        created_at=result.created_at,
    )

    return ResponseModel.ok(data=response_dto)


@router.get(
    "/usage",
    response_model=ResponseModel[dict],
    operation_id="ai_get_usage",
    summary="Get AI generation usage",
    description="""
    Get the current user's AI generation usage and quota information.

    **Requires:**
    - Authentication (valid access token)
    """,
)
@limiter.limit("60/minute")
async def get_usage(
    request: Request,  # noqa: ARG001
    user: CurrentUser,
) -> ResponseModel[dict]:
    """
    Get usage statistics for the authenticated user.

    Returns current usage and quota information for the ai_generation feature.
    """
    # Get feature usage from IPK (if available on user's plan)
    usage_info = {
        "user_id": str(user.id),
        "email": user.email,
        "feature": FeatureCode.AI_GENERATION,
        "message": "Use /api/v1/ai/generate to track actual usage",
    }

    return ResponseModel.ok(data=usage_info)
