"""Phase 4 integration tests: TTL cache, vector search recall, QE round trip.

All require a live MongoDB via ``MONGO_TEST_URI``; the vector test additionally
needs an Atlas-capable server (``mongodb-atlas-local``), and the QE test needs
the ``crypt_shared`` library via ``GATEWAY_CRYPT_SHARED_LIB_PATH``. Each is
skipped cleanly when its prerequisites are absent.

Run inside the compose stack (crypt_shared is baked into the image):

    docker compose exec gateway sh -lc \\
      'MONGO_TEST_URI="$GATEWAY_MONGO_URI" pytest -m integration tests/integration/test_phase4.py'
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from bson.binary import Binary
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

from blackbox_ai.bootstrap import ensure_storage
from blackbox_ai.cache.keys import CacheIdentity
from blackbox_ai.cache.store import CacheStore
from blackbox_ai.config import EmbeddingsProvider, Settings
from blackbox_ai.db.mongo import create_client, ping
from blackbox_ai.db.search_indexes import ensure_vector_index
from blackbox_ai.search import SearchMode, SearchService
from blackbox_ai.security.encryption import EncryptionManager, generate_local_key
from blackbox_ai.telemetry.embeddings import NullEmbedder
from blackbox_ai.telemetry.models import IntentDocument, IntentTelemetry
from blackbox_ai.telemetry.sink_mongo import MongoSink
from tests.conftest import default_settings

pytestmark = pytest.mark.integration


def _mongo_uri() -> str:
    uri = os.environ.get("MONGO_TEST_URI")
    if not uri:
        pytest.skip("MONGO_TEST_URI not set")
    return uri


@pytest_asyncio.fixture
async def mongo_client() -> AsyncIterator[AsyncMongoClient[dict[str, Any]]]:
    settings = default_settings(mongo_uri=_mongo_uri())
    client = create_client(settings)
    try:
        await ping(client)
    except PyMongoError:
        await client.close()
        pytest.skip("MongoDB not reachable at MONGO_TEST_URI")
    try:
        yield client
    finally:
        await client.close()


# --- TTL cache -------------------------------------------------------------


async def test_cache_store_round_trip_and_ttl_index(
    mongo_client: AsyncMongoClient[dict[str, Any]],
) -> None:
    coll = mongo_client["blackbox_ai_test"][f"cache_it_{uuid.uuid4().hex}"]
    store = CacheStore(coll, ttl_s=3600)
    try:
        await store.ensure_indexes()
        await store.put(
            CacheIdentity(key="k1", streamed=False),
            provider="openai",
            endpoint="v1/chat/completions",
            status_code=200,
            content_type="application/json",
            response_body=b'{"ok":true}',
            request_payload={"model": "gpt-4o"},
        )
        hit = await store.lookup(CacheIdentity(key="k1", streamed=False))
        assert hit is not None
        assert hit.response_body == b'{"ok":true}'
        # Format identity is part of the key: a streaming lookup must miss.
        assert await store.lookup(CacheIdentity(key="k1", streamed=True)) is None

        index_names = {idx["name"] async for idx in await coll.list_indexes()}
        assert "cache_ttl" in index_names
        assert "cache_key_streamed" in index_names
    finally:
        await coll.drop()


# --- Vector search recall --------------------------------------------------


class _StubEmbedder:
    """Returns a fixed query vector; used to drive deterministic recall."""

    model_name = "stub"
    dims = 4

    def __init__(self, query_vector: list[float]) -> None:
        self._query_vector = query_vector

    async def embed_documents(self, texts: list[str]) -> list[list[float] | None]:
        return [None for _ in texts]

    async def embed_query(self, text: str) -> list[float] | None:
        return self._query_vector


async def test_vector_search_recall(
    mongo_client: AsyncMongoClient[dict[str, Any]],
) -> None:
    coll = mongo_client["blackbox_ai_test"][f"intents_vec_{uuid.uuid4().hex}"]
    index_name = "intent_vector_index_it"
    docs = [
        {"request_id": "near", "embedding": [1.0, 0.0, 0.0, 0.0], "project_id": "p1"},
        {"request_id": "far", "embedding": [0.0, 1.0, 0.0, 0.0], "project_id": "p1"},
    ]
    await coll.insert_many(docs)
    result = await ensure_vector_index(coll, name=index_name, dims=4, wait=True, timeout_s=90.0)
    if not result.queryable:
        await coll.drop()
        pytest.skip(f"vector search not available (atlas-local required): {result.error}")

    service = SearchService(
        coll,
        _StubEmbedder([0.9, 0.1, 0.0, 0.0]),
        vector_index_name=index_name,
        search_index_name="intent_text_index",
    )
    try:
        hits: list[Any] = []
        # mongot indexes asynchronously; poll briefly for the inserted docs.
        for _ in range(20):
            results = await service.search("anything", mode=SearchMode.VECTOR, k=2)
            hits = results.hits
            if hits:
                break
            await asyncio.sleep(1.0)
        assert hits, "no vector search results (indexing did not converge)"
        assert hits[0].document["request_id"] == "near"
    finally:
        await coll.drop()


# --- Queryable Encryption --------------------------------------------------


def _qe_settings() -> Settings:
    crypt_path = os.environ.get("GATEWAY_CRYPT_SHARED_LIB_PATH")
    if not crypt_path or not os.path.isfile(crypt_path):
        pytest.skip("GATEWAY_CRYPT_SHARED_LIB_PATH not set / file missing (needed for QE)")
    return default_settings(
        mongo_uri=_mongo_uri(),
        mongo_db=f"blackbox_ai_qe_{uuid.uuid4().hex[:8]}",
        mongo_collection="intents",
        cache_collection="cache",
        encryption_enabled=True,
        encryption_key=generate_local_key(),
        crypt_shared_lib_path=crypt_path,
        embeddings_provider=EmbeddingsProvider.NONE,
    )


async def test_queryable_encryption_round_trip() -> None:
    settings = _qe_settings()
    admin_client = create_client(settings)
    try:
        await ping(admin_client)
    except PyMongoError:
        await admin_client.close()
        pytest.skip("MongoDB not reachable")

    encryption = EncryptionManager(settings)
    data_client = encryption.build_encrypting_client()
    try:
        await ensure_storage(
            settings,
            admin_client=admin_client,
            encryption=encryption,
            embedder=NullEmbedder(),
        )
        doc = IntentDocument(
            request_id="qe_1",
            timestamp=datetime.now(UTC),
            provider="anthropic",
            method="POST",
            endpoint="v1/messages",
            raw_payload={"messages": [{"role": "user", "content": "secret prompt"}]},
            intent_telemetry=IntentTelemetry(
                content="secret answer", chain_of_thought="secret reasoning"
            ),
        )
        await MongoSink(data_client[settings.mongo_db][settings.mongo_collection]).write_many([doc])

        # Plain client sees ciphertext for the protected fields.
        raw = await admin_client[settings.mongo_db][settings.mongo_collection].find_one(
            {"request_id": "qe_1"}
        )
        assert raw is not None
        assert isinstance(raw["raw_payload"], Binary)
        assert isinstance(raw["intent_telemetry"]["content"], Binary)
        assert isinstance(raw["intent_telemetry"]["chain_of_thought"], Binary)
        # Non-sensitive fields stay queryable in plaintext.
        assert raw["provider"] == "anthropic"

        # Encrypting client transparently decrypts on read.
        decrypted = await data_client[settings.mongo_db][settings.mongo_collection].find_one(
            {"request_id": "qe_1"}
        )
        assert decrypted is not None
        assert decrypted["raw_payload"] == {
            "messages": [{"role": "user", "content": "secret prompt"}]
        }
        assert decrypted["intent_telemetry"]["content"] == "secret answer"
        assert decrypted["intent_telemetry"]["chain_of_thought"] == "secret reasoning"
    finally:
        await admin_client.drop_database(settings.mongo_db)
        await data_client.close()
        await admin_client.close()
