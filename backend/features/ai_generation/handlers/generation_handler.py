"""
AI Generation HTTP Handlers.

Transport layer adapters for AI generation feature.
Handles HTTP-specific concerns and delegates business logic to services.

Demonstrates identity-plan-kit (IPK) integration:
- CurrentUser: Ensures user is authenticated
- requires_feature: Checks plan access, consumes quota with idempotency support

Architecture notes:
- DTOs are used for request/response serialization
- Mappers convert between domain objects and DTOs
- Services receive primitives, not DTOs (boundary enforcement)

Idempotency:
- X-Idempotency-Key header enables safe retries for quota-consuming operations
- Duplicate requests with same key return cached result without double-deducting
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request
from identity_plan_kit.auth.dependencies import CurrentUserNoRole
from identity_plan_kit.plans.dependencies import requires_feature
from identity_plan_kit.plans.dto.usage import UsageInfo
import structlog

from backend.core.api.dtos.base import ResponseModel
from backend.core.security.rate_limiting import limiter
from backend.features.ai_generation.dependencies import AiGenerationContainer
from backend.features.ai_generation.domain import FeatureCode
from backend.features.ai_generation.dto.generation_dto import (
    GenerateTextRequestDto,
    GenerateTextResponseDto,
)
from backend.features.ai_generation.mappers import GenerationMapper
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

    **Headers:**
    - `X-Idempotency-Key` (recommended): Unique key for safe retries.
      Prevents double quota deduction on network retries.

    **IPK Integration:**
    - `CurrentUser`: Validates JWT and returns authenticated user
    - `requires_feature`: Checks plan access, consumes quota with idempotency support
    """,
)
@limiter.limit("30/minute")
@inject
async def generate_text(
    request: Request,  # noqa: ARG001
    body: GenerateTextRequestDto,
    # IPK: Require authenticated user (CurrentUser is Annotated[User, Depends(...)])
    user: CurrentUserNoRole,
    # IPK: Check feature access and consume quota (returns UsageInfo with quota details)
    _usage: Annotated[UsageInfo, requires_feature(FeatureCode.AI_GENERATION, consume=1)],
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

    Architecture:
    - DTO handles syntax validation (Pydantic)
    - Service receives primitives (not DTO) - boundary enforcement
    - Mapper converts domain object to response DTO
    """
    # Log request metadata only - no PII (email) or user content (prompt)
    logger.info(
        "AI generation request",
        user_id=str(user.id),
        prompt_length=len(body.prompt),
        max_tokens=body.max_tokens,
    )

    # Generate text using the service (pass primitives, not DTO)
    result = await generation_service.generate_text(
        prompt=body.prompt,
        max_tokens=body.max_tokens,
        temperature=body.temperature,
        user_id=str(user.id),
    )

    # Map domain object to response DTO using mapper
    response_dto = GenerationMapper.to_response_dto(result, quota_info=_usage)

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
    user: CurrentUserNoRole,
) -> ResponseModel[dict]:
    """
    Get usage statistics for the authenticated user.

    Returns current usage and quota information for the ai_generation feature.
    """
    # Get feature usage from IPK (if available on user's plan)
    # Note: email omitted from response - use /auth/me for user profile
    usage_info = {
        "user_id": str(user.id),
        "feature": FeatureCode.AI_GENERATION,
        "message": "Use /api/v1/ai/generate to track actual usage",
    }

    return ResponseModel.ok(data=usage_info)
