"""Canonical request hashing for the exact-match cache.

The cache key is a stable SHA-256 over the provider, HTTP method, upstream path,
and a canonicalised request body. Canonicalisation drops the ``stream`` flag (so
a streamed and non-streamed request to the same model share a *logical* key) and
sorts object keys so semantically identical JSON hashes identically regardless of
field order.

Format mismatches are handled by the store, not the key: the entry's identity is
``(cache_key, streamed)``, so a non-streamed response is never replayed to a
streaming client (or vice versa).

Only JSON-object bodies are cacheable (the shape of every LLM generation call).
Anything else returns ``None`` - "not cacheable" - which the relay treats as a
plain pass-through.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

__all__ = ["CacheIdentity", "canonical_request_key"]

# Request-body fields that do not change the generated content and so are
# excluded from the key. ``stream`` only affects wire format (handled via the
# store's per-format identity), not the content itself.
_VOLATILE_FIELDS = frozenset({"stream"})


@dataclass(frozen=True, slots=True)
class CacheIdentity:
    """What uniquely identifies a cached response.

    ``key`` is the content hash (stream-agnostic); ``streamed`` is the wire
    format the entry holds. Pairing them in one type makes the format-safety
    invariant unforgeable: a streamed response can never be looked up - or
    replayed - as a non-streamed one, because identity is the pair, not the key.
    """

    key: str
    streamed: bool


def canonical_request_key(provider: str, method: str, endpoint: str, body: bytes) -> str | None:
    """Return a deterministic cache key, or ``None`` if not cacheable."""
    parsed = _parse_json_object(body)
    if parsed is None:
        return None
    normalized = {k: v for k, v in parsed.items() if k not in _VOLATILE_FIELDS}
    body_repr = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    material = "\x1f".join((provider, method.upper(), endpoint.lstrip("/"), body_repr))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _parse_json_object(body: bytes) -> dict[str, Any] | None:
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
