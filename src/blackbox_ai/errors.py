"""Typed gateway exceptions and their JSON representations.

Errors raised on the request path are always mapped to a structured JSON body
shaped like provider errors (``{"error": {...}}``) so that client SDKs can parse
them. Stack traces are never leaked to clients.
"""

from __future__ import annotations

import math
from typing import Any

__all__ = [
    "AuthenticationError",
    "GatewayError",
    "ProviderNotConfiguredError",
    "RateLimitExceededError",
    "RequestTooLargeError",
    "SearchUnavailableError",
    "ServiceOverloadedError",
    "UnknownProviderError",
    "UpstreamConnectionError",
    "UpstreamTimeoutError",
]


class GatewayError(Exception):
    """Base class for all gateway request-path errors.

    Attributes:
        status_code: HTTP status to return to the client.
        code: Stable machine-readable error code.
        message: Human readable description (safe to expose).
        headers: Extra response headers to emit (e.g. ``Retry-After``).
    """

    status_code: int = 500
    code: str = "gateway_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.headers: dict[str, str] = {}

    def to_dict(self) -> dict[str, Any]:
        """Render an OpenAI/Anthropic-style error envelope."""
        error: dict[str, Any] = {
            "message": self.message,
            "type": self.code,
        }
        if self.details:
            error["details"] = self.details
        return {"error": error}


class UnknownProviderError(GatewayError):
    """Requested a provider path segment that is not registered."""

    status_code = 404
    code = "unknown_provider"


class ProviderNotConfiguredError(GatewayError):
    """Provider is known but cannot be used (e.g. Azure endpoint missing)."""

    status_code = 503
    code = "provider_not_configured"


class AuthenticationError(GatewayError):
    """Client did not present a valid gateway token while auth is enforced."""

    status_code = 401
    code = "authentication_error"


class RequestTooLargeError(GatewayError):
    """The client request body exceeded the configured size limit."""

    status_code = 413
    code = "request_too_large"


class UpstreamConnectionError(GatewayError):
    """The gateway could not reach the upstream provider."""

    status_code = 502
    code = "upstream_connection_error"


class UpstreamTimeoutError(GatewayError):
    """The upstream provider did not respond within the configured budget."""

    status_code = 504
    code = "upstream_timeout"


class SearchUnavailableError(GatewayError):
    """Vector search was requested but is not configured or not ready."""

    status_code = 503
    code = "search_unavailable"


class ServiceOverloadedError(GatewayError):
    """In-flight request cap reached; the client should retry shortly."""

    status_code = 503
    code = "service_overloaded"

    def __init__(self, message: str, *, retry_after_s: int = 1) -> None:
        super().__init__(message)
        self.headers = {"Retry-After": str(retry_after_s)}


class RateLimitExceededError(GatewayError):
    """The client exceeded its per-client request-rate budget."""

    status_code = 429
    code = "rate_limit_exceeded"

    def __init__(self, message: str, *, retry_after_s: float = 1.0) -> None:
        super().__init__(message)
        # Round up so a client never retries a hair too early.
        self.headers = {"Retry-After": str(max(1, math.ceil(retry_after_s)))}
