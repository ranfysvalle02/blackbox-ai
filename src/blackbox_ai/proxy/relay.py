"""The generic, fail-open streaming relay - the entire data plane.

One handler serves every provider. It rebuilds the upstream URL from the
provider config and the client-supplied path, swaps in sovereign credentials,
streams the response straight back, and mirrors a copy to the telemetry pipeline
out-of-band. Nothing on this path depends on parsing succeeding or the database
being reachable: telemetry failures are isolated to the worker pool.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode

import httpx
from starlette.datastructures import Headers as StarletteHeaders
from starlette.requests import Request
from starlette.responses import StreamingResponse

from blackbox_ai.cache.gate import CACHE_HEADER, CacheGate
from blackbox_ai.cache.store import CacheEntry
from blackbox_ai.config import Settings
from blackbox_ai.errors import (
    AuthenticationError,
    RateLimitExceededError,
    RequestTooLargeError,
    ServiceOverloadedError,
    UpstreamConnectionError,
    UpstreamTimeoutError,
)
from blackbox_ai.logging import get_logger
from blackbox_ai.metrics import (
    RELAY_INFLIGHT,
    RELAY_LATENCY,
    RELAY_RATE_LIMITED,
    RELAY_REJECTED,
    RELAY_REQUESTS,
    RELAY_TTFT,
)
from blackbox_ai.middleware.context import RequestContext
from blackbox_ai.providers.base import AuthScheme, ProviderConfig
from blackbox_ai.proxy.tee import tee_stream
from blackbox_ai.security.rate_limit import RateLimiter, SlidingWindowRateLimiter
from blackbox_ai.telemetry.capture import CaptureBuffer, RawCapture
from blackbox_ai.telemetry.models import CaptureStatus
from blackbox_ai.telemetry.pipeline import TelemetryPipeline

__all__ = ["Relay"]

_log = get_logger("blackbox_ai.relay")

# Hop-by-hop headers (RFC 9110) plus framing/encoding headers the gateway must
# regenerate. accept-encoding is stripped so upstreams reply uncompressed, which
# keeps captured bytes directly parseable and avoids re-encoding on the way out.
_DROP_REQUEST_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "accept-encoding",
        "x-gateway-token",
    }
)
_DROP_RESPONSE_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "transfer-encoding",
        "content-length",
        "content-encoding",
        "trailer",
        "upgrade",
    }
)
_STREAMING_CONTENT_TYPES = ("text/event-stream", "x-ndjson", "application/stream+json")
_GATEWAY_TOKEN_HEADER = "x-gateway-token"
_TOO_LARGE_MSG = "Request body exceeds the {limit}-byte limit."


class Relay:
    """Forwards requests to providers and tees responses to telemetry."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        pipeline: TelemetryPipeline,
        settings: Settings,
        cache: CacheGate,
    ) -> None:
        self._client = client
        self._pipeline = pipeline
        self._settings = settings
        self._cache = cache
        # In-flight concurrency gate. The event loop is single-threaded, so the
        # check-then-increment in handle() is race-free (no await between them).
        self._max_concurrent = settings.max_concurrent_requests
        self._inflight = 0
        self._rate_limiter: RateLimiter | None = (
            SlidingWindowRateLimiter(settings.rate_limit_requests, settings.rate_limit_window_s)
            if settings.rate_limit_enabled
            else None
        )

    async def handle(
        self,
        request: Request,
        provider: ProviderConfig,
        upstream_path: str,
        context: RequestContext,
    ) -> StreamingResponse:
        """Relay a single request to ``provider`` and stream the response."""
        self._authenticate(request, provider)
        self._check_rate_limit(request, provider)
        # Acquire an in-flight permit before buffering the body, so the body and
        # capture memory are bounded by the cap. The permit is released exactly
        # once: by the streaming generator if we hand off a response, otherwise
        # by this method's `finally` on any early-exit error.
        self._acquire(provider)
        handed_off = False
        try:
            body = await self._read_body(request)

            # --- Cache lookup (opt-in, time-bounded, fail-open) -------------
            identity = self._cache.identity_for(request, provider, upstream_path, body)
            if identity is not None:
                hit = await self._cache.lookup(identity)
                if hit is not None:
                    replayed = self._replay_from_cache(
                        hit, context, provider, request, upstream_path, body
                    )
                    handed_off = True
                    return replayed

            url = self._build_url(provider, upstream_path, request.url.query)
            headers = self._prepare_request_headers(request.headers, provider)

            upstream_request = self._client.build_request(
                request.method, url, headers=headers, content=body
            )
            response = await self._send(upstream_request, provider)

            content_type = response.headers.get("content-type")
            streamed = _is_streaming_response(response)
            buffer = CaptureBuffer.for_request(
                context=context,
                provider=provider,
                request=request,
                upstream_path=upstream_path,
                body=body,
                max_bytes=self._settings.telemetry_max_capture_bytes,
                cache_key=identity.key if identity is not None else None,
                cache_requested=identity is not None,
            )
            initial_status = (
                CaptureStatus.OK if response.status_code < 400 else CaptureStatus.UPSTREAM_ERROR
            )

            out_headers = self._sanitize_response_headers(response.headers)
            if identity is not None:
                out_headers[CACHE_HEADER] = "MISS"

            streaming = StreamingResponse(
                self._stream_and_capture(response, buffer, content_type, streamed, initial_status),
                status_code=response.status_code,
                headers=out_headers,
                media_type=content_type,
            )
            handed_off = True
            return streaming
        finally:
            if not handed_off:
                self._release()

    def _acquire(self, provider: ProviderConfig) -> None:
        """Take an in-flight permit, or reject with 503 when at capacity."""
        if self._inflight >= self._max_concurrent:
            RELAY_REJECTED.labels(provider.name).inc()
            _log.warning("relay_overloaded", provider=provider.name, inflight=self._inflight)
            raise ServiceOverloadedError("Gateway at capacity; please retry shortly.")
        self._inflight += 1
        RELAY_INFLIGHT.inc()

    def _release(self) -> None:
        """Return an in-flight permit. Called exactly once per acquired request."""
        self._inflight -= 1
        RELAY_INFLIGHT.dec()

    def _record_metrics(self, capture: RawCapture, cache_label: str) -> None:
        """Update request-path Prometheus instruments from a finalized capture."""
        status = str(capture.http_status) if capture.http_status is not None else "error"
        RELAY_REQUESTS.labels(capture.provider, capture.method, status, cache_label).inc()
        if capture.latency_ms is not None:
            RELAY_LATENCY.labels(capture.provider).observe(capture.latency_ms / 1000.0)
        if capture.ttft_ms is not None:
            RELAY_TTFT.labels(capture.provider).observe(capture.ttft_ms / 1000.0)

    async def _read_body(self, request: Request) -> bytes:
        """Read the request body, rejecting anything over the configured cap.

        The data plane must buffer the whole body to forward and tee it, so an
        unbounded read is a memory-exhaustion vector. We refuse an oversized
        ``Content-Length`` up front, then enforce the same ceiling while reading
        in case the header lies or is absent (chunked uploads).
        """
        limit = self._settings.max_request_bytes
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise RequestTooLargeError(_TOO_LARGE_MSG.format(limit=limit))
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise RequestTooLargeError(_TOO_LARGE_MSG.format(limit=limit))
            chunks.append(chunk)
        return b"".join(chunks)

    async def _send(
        self, upstream_request: httpx.Request, provider: ProviderConfig
    ) -> httpx.Response:
        try:
            return await self._client.send(upstream_request, stream=True)
        except httpx.TimeoutException as exc:
            _log.warning("upstream_timeout", provider=provider.name, error=str(exc))
            raise UpstreamTimeoutError(f"Upstream provider '{provider.name}' timed out.") from exc
        except httpx.HTTPError as exc:
            _log.warning("upstream_connection_error", provider=provider.name, error=str(exc))
            raise UpstreamConnectionError(
                f"Could not reach upstream provider '{provider.name}'."
            ) from exc

    async def _stream_and_capture(
        self,
        response: httpx.Response,
        buffer: CaptureBuffer,
        content_type: str | None,
        streamed: bool,
        initial_status: CaptureStatus,
    ) -> AsyncIterator[bytes]:
        status = initial_status
        error: str | None = None
        completed = False
        try:
            async for chunk in tee_stream(response.aiter_raw(), buffer.observe):
                yield chunk
            completed = True
        except httpx.HTTPError as exc:
            # Upstream broke mid-stream. Headers (200) are already sent, so we end
            # the stream gracefully and record the failure for telemetry.
            status = CaptureStatus.UPSTREAM_ERROR
            error = str(exc)
            _log.warning("upstream_stream_error", error=error)
        finally:
            await response.aclose()
            if not completed and status is CaptureStatus.OK:
                status = CaptureStatus.CLIENT_DISCONNECT
            capture = buffer.finalize(
                http_status=response.status_code,
                response_content_type=content_type,
                streamed=streamed,
                status=status,
                error=error,
            )
            self._record_metrics(capture, "MISS" if buffer.cache_requested else "OFF")
            # Non-blocking, fail-open: a full queue drops the capture, never the
            # client's response.
            self._pipeline.submit(capture)
            self._release()

    # --- Cache replay ------------------------------------------------------
    def _replay_from_cache(
        self,
        entry: CacheEntry,
        context: RequestContext,
        provider: ProviderConfig,
        request: Request,
        upstream_path: str,
        body: bytes,
    ) -> StreamingResponse:
        """Serve a cached response and record the hit as telemetry."""
        _log.info("cache_hit", cache_key=entry.cache_key, provider=provider.name)
        buffer = CaptureBuffer.for_request(
            context=context,
            provider=provider,
            request=request,
            upstream_path=upstream_path,
            body=body,
            max_bytes=self._settings.telemetry_max_capture_bytes,
            cache_key=entry.cache_key,
            cache_requested=True,
            served_from_cache=True,
        )
        headers = {CACHE_HEADER: "HIT"}
        if entry.content_type:
            headers["content-type"] = entry.content_type
        return StreamingResponse(
            self._replay_and_capture(entry, buffer),
            status_code=entry.status_code,
            headers=headers,
            media_type=entry.content_type,
        )

    async def _replay_and_capture(
        self, entry: CacheEntry, buffer: CaptureBuffer
    ) -> AsyncIterator[bytes]:
        try:
            buffer.observe(entry.response_body)
            yield entry.response_body
        finally:
            capture = buffer.finalize(
                http_status=entry.status_code,
                response_content_type=entry.content_type,
                streamed=entry.streamed,
                status=CaptureStatus.OK,
            )
            self._record_metrics(capture, "HIT")
            self._pipeline.submit(capture)
            self._release()

    def _authenticate(self, request: Request, provider: ProviderConfig) -> None:
        if not self._settings.effective_require_auth:
            return
        token = self._extract_client_token(request, provider)
        if token is None or not _token_valid(token, self._settings.gateway_tokens):
            raise AuthenticationError("Invalid or missing gateway token.")

    def _check_rate_limit(self, request: Request, provider: ProviderConfig) -> None:
        """Enforce the per-client request-rate budget (fail-fast with 429)."""
        if self._rate_limiter is None:
            return
        key, scope = self._client_key(request, provider)
        decision = self._rate_limiter.check(key)
        if not decision.allowed:
            RELAY_RATE_LIMITED.labels(scope).inc()
            _log.warning("rate_limited", scope=scope, provider=provider.name)
            raise RateLimitExceededError(
                "Rate limit exceeded; slow down and retry shortly.",
                retry_after_s=decision.retry_after_s,
            )

    def _client_key(self, request: Request, provider: ProviderConfig) -> tuple[str, str]:
        """Identify the caller: the gateway token when auth is on, else client IP.

        Tokens are hashed so the limiter never holds raw secrets in memory.
        """
        if self._settings.effective_require_auth:
            token = self._extract_client_token(request, provider)
            if token:
                digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
                return f"tok:{digest}", "token"
        host = request.client.host if request.client else "unknown"
        return f"ip:{host}", "ip"

    @staticmethod
    def _extract_client_token(request: Request, provider: ProviderConfig) -> str | None:
        """Locate the client's gateway token without ever leaking it upstream.

        The dedicated ``x-gateway-token`` header is the primary, unambiguous
        source and is stripped from forwarded requests. When a sovereign key is
        configured, the gateway also accepts the token in the provider's
        credential slot (the drop-in SDK ergonomic) because that slot is
        overwritten with the real key before forwarding. In transparent
        passthrough mode (no sovereign key) that slot carries the client's *own*
        upstream credential, so it must NOT be read as the gateway token (nor
        forwarded as a gateway secret); the dedicated header is required.
        """
        explicit = request.headers.get(_GATEWAY_TOKEN_HEADER)
        if explicit:
            return explicit
        if provider.api_key is None:
            return None
        if provider.auth_scheme is AuthScheme.BEARER:
            header = request.headers.get("authorization")
            if header:
                return ProviderConfig.strip_bearer(header)
        elif provider.auth_scheme is AuthScheme.HEADER and provider.auth_param:
            value = request.headers.get(provider.auth_param)
            if value:
                return value
        elif provider.auth_scheme is AuthScheme.QUERY and provider.auth_param:
            value = request.query_params.get(provider.auth_param)
            if value:
                return value
        return None

    def _build_url(self, provider: ProviderConfig, upstream_path: str, query_string: str) -> str:
        if provider.auth_scheme is AuthScheme.QUERY and provider.api_key:
            query_string = _replace_query_param(query_string, provider.auth_param, provider.api_key)
        return provider.build_upstream_url(upstream_path, query_string)

    @staticmethod
    def _prepare_request_headers(
        client_headers: StarletteHeaders, provider: ProviderConfig
    ) -> httpx.Headers:
        headers = httpx.Headers(
            {k: v for k, v in client_headers.items() if k.lower() not in _DROP_REQUEST_HEADERS}
        )
        # Inject the sovereign credential only when one is configured; otherwise
        # forward whatever the client already sent (zero-config relay).
        if provider.api_key is None:
            return headers
        if provider.auth_scheme is AuthScheme.BEARER:
            headers["authorization"] = provider.format_credential_header(provider.api_key)
        elif provider.auth_scheme is AuthScheme.HEADER and provider.auth_param:
            headers[provider.auth_param] = provider.api_key
        # QUERY credentials are injected into the URL, not the headers.
        return headers

    @staticmethod
    def _sanitize_response_headers(response_headers: httpx.Headers) -> dict[str, str]:
        return {
            key: value
            for key, value in response_headers.items()
            if key.lower() not in _DROP_RESPONSE_HEADERS
        }


def _token_valid(token: str, valid: frozenset[str]) -> bool:
    """Timing-safe membership test: compare against every accepted token.

    ``hmac.compare_digest`` avoids the per-byte early-exit of ``==``/``in`` so a
    caller cannot probe a valid token byte by byte from response timing.
    """
    return any(hmac.compare_digest(token, candidate) for candidate in valid)


def _is_streaming_response(response: httpx.Response) -> bool:
    content_type = str(response.headers.get("content-type", "")).lower()
    if any(token in content_type for token in _STREAMING_CONTENT_TYPES):
        return True
    return str(response.headers.get("transfer-encoding", "")).lower() == "chunked"


def _replace_query_param(query_string: str, key: str, value: str) -> str:
    pairs = [(k, v) for k, v in parse_qsl(query_string, keep_blank_values=True) if k != key]
    pairs.append((key, value))
    return urlencode(pairs)
