"""
AI Generation Feature Exceptions.

Feature-specific domain errors for AI text generation.
These exceptions are raised within the feature and translated
to HTTP errors at the handler layer.
"""

from typing import Any

from backend.core.domain.exceptions import DomainError, DomainValidationError


class AiGenerationError(DomainError):
    """Base exception for AI generation feature errors."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            code=code or "AI_GENERATION_ERROR",
            context=context,
        )


class PromptValidationError(DomainValidationError):
    """Exception for invalid prompt content."""

    def __init__(
        self,
        message: str,
        *,
        prompt_length: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if prompt_length is not None:
            ctx["prompt_length"] = prompt_length
        super().__init__(message, field="prompt", context=ctx)


class TokenLimitExceededError(AiGenerationError):
    """Exception when token limits are exceeded."""

    def __init__(
        self,
        message: str,
        *,
        requested_tokens: int | None = None,
        max_tokens: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if requested_tokens is not None:
            ctx["requested_tokens"] = requested_tokens
        if max_tokens is not None:
            ctx["max_tokens"] = max_tokens
        super().__init__(message, code="TOKEN_LIMIT_EXCEEDED", context=ctx)


class GenerationFailedError(AiGenerationError):
    """Exception when AI generation fails."""

    def __init__(
        self,
        message: str,
        *,
        model: str | None = None,
        reason: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if model:
            ctx["model"] = model
        if reason:
            ctx["reason"] = reason
        super().__init__(message, code="GENERATION_FAILED", context=ctx)


class QuotaExhaustedError(AiGenerationError):
    """Exception when user's generation quota is exhausted."""

    def __init__(
        self,
        message: str = "AI generation quota exhausted",
        *,
        user_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if user_id:
            ctx["user_id"] = user_id
        super().__init__(message, code="QUOTA_EXHAUSTED", context=ctx)
