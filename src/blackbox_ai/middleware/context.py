"""Request-scoped context derived from sovereign metadata headers.

The gateway never asks developers to write logging code. Instead it reads a tiny
set of optional HTTP headers and threads their values through both structured
logs and the captured Intent Document:

================  =========================  ==============================
Header            Field                      Purpose
================  =========================  ==============================
X-Project-ID      project_id                 Group telemetry by repo/service
X-Agent-Session   session_id                 Tie one autonomous run together
X-Developer-ID    developer_id               Attribute cost/usage per engineer
X-Request-ID      request_id                  Correlate a single hop (generated
                                             if the client did not supply one)
================  =========================  ==============================
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "DEVELOPER_HEADER",
    "PROJECT_HEADER",
    "REQUEST_ID_HEADER",
    "SESSION_HEADER",
    "RequestContext",
    "RequestContextMiddleware",
    "current_context",
]

PROJECT_HEADER = "x-project-id"
SESSION_HEADER = "x-agent-session"
DEVELOPER_HEADER = "x-developer-id"
REQUEST_ID_HEADER = "x-request-id"

# Conservative security headers added to every response. CSP/HSTS are omitted on
# purpose: this is a JSON/SSE API (no HTML to frame or script) and HSTS depends
# on a TLS terminator the gateway can't assume. Only added when absent so a
# relayed upstream response that already sets one is left untouched.
_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Immutable snapshot of the metadata describing one request."""

    request_id: str
    project_id: str | None = None
    session_id: str | None = None
    developer_id: str | None = None

    def as_log_fields(self) -> dict[str, str]:
        """Non-null fields suitable for binding to the logger."""
        fields = {"request_id": self.request_id}
        if self.project_id:
            fields["project_id"] = self.project_id
        if self.session_id:
            fields["session_id"] = self.session_id
        if self.developer_id:
            fields["developer_id"] = self.developer_id
        return fields


def current_context(scope: Scope) -> RequestContext:
    """Extract a :class:`RequestContext` from an ASGI scope's state."""
    state = scope.get("state", {})
    context = state.get("request_context")
    if isinstance(context, RequestContext):
        return context
    # Defensive fallback; middleware should always have populated this.
    return RequestContext(request_id=str(uuid.uuid4()))


def _header(headers: dict[bytes, bytes], name: str) -> str | None:
    raw = headers.get(name.encode("latin-1"))
    if raw is None:
        return None
    value = raw.decode("latin-1").strip()
    return value or None


class RequestContextMiddleware:
    """Pure-ASGI middleware that builds the context and binds structlog vars.

    Implemented at the ASGI layer (rather than Starlette's BaseHTTPMiddleware)
    so it never buffers the streaming response body. It also stamps the response
    with ``X-Request-ID`` for end-to-end correlation.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        request_id = _header(headers, REQUEST_ID_HEADER) or str(uuid.uuid4())
        context = RequestContext(
            request_id=request_id,
            project_id=_header(headers, PROJECT_HEADER),
            session_id=_header(headers, SESSION_HEADER),
            developer_id=_header(headers, DEVELOPER_HEADER),
        )

        scope.setdefault("state", {})
        scope["state"]["request_context"] = context

        structlog.contextvars.bind_contextvars(**context.as_log_fields())

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw_headers = list(message.get("headers") or [])
                raw_headers.append(
                    (REQUEST_ID_HEADER.encode("latin-1"), request_id.encode("latin-1"))
                )
                present = {name.lower() for name, _ in raw_headers}
                raw_headers.extend(
                    (name, value) for name, value in _SECURITY_HEADERS if name not in present
                )
                message = {**message, "headers": raw_headers}
            await send(message)

        try:
            await self._app(scope, receive, send_with_request_id)
        finally:
            structlog.contextvars.unbind_contextvars(*context.as_log_fields())
