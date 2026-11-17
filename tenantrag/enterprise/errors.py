"""Structured enterprise domain errors mapped cleanly to HTTP statuses."""
from __future__ import annotations


class EnterpriseError(RuntimeError):
    """Base error for enterprise control plane and runtime operations."""

    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(self, message: str, *, error_code: str | None = None):
        super().__init__(message)
        if error_code:
            self.error_code = error_code


class UnauthorizedError(EnterpriseError):
    status_code = 401
    error_code = "unauthorized"


class ForbiddenError(EnterpriseError):
    status_code = 403
    error_code = "forbidden"


class NotFoundError(EnterpriseError):
    status_code = 404
    error_code = "not_found"


class ConflictError(EnterpriseError):
    status_code = 409
    error_code = "conflict"


class RateLimitedError(EnterpriseError):
    status_code = 429
    error_code = "rate_limited"

    def __init__(self, message: str, *, headers: dict[str, str] | None = None, retry_after: int = 1):
        super().__init__(message, error_code="rate_limited")
        self.headers = headers or {}
        self.retry_after = retry_after
