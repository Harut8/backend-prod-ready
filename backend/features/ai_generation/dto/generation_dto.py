"""
AI Generation DTOs.

Data Transfer Objects for AI text generation endpoints.
"""

from datetime import datetime

from pydantic import Field

from backend.core.api.dtos.base import BaseRequestDto, BaseResponseDto


class GenerateTextRequestDto(BaseRequestDto):
    """Request DTO for text generation."""

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="The prompt to generate text from",
    )
    max_tokens: int = Field(
        default=256,
        ge=1,
        le=2048,
        description="Maximum number of tokens to generate",
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling temperature (higher = more creative)",
    )


class GenerationUsageDto(BaseResponseDto):
    """Usage information for the generation."""

    prompt_tokens: int = Field(..., description="Number of tokens in the prompt")
    completion_tokens: int = Field(..., description="Number of tokens generated")
    total_tokens: int = Field(..., description="Total tokens used")


class GenerateTextResponseDto(BaseResponseDto):
    """Response DTO for text generation."""

    text: str = Field(..., description="The generated text")
    model: str = Field(..., description="The model used for generation")
    usage: GenerationUsageDto = Field(..., description="Token usage information")
    created_at: datetime = Field(..., description="Generation timestamp")
