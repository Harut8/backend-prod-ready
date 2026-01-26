"""
AI Generation Service.

Application service that orchestrates text generation use cases.
Handles business logic coordination without infrastructure concerns.
"""

import structlog

from backend.features.ai_generation.domain.generation import TextGeneration
from backend.features.ai_generation.dto.generation_dto import GenerateTextRequestDto


logger = structlog.get_logger(__name__)


class AiGenerationService:
    """
    Service for AI text generation.

    This service orchestrates the generation process:
    1. Validates the request
    2. Calls the AI model (mocked for demo)
    3. Returns the domain result

    In production, this would integrate with actual AI providers
    (OpenAI, Anthropic, etc.) or custom models.
    """

    def __init__(self) -> None:
        """Initialize the generation service."""
        self._model_name = "demo-model-v1"

    async def generate_text(
        self,
        request: GenerateTextRequestDto,
        user_id: str,
    ) -> TextGeneration:
        """
        Generate text based on the provided prompt.

        Args:
            request: The generation request with prompt and parameters
            user_id: The authenticated user's ID (for logging/tracking)

        Returns:
            TextGeneration domain object with the result
        """
        logger.info(
            "Generating text",
            user_id=user_id,
            prompt_length=len(request.prompt),
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )

        # Mock AI generation (replace with actual AI integration)
        generated_text = self._mock_generate(
            prompt=request.prompt,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )

        # Calculate token usage (simplified estimation)
        prompt_tokens = len(request.prompt.split()) * 2  # Rough estimate
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
