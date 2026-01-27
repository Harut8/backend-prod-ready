"""
Custom Error Formatter for Kinonee.

Extends identity-plan-kit's ErrorFormatter to match the application's
existing error response format with `success: false` and `context` field.

Error response format:
    {
        "success": false,
        "error": {
            "code": "ERROR_CODE",
            "message": "Human-readable message",
            "context": { ... }  # Optional additional details
        }
    }
"""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from identity_plan_kit.shared.error_formatter import ErrorFormatter


class KinoneeErrorFormatter(ErrorFormatter):
    """
    Custom error formatter matching Kinonee's API error response format.

    This formatter ensures consistency between IPK-generated errors and
    application-level errors by using the same response structure.

    Produces responses in the format:
        ```json
        {
            "success": false,
            "error": {
                "code": "ERROR_CODE",
                "message": "Human-readable message",
                "context": { ... }
            }
        }
        ```
    """

    def format_error(
        self,
        request: Request,  # noqa: ARG002
        status_code: int,  # noqa: ARG002
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Format error in Kinonee's standard format."""
        content: dict[str, Any] = {
            "success": False,
            "error": {
                "code": code,
                "message": message,
            },
        }

        # Use 'context' instead of 'details' to match existing handlers
        if details:
            content["error"]["context"] = details

        return content

    def create_response(
        self,
        request: Request,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> JSONResponse:
        """Create a JSONResponse with Kinonee's error format."""
        content = self.format_error(request, status_code, code, message, details)
        return JSONResponse(status_code=status_code, content=content)
