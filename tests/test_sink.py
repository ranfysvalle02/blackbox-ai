"""Tests for MongoSink, including QE null-field pruning."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from blackbox_ai.telemetry.models import IntentDocument, IntentTelemetry
from blackbox_ai.telemetry.sink_mongo import MongoSink


class _FakeResult:
    def __init__(self, n: int) -> None:
        self.inserted_ids = list(range(n))


class _FakeCollection:
    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []

    async def insert_many(self, payload: list[dict[str, Any]], *, ordered: bool) -> _FakeResult:
        self.inserted.extend(payload)
        return _FakeResult(len(payload))


def _doc() -> IntentDocument:
    return IntentDocument(
        request_id="r",
        timestamp=datetime.now(UTC),
        provider="openai",
        method="GET",
        endpoint="v1/models",
        raw_payload=None,
        intent_telemetry=IntentTelemetry(content="visible", chain_of_thought=None),
    )


async def test_sink_keeps_nulls_without_prune_paths() -> None:
    coll = _FakeCollection()
    written = await MongoSink(coll).write_many([_doc()])  # type: ignore[arg-type]
    assert written == 1
    stored = coll.inserted[0]
    assert stored["raw_payload"] is None
    assert stored["intent_telemetry"]["chain_of_thought"] is None


async def test_sink_prunes_null_encrypted_paths() -> None:
    coll = _FakeCollection()
    sink = MongoSink(
        coll,  # type: ignore[arg-type]
        prune_null_paths=(
            "raw_payload",
            "intent_telemetry.content",
            "intent_telemetry.chain_of_thought",
        ),
    )
    await sink.write_many([_doc()])
    stored = coll.inserted[0]
    # Null encrypted fields are dropped (QE cannot encrypt null)...
    assert "raw_payload" not in stored
    assert "chain_of_thought" not in stored["intent_telemetry"]
    # ...but non-null encrypted fields and untouched plaintext remain.
    assert stored["intent_telemetry"]["content"] == "visible"
    assert stored["provider"] == "openai"
