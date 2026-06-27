"""Shared admin-token verification for protected API routes.

Centralises the timing-safe ``GATEWAY_ADMIN_TOKEN`` check so the admin search
API and the (optionally protected) metrics endpoint authorise identically.
"""

from __future__ import annotations

import hmac

from fastapi import Request

from blackbox_ai.config import Settings
from blackbox_ai.errors import AuthenticationError, SearchUnavailableError
from blackbox_ai.providers.base import ProviderConfig

__all__ = ["ADMIN_TOKEN_HEADER", "require_admin_token"]

ADMIN_TOKEN_HEADER = "x-admin-token"


def require_admin_token(request: Request, settings: Settings) -> None:
    """Authorize a request against ``GATEWAY_ADMIN_TOKEN`` (constant-time).

    Raises:
        SearchUnavailableError: (503) when no admin token is configured.
        AuthenticationError: (401) when the presented token is missing or wrong.
    """
    configured_secret = settings.admin_token
    if configured_secret is None or not configured_secret.get_secret_value():
        raise SearchUnavailableError("Admin token is not configured (set GATEWAY_ADMIN_TOKEN).")
    configured = configured_secret.get_secret_value()
    presented = request.headers.get(ADMIN_TOKEN_HEADER)
    if presented is None:
        header = request.headers.get("authorization")
        presented = ProviderConfig.strip_bearer(header) if header else None
    if presented is None or not hmac.compare_digest(presented, configured):
        raise AuthenticationError("Invalid or missing admin token.")
