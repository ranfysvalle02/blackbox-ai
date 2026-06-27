"""Async MongoDB client construction.

Uses PyMongo's native async API (``AsyncMongoClient``), the officially supported
replacement for the now-deprecated Motor driver. A single client is created at
startup and reused for the process lifetime - never per request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pymongo import AsyncMongoClient

from blackbox_ai.config import Settings

if TYPE_CHECKING:
    from pymongo.encryption_options import AutoEncryptionOpts

__all__ = ["create_client", "ping"]


def create_client(
    settings: Settings,
    *,
    auto_encryption_opts: AutoEncryptionOpts | None = None,
) -> AsyncMongoClient[dict[str, Any]]:
    """Create a pooled async MongoDB client.

    Pool/timeout rationale (long-running write-heavy telemetry server; writes
    originate only from a small, bounded worker pool, so DB-side concurrency is
    low):

    * ``maxPoolSize`` / ``minPoolSize`` - sized to the telemetry worker count
      with headroom; a couple of pre-warmed connections avoid first-flush
      latency. Raise these together with ``telemetry_workers``.
    * ``serverSelectionTimeoutMS`` = 5s - fail fast so readiness checks and the
      first write surface an unreachable database quickly instead of hanging.
    * ``connectTimeoutMS`` = 10s - bounded TCP/TLS establishment.
    * ``socketTimeoutMS`` = 30s - telemetry inserts are short; prevents a hung
      socket from pinning a worker indefinitely.

    When ``auto_encryption_opts`` is provided the client transparently encrypts
    and decrypts the configured fields via MongoDB Queryable Encryption.
    """
    return AsyncMongoClient(
        settings.mongo_uri,
        appname="blackbox-ai",
        tz_aware=True,
        maxPoolSize=settings.mongo_max_pool_size,
        minPoolSize=settings.mongo_min_pool_size,
        serverSelectionTimeoutMS=5_000,
        connectTimeoutMS=10_000,
        socketTimeoutMS=30_000,
        retryWrites=True,
        auto_encryption_opts=auto_encryption_opts,
    )


async def ping(client: AsyncMongoClient[dict[str, Any]]) -> None:
    """Issue an ``admin.ping``; raises ``PyMongoError`` if unreachable."""
    await client.admin.command("ping")
