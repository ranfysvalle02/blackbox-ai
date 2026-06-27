"""Provider configuration primitives.

A :class:`ProviderConfig` is pure data describing how the generic relay should
forward to a single backend: where it lives, how to authenticate, and which
telemetry parser understands its wire format. Keeping this free of any HTTP or
database concerns is what lets "add a provider" mean "add one config entry".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

__all__ = ["AuthScheme", "ProviderConfig"]


class AuthScheme(StrEnum):
    """How a provider expects its API credential to be presented."""

    BEARER = "bearer"  # Authorization: Bearer <key>
    HEADER = "header"  # <auth_param>: <key>
    QUERY = "query"  # ?<auth_param>=<key>
    NONE = "none"  # keyless (e.g. local Ollama)


_AUTHORIZATION = "authorization"
_BEARER_PREFIX = "Bearer "


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Immutable description of one upstream provider.

    Attributes:
        name: Path segment that selects this provider (``/{name}/...``).
        upstream_base_url: Scheme + host (+ optional base path) of the backend.
        auth_scheme: Where/how the credential is carried.
        auth_param: Header name (HEADER) or query parameter (QUERY); unused for
            BEARER (always ``Authorization``) and NONE.
        api_key: Sovereign credential injected by the gateway. When ``None`` the
            gateway transparently forwards whatever credential the client sent.
        parser_name: Key identifying the telemetry parser for this wire format.
    """

    name: str
    upstream_base_url: str
    auth_scheme: AuthScheme
    parser_name: str
    auth_param: str = ""
    api_key: str | None = None

    @property
    def auth_header_name(self) -> str | None:
        """Header that carries the credential, if any (lower-cased)."""
        if self.auth_scheme is AuthScheme.BEARER:
            return _AUTHORIZATION
        if self.auth_scheme is AuthScheme.HEADER:
            return self.auth_param.lower()
        return None

    @property
    def auth_query_param(self) -> str | None:
        """Query parameter that carries the credential, if any."""
        return self.auth_param if self.auth_scheme is AuthScheme.QUERY else None

    def format_credential_header(self, value: str) -> str:
        """Format a raw key for placement in the auth header."""
        if self.auth_scheme is AuthScheme.BEARER:
            return f"{_BEARER_PREFIX}{value}"
        return value

    @staticmethod
    def strip_bearer(value: str) -> str:
        """Remove a ``Bearer`` prefix if present, returning the raw token."""
        if value.startswith(_BEARER_PREFIX):
            return value[len(_BEARER_PREFIX) :]
        return value

    def build_upstream_url(self, upstream_path: str, query_string: str) -> str:
        """Join the backend base URL with the client-supplied path and query.

        The base URL may itself contain a path prefix; both are concatenated and
        normalised so duplicate slashes never appear.
        """
        base = urlsplit(self.upstream_base_url)
        base_path = base.path.rstrip("/")
        suffix = upstream_path.lstrip("/")
        full_path = f"{base_path}/{suffix}" if suffix else base_path or "/"
        return urlunsplit((base.scheme, base.netloc, full_path, query_string, ""))
