"""The exact-match cache collection: lookup, put, and TTL management.

Cache entries are identified by ``(cache_key, streamed)`` so a stored response is
only ever replayed to a client expecting the same wire format. Entries expire via
a MongoDB TTL index on ``expires_at``; the lookup additionally filters on
``expires_at > now`` to avoid serving an entry in the brief window before the TTL
monitor reaps it.

When Queryable Encryption is enabled, ``response_body`` and ``request_payload``
are encrypted transparently by the underlying client; the lookup key, routing
metadata, and ``expires_at`` stay in plaintext so lookups and the TTL index keep
working.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.collection import AsyncCollection

from blackbox_ai.cache.keys import CacheIdentity

__all__ = ["CacheEntry", "CacheStore"]


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """A replayable cached upstream response."""

    cache_key: str
    streamed: bool
    provider: str
    endpoint: str
    status_code: int
    content_type: str | None
    response_body: bytes
    created_at: datetime
    expires_at: datetime


class CacheStore:
    """CRUD over the cache collection with TTL semantics."""

    def __init__(self, collection: AsyncCollection[dict[str, Any]], *, ttl_s: int) -> None:
        self._collection = collection
        self._ttl_s = ttl_s

    async def ensure_indexes(self) -> None:
        """Create the lookup and TTL indexes (idempotent)."""
        await self._collection.create_index(
            [("cache_key", ASCENDING), ("streamed", ASCENDING)],
            name="cache_key_streamed",
        )
        # expireAfterSeconds=0 means "expire exactly at the expires_at instant".
        await self._collection.create_index(
            [("expires_at", ASCENDING)],
            name="cache_ttl",
            expireAfterSeconds=0,
        )

    async def lookup(self, identity: CacheIdentity) -> CacheEntry | None:
        """Return a live (non-expired) entry for the identity, or ``None``."""
        now = datetime.now(UTC)
        doc = await self._collection.find_one(
            {
                "cache_key": identity.key,
                "streamed": identity.streamed,
                "expires_at": {"$gt": now},
            },
            sort=[("created_at", DESCENDING)],
        )
        if doc is None:
            return None
        return _entry_from_doc(doc)

    async def put(
        self,
        identity: CacheIdentity,
        *,
        provider: str,
        endpoint: str,
        status_code: int,
        content_type: str | None,
        response_body: bytes,
        request_payload: dict[str, Any] | None,
    ) -> None:
        """Insert a cache entry that expires after the configured TTL."""
        now = datetime.now(UTC)
        document: dict[str, Any] = {
            "cache_key": identity.key,
            "streamed": identity.streamed,
            "provider": provider,
            "endpoint": endpoint,
            "status_code": status_code,
            "content_type": content_type,
            "response_body": response_body,
            "created_at": now,
            "expires_at": now + timedelta(seconds=self._ttl_s),
        }
        # Omit a null request_payload: under QE this is an encrypted field and
        # automatic encryption refuses to encrypt null.
        if request_payload is not None:
            document["request_payload"] = request_payload
        await self._collection.insert_one(document)


def _entry_from_doc(doc: dict[str, Any]) -> CacheEntry:
    body = doc["response_body"]
    return CacheEntry(
        cache_key=doc["cache_key"],
        streamed=bool(doc.get("streamed", False)),
        provider=doc.get("provider", ""),
        endpoint=doc.get("endpoint", ""),
        status_code=int(doc.get("status_code", 200)),
        content_type=doc.get("content_type"),
        response_body=bytes(body),
        created_at=doc["created_at"],
        expires_at=doc["expires_at"],
    )
