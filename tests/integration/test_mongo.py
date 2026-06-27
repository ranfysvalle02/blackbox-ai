"""Integration tests against a live MongoDB (Atlas Local).

Skipped unless ``MONGO_TEST_URI`` is set and reachable. With docker compose up,
run: ``MONGO_TEST_URI=mongodb://localhost:27017/?directConnection=true make test-int``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError

from blackbox_ai.db.indexes import ensure_indexes
from blackbox_ai.db.mongo import create_client, ping
from blackbox_ai.telemetry.models import IntentDocument, IntentTelemetry, Performance
from blackbox_ai.telemetry.sink_mongo import MongoSink
from tests.conftest import default_settings

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def collection() -> AsyncIterator[AsyncCollection[dict[str, Any]]]:
    uri = os.environ.get("MONGO_TEST_URI")
    if not uri:
        pytest.skip("MONGO_TEST_URI not set")
    settings = default_settings(mongo_uri=uri, mongo_db="blackbox_ai_test")
    client = create_client(settings)
    try:
        await ping(client)
    except PyMongoError:
        await client.close()
        pytest.skip("MongoDB not reachable at MONGO_TEST_URI")
    coll = client[settings.mongo_db]["intents_it"]
    await coll.delete_many({})
    await ensure_indexes(coll)
    try:
        yield coll
    finally:
        await coll.drop()
        await client.close()


async def test_intent_document_round_trip(collection: AsyncCollection[dict[str, Any]]) -> None:
    sink = MongoSink(collection)
    doc = IntentDocument(
        request_id="req_it_1",
        timestamp=datetime.now(UTC),
        provider="anthropic",
        method="POST",
        endpoint="v1/messages",
        model_requested="claude-3-5-sonnet",
        model_responded="claude-3-5-sonnet-20241022",
        streamed=True,
        project_id="auth-service-migration",
        session_id="agent_run_99482f",
        developer_id="dev_clara",
        performance=Performance(latency_ms=420.0, input_tokens=14050, output_tokens=2401),
        raw_payload={"messages": [{"role": "user", "content": "Fix the pool leak."}]},
        intent_telemetry=IntentTelemetry(
            content="Set max_overflow to 10.",
            chain_of_thought="The user is experiencing a pool leak.",
            finish_reason="end_turn",
        ),
    )

    written = await sink.write_many([doc])
    assert written == 1

    stored = await collection.find_one({"request_id": "req_it_1"})
    assert stored is not None
    assert stored["provider"] == "anthropic"
    assert stored["project_id"] == "auth-service-migration"
    assert stored["performance"]["input_tokens"] == 14050
    assert stored["intent_telemetry"]["chain_of_thought"].startswith("The user")
    # Reserved Phase-4 fields exist for forward compatibility.
    assert stored["embedding"] is None
    assert stored["cache_key"] is None


async def test_expected_indexes_exist(collection: AsyncCollection[dict[str, Any]]) -> None:
    cursor = await collection.list_indexes()
    names = {index["name"] async for index in cursor}
    assert {"project_timestamp", "session_id", "developer_partial", "timestamp"} <= names
