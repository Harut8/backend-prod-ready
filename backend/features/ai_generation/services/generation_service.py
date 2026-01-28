"""
AI Generation Service.

Application service that orchestrates text generation use cases.
Handles business logic coordination without infrastructure concerns.

Note: This service accepts primitive types, NOT DTOs.
DTOs should stop at the handler boundary per Clean Architecture.
"""

import structlog

from backend.features.ai_generation.domain.generation import TextGeneration
from backend.features.ai_generation.exceptions import (
    GenerationFailedError,
    PromptValidationError,
)


logger = structlog.get_logger(__name__)


class AiGenerationService:
    """
    Service for AI text generation.

    This service orchestrates the generation process:
    1. Validates business rules (not syntax - that's DTO's job)
    2. Calls the AI model (mocked for demo)
    3. Returns the domain result

    In production, this would integrate with actual AI providers
    (OpenAI, Anthropic, etc.) or custom models.
    """

    # Service-level constants for business rules
    MIN_PROMPT_LENGTH = 1
    MAX_PROMPT_LENGTH = 4000

    def __init__(self) -> None:
        """Initialize the generation service."""
        self._model_name = "demo-model-v1"

    async def generate_text(
        self,
        *,
        prompt: str,
        max_tokens: int,
        temperature: float,
        user_id: str,
    ) -> TextGeneration:
        """
        Generate text based on the provided prompt.

        Args:
            prompt: The input prompt for text generation
            max_tokens: Maximum number of tokens to generate
            temperature: Sampling temperature (creativity level)
            user_id: The authenticated user's ID (for logging/tracking)

        Returns:
            TextGeneration domain object with the result

        Raises:
            PromptValidationError: If prompt fails business validation
            GenerationFailedError: If generation process fails
        """
        # Business-level validation (not syntax validation)
        self._validate_prompt(prompt)

        logger.info(
            "Generating text",
            user_id=user_id,
            prompt_length=len(prompt),
            max_tokens=max_tokens,
            temperature=temperature,
        )

        # Mock AI generation (replace with actual AI integration)
        try:
            generated_text = self._mock_generate(
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e:
            msg = "Text generation failed"
            raise GenerationFailedError(
                msg,
                model=self._model_name,
                reason=str(e),
            ) from e

        # Calculate token usage (simplified estimation)
        prompt_tokens = len(prompt.split()) * 2  # Rough estimate
        completion_tokens = len(generated_text.split()) * 2

        result = TextGeneration.create(
            text=generated_text,
            model=self._model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

        logger.info(
            "Text generated successfully",
            user_id=user_id,
            total_tokens=result.usage.total_tokens,
        )

        return result

    def _validate_prompt(self, prompt: str) -> None:
        """
        Validate prompt against business rules.

        Args:
            prompt: The prompt to validate

        Raises:
            PromptValidationError: If prompt fails validation
        """
        # Check for empty or whitespace-only prompts
        if not prompt or not prompt.strip():
            msg = "Prompt cannot be empty or whitespace only"
            raise PromptValidationError(
                msg,
                prompt_length=len(prompt) if prompt else 0,
            )

        # Check for suspicious patterns (basic content moderation)
        # In production, this would integrate with content moderation APIs

    def _mock_generate(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,  # noqa: ARG002
    ) -> str:
        """
        Mock text generation for demonstration.

        In production, replace this with actual AI provider integration:
        - OpenAI API
        - Anthropic API
        - Self-hosted models
        - etc.
        """
        # Simple mock response for demo purposes
        response_templates = [
            f"Based on your prompt about '{prompt[:50]}...', here is a thoughtful response. ",
            "This feature demonstrates the identity-plan-kit integration. ",
            "The AI generation endpoint requires authentication, checks plan features, ",
            "and tracks usage against quotas. ",
            f"Max tokens: {max_tokens}. ",
            "Replace this mock with actual AI integration for production use.",
        ]
        return "".join(response_templates)
