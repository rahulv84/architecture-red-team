"""Application exception hierarchy.

``AppError`` subclasses carry an HTTP status and a stable machine-readable ``code`` so the
FastAPI exception handler can return structured errors:
``{"error": {"code": ..., "message": ..., "details": ...}}``.
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: Any = None, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if code:
            self.code = code

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


def describe_error(exc: BaseException, limit: int = 300) -> str:
    """User-facing error text that never includes stack traces or secrets."""
    if isinstance(exc, AppError):
        return f"{exc.code}: {exc.message}"
    return f"{exc.__class__.__name__}: {str(exc)[:limit]}"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class UnsupportedFileError(AppError):
    status_code = 415
    code = "unsupported_file"


class FileTooLargeError(AppError):
    status_code = 413
    code = "file_too_large"


class ParseError(AppError):
    """A document could not be parsed (corrupt, encrypted, malformed)."""

    status_code = 422
    code = "parse_error"


class ProviderError(AppError):
    """Base class for LLM provider failures."""

    status_code = 502
    code = "provider_error"


class ProviderAuthError(ProviderError):
    status_code = 401
    code = "provider_auth_error"


class ProviderRateLimitError(ProviderError):
    status_code = 429
    code = "provider_rate_limited"


class ProviderTimeoutError(ProviderError):
    status_code = 504
    code = "provider_timeout"


class ProviderConfigError(ProviderError):
    status_code = 400
    code = "provider_not_configured"


class StructuredOutputError(ProviderError):
    """The model returned output that does not satisfy the requested schema."""

    code = "structured_output_invalid"


class ReviewCancelled(AppError):
    status_code = 409
    code = "review_cancelled"
