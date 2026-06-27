"""The cache gate: the data plane's one and only door to the token cache.

The relay does not know how cache keys are derived, how opt-in is resolved, or
how a lookup is time-bounded - it asks the gate for a :class:`CacheIdentity` and,
on a hit, a :class:`CacheEntry`. Concentrating the whole decision here keeps the
relay focused on relaying and makes the cache an independently testable (and
deletable) feature.

When no store is configured the gate is inert: :meth:`identity_for` returns
``None`` and the relay behaves as a plain pass-through. The lookup is
time-bounded and fail-open by contract, so a slow or unreachable cache never
delays or breaks the request path.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from pymongo.errors import PyMongoError
from starlette.requests import Request

from blackbox_ai.cache.keys import CacheIdentity, canonical_request_key
from blackbox_ai.cache.store import CacheEntry, CacheStore
from blackbox_ai.logging import get_logger
from blackbox_ai.providers.base import ProviderConfig

__all__ = ["CACHE_HEADER", "CacheGate", "CachePolicy"]

_log = get_logger("blackbox_ai.cache")

# Per-request opt-in header, also echoed on responses as HIT / MISS.
CACHE_HEADER = "x-intent-cache"
_CACHE_ON_VALUES = frozenset({"on", "true", "1", "yes"})
_CACHE_OFF_VALUES = frozenset({"off", "false", "0", "no"})


@dataclass(frozen=True, slots=True)
class CachePolicy:
    """The narrow slice of configuration the cache gate actually needs."""

    default_on: bool
    lookup_timeout_s: float


class CacheGate:
    """Resolves cache identity and performs fail-open lookups for the relay."""

    def __init__(self, store: CacheStore | None, policy: CachePolicy) -> None:
        self._store = store
        self._policy = policy

    @property
    def enabled(self) -> bool:
        return self._store is not None

    def identity_for(
        self, request: Request, provider: ProviderConfig, upstream_path: str, body: bytes
    ) -> CacheIdentity | None:
        """Return the cache identity when this request is cacheable and opted in."""
        if self._store is None or request.method.upper() != "POST":
            return None
        if not self._opted_in(request):
            return None
        key = canonical_request_key(provider.name, request.method, upstream_path, body)
        if key is None:
            return None
        return CacheIdentity(key=key, streamed=_wants_stream(body, upstream_path))

    def _opted_in(self, request: Request) -> bool:
        """Resolve opt-in: an explicit header wins, else the configured default."""
        header = request.headers.get(CACHE_HEADER)
        if header is not None:
            value = header.strip().lower()
            if value in _CACHE_ON_VALUES:
                return True
            if value in _CACHE_OFF_VALUES:
                return False
        return self._policy.default_on

    async def lookup(self, identity: CacheIdentity) -> CacheEntry | None:
        """Time-bounded, fail-open cache lookup; never blocks the data plane."""
        if self._store is None:
            return None
        try:
            return await asyncio.wait_for(
                self._store.lookup(identity), timeout=self._policy.lookup_timeout_s
            )
        except TimeoutError:
            _log.warning("cache_lookup_timeout", cache_key=identity.key)
            return None
        except PyMongoError as exc:
            _log.warning("cache_lookup_failed", error=str(exc))
            return None


def _wants_stream(body: bytes, upstream_path: str) -> bool:
    """Infer the wire format the client expects, for safe cache replay.

    Most providers signal streaming via ``"stream": true`` in the JSON body;
    Gemini signals it in the path (``:streamGenerateContent``). The cached
    entry's format must match so an SSE body is never replayed to a client that
    asked for a single JSON document (or vice versa).
    """
    if "streamgeneratecontent" in upstream_path.lower():
        return True
    if not body:
        return False
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return False
    return isinstance(parsed, dict) and bool(parsed.get("stream"))
