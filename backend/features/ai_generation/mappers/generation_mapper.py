"""
AI Generation Mapper.

Handles conversions between domain objects and DTOs for the AI generation feature.
Following Clean Architecture, mappers provide explicit layer boundary conversions.
"""

from identity_plan_kit.plans.dto.usage import UsageInfo

from backend.features.ai_generation.domain.generation import TextGeneration
from backend.features.ai_generation.dto.generation_dto import (
    GenerateTextResponseDto,
    GenerationUsageDto,
)


class GenerationMapper:
    """
    Mapper for AI generation domain objects to DTOs.

    Responsibilities:
    - Convert domain TextGeneration to response DTO
    - Handle nested object mapping (TokenUsage -> GenerationUsageDto)

    This mapper ensures domain objects never leak outside the feature boundary
    and provides a single point of change for DTO structure modifications.
    """

    @staticmethod
    def to_response_dto(domain: TextGeneration, quota_info: UsageInfo) -> GenerateTextResponseDto:
        """
        Convert TextGeneration domain object to response DTO.

        Args:
            domain: The TextGeneration domain entity

        Returns:
            GenerateTextResponseDto ready for HTTP response
        """
        return GenerateTextResponseDto(
            text=domain.text,
            model=domain.model,
            usage=GenerationUsageDto(
                prompt_tokens=domain.usage.prompt_tokens,
                completion_tokens=domain.usage.completion_tokens,
                total_tokens=domain.usage.total_tokens,
            ),
            quota_info=quota_info,
            created_at=domain.created_at,
        )

    @staticmethod
    def usage_to_dto(prompt_tokens: int, completion_tokens: int) -> GenerationUsageDto:
        """
        Create a GenerationUsageDto from token counts.

        Args:
            prompt_tokens: Number of tokens in the prompt
            completion_tokens: Number of tokens generated

        Returns:
            GenerationUsageDto with calculated total
        """
        return GenerationUsageDto(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
