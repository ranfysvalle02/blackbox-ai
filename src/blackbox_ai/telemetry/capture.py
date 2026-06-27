"""Out-of-band capture buffer for the data plane.

The relay feeds streamed response bytes to a :class:`CaptureBuffer` as they fly
past to the client. The buffer only appends bytes (bounded by a cap) and records
timing - operations that cannot meaningfully fail - keeping the request path
safe. When the stream ends, the relay calls :meth:`finalize` to produce an
immutable :class:`RawCapture` envelope that is handed to the telemetry pipeline.
All expensive, fallible parsing happens later, in a worker.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from blackbox_ai.middleware.context import RequestContext
from blackbox_ai.telemetry.models import CaptureStatus

if TYPE_CHECKING:
    from starlette.requests import Request

    from blackbox_ai.providers.base import ProviderConfig

__all__ = ["CaptureBuffer", "RawCapture"]


@dataclass(frozen=True, slots=True)
class RawCapture:
    """Immutable record of one relayed interaction, awaiting parsing."""

    request_id: str
    context: RequestContext
    provider: str
    parser_name: str
    method: str
    endpoint: str
    query_string: str
    timestamp: datetime
    request_body: bytes
    response_body: bytes
    response_content_type: str | None
    http_status: int | None
    streamed: bool
    latency_ms: float | None
    ttft_ms: float | None
    status: CaptureStatus
    error: str | None
    response_truncated: bool
    # --- Cache linkage (Phase 4) -------------------------------------------
    cache_key: str | None = None
    cache_requested: bool = False
    served_from_cache: bool = False


@dataclass(slots=True)
class CaptureBuffer:
    """Accumulates response bytes and timing for a single in-flight request."""

    request_id: str
    context: RequestContext
    provider: str
    parser_name: str
    method: str
    endpoint: str
    query_string: str
    request_body: bytes
    max_capture_bytes: int

    # --- Cache linkage (Phase 4) -------------------------------------------
    cache_key: str | None = None
    cache_requested: bool = False
    served_from_cache: bool = False

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    _start_monotonic: float = field(default_factory=time.monotonic)
    _first_chunk_monotonic: float | None = None
    _end_monotonic: float | None = None
    _response: bytearray = field(default_factory=bytearray)
    _truncated: bool = False

    @classmethod
    def for_request(
        cls,
        *,
        context: RequestContext,
        provider: ProviderConfig,
        request: Request,
        upstream_path: str,
        body: bytes,
        max_bytes: int,
        cache_key: str | None = None,
        cache_requested: bool = False,
        served_from_cache: bool = False,
    ) -> CaptureBuffer:
        """Build a buffer from the request-path objects the relay already holds.

        A single construction site keeps the many capture fields in lock-step:
        adding one here updates every caller (live relay and cache replay) at
        once, instead of being silently forgotten in one of them.
        """
        return cls(
            request_id=context.request_id,
            context=context,
            provider=provider.name,
            parser_name=provider.parser_name,
            method=request.method,
            endpoint=upstream_path,
            query_string=request.url.query,
            request_body=body,
            max_capture_bytes=max_bytes,
            cache_key=cache_key,
            cache_requested=cache_requested,
            served_from_cache=served_from_cache,
        )

    def observe(self, chunk: bytes) -> None:
        """Record a streamed chunk. Pure and non-raising by design."""
        if self._first_chunk_monotonic is None:
            self._first_chunk_monotonic = time.monotonic()
        remaining = self.max_capture_bytes - len(self._response)
        if remaining <= 0:
            self._truncated = True
            return
        if len(chunk) > remaining:
            self._response.extend(chunk[:remaining])
            self._truncated = True
        else:
            self._response.extend(chunk)

    def finalize(
        self,
        *,
        http_status: int | None,
        response_content_type: str | None,
        streamed: bool,
        status: CaptureStatus = CaptureStatus.OK,
        error: str | None = None,
    ) -> RawCapture:
        """Freeze the accumulated state into a :class:`RawCapture`."""
        self._end_monotonic = time.monotonic()
        latency_ms = (self._end_monotonic - self._start_monotonic) * 1000.0
        ttft_ms = (
            (self._first_chunk_monotonic - self._start_monotonic) * 1000.0
            if self._first_chunk_monotonic is not None
            else None
        )
        return RawCapture(
            request_id=self.request_id,
            context=self.context,
            provider=self.provider,
            parser_name=self.parser_name,
            method=self.method,
            endpoint=self.endpoint,
            query_string=self.query_string,
            timestamp=self.timestamp,
            request_body=self.request_body,
            response_body=bytes(self._response),
            response_content_type=response_content_type,
            http_status=http_status,
            streamed=streamed,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            status=status,
            error=error,
            response_truncated=self._truncated,
            cache_key=self.cache_key,
            cache_requested=self.cache_requested,
            served_from_cache=self.served_from_cache,
        )
