"""Shared construction of Phase 4 components for the server and the CLI.

Centralising this keeps the FastAPI lifespan and the ``blackbox-ai`` CLI
(``init`` / ``search``) in lock-step: the same embedder, the same encryption
posture, and the same storage bootstrap (encrypted collections, indexes, TTL,
vector index).
"""

from __future__ import annotations

from typing import Any

from pymongo import AsyncMongoClient

from blackbox_ai.cache.store import CacheStore
from blackbox_ai.config import EmbeddingsProvider, Settings
from blackbox_ai.db.indexes import ensure_indexes
from blackbox_ai.db.search_indexes import ensure_search_index, ensure_vector_index
from blackbox_ai.logging import get_logger
from blackbox_ai.security.encryption import EncryptionManager
from blackbox_ai.telemetry.embeddings import Embedder, NullEmbedder, VoyageEmbedder

__all__ = ["build_embedder", "build_encryption_manager", "ensure_storage"]

_log = get_logger("blackbox_ai.bootstrap")


def build_embedder(settings: Settings) -> Embedder:
    """Return the configured embedder, or a no-op when disabled/unconfigured."""
    if settings.embeddings_provider is EmbeddingsProvider.VOYAGE and settings.voyage_api_key:
        key = settings.voyage_api_key.get_secret_value()
        if key:
            return VoyageEmbedder(
                api_key=key,
                model=settings.embedding_model,
                dims=settings.embedding_dims,
                breaker_threshold=settings.embedding_breaker_threshold,
                breaker_cooldown_s=settings.embedding_breaker_cooldown_s,
            )
    return NullEmbedder()


def build_encryption_manager(settings: Settings) -> EncryptionManager | None:
    """Build the encryption manager when QE is enabled (fail-closed).

    Returns ``None`` when encryption is disabled. When enabled, construction
    validates the key and ``crypt_shared`` path and raises ``ValueError`` on a
    misconfiguration - callers should let this abort startup.
    """
    if not settings.encryption_enabled:
        return None
    return EncryptionManager(settings)


async def ensure_storage(
    settings: Settings,
    *,
    admin_client: AsyncMongoClient[dict[str, Any]],
    encryption: EncryptionManager | None,
    embedder: Embedder,
    wait_vector: bool = False,
) -> None:
    """Ensure encrypted collections, indexes, TTL, and the vector index exist.

    All operations target plaintext metadata only, so the plain ``admin_client``
    is sufficient (and avoids any auto-encryption overhead during setup). Index
    creation is idempotent; vector index creation is best-effort.
    """
    if encryption is not None:
        await encryption.ensure_encrypted_collections(admin_client)

    database = admin_client[settings.mongo_db]
    intents = database[settings.mongo_collection]
    await ensure_indexes(intents)

    if settings.cache_enabled:
        cache = CacheStore(database[settings.cache_collection], ttl_s=settings.cache_ttl_s)
        await cache.ensure_indexes()

    if embedder.dims > 0:
        vector_result = await ensure_vector_index(
            intents,
            name=settings.vector_index_name,
            dims=settings.embedding_dims,
            wait=wait_vector,
        )
        _log.info("vector_index_ensured", result=repr(vector_result))
        # Full-text index powers hybrid search ($rankFusion). Best-effort: a
        # cluster without Atlas Search simply runs vector-only search.
        text_result = await ensure_search_index(
            intents,
            name=settings.search_index_name,
            wait=wait_vector,
        )
        _log.info("text_index_ensured", result=repr(text_result))
