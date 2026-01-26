"""
AI Generation Domain Entities.

Pure domain objects representing text generation concepts.
No framework or infrastructure dependencies.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class FeatureCode(StrEnum):
    """Feature codes for IPK plan-based access control."""

    AI_GENERATION = "ai_generation"


@dataclass(frozen=True)
class TokenUsage:
    """Token usage for a generation request."""

    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        """Calculate total tokens used."""
        return self.prompt_tokens + self.completion_tokens

    def __post_init__(self) -> None:
        """Validate token counts."""
        if self.prompt_tokens < 0:
            msg = "prompt_tokens must be non-negative"
            raise ValueError(msg)
        if self.completion_tokens < 0:
            msg = "completion_tokens must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True)
class TextGeneration:
    """
    Domain entity representing a text generation result.

    This is a pure domain object with no external dependencies.
    Business logic and invariants are enforced here.
    """

    text: str
    model: str
    usage: TokenUsage
    created_at: datetime

    @classmethod
    def create(
        cls,
        text: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> "TextGeneration":
        """
        Factory method to create a TextGeneration.

        Args:
            text: The generated text
            model: The model identifier used
            prompt_tokens: Number of tokens in the prompt
            completion_tokens: Number of tokens generated

        Returns:
            A new TextGeneration instance
        """
        return cls(
            text=text,
            model=model,
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
            created_at=datetime.now(UTC),
        )
