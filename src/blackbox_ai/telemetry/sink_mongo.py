"""MongoDB sink for Intent Documents.

A thin wrapper over an async PyMongo collection that performs unordered bulk
inserts. Errors are surfaced to the caller (the telemetry worker), which decides
on the fail-open policy; this class does not swallow them.
"""

from __future__ import annotations

from typing import Any, Protocol

from pymongo.asynchronous.collection import AsyncCollection

from blackbox_ai.telemetry.models import IntentDocument

__all__ = ["MongoSink", "TelemetrySink"]


class TelemetrySink(Protocol):
    """Destination for batches of Intent Documents."""

    async def write_many(self, documents: list[IntentDocument]) -> int:
        """Persist a batch and return the number of documents written."""
        ...


class MongoSink:
    """Persists Intent Documents to a MongoDB collection.

    When Queryable Encryption is enabled, ``prune_null_paths`` lists the encrypted
    field paths whose ``null`` values must be removed before insertion: automatic
    QE refuses to encrypt ``null`` (error 31041), and these fields are frequently
    absent (e.g. no chain-of-thought, or a body-less GET).
    """

    def __init__(
        self,
        collection: AsyncCollection[dict[str, Any]],
        *,
        prune_null_paths: tuple[str, ...] = (),
    ) -> None:
        self._collection = collection
        self._prune_null_paths = prune_null_paths

    async def write_many(self, documents: list[IntentDocument]) -> int:
        if not documents:
            return 0
        payload = [self._to_storable(doc) for doc in documents]
        # ordered=False lets a single malformed document fail without aborting
        # the rest of the batch.
        result = await self._collection.insert_many(payload, ordered=False)
        return len(result.inserted_ids)

    def _to_storable(self, document: IntentDocument) -> dict[str, Any]:
        raw = document.to_mongo()
        for path in self._prune_null_paths:
            _drop_if_none(raw, path)
        return raw


def _drop_if_none(document: dict[str, Any], dotted_path: str) -> None:
    """Delete a (possibly nested) field from ``document`` when its value is None."""
    parts = dotted_path.split(".")
    cursor: Any = document
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            return
        cursor = cursor[part]
    leaf = parts[-1]
    if isinstance(cursor, dict) and cursor.get(leaf) is None:
        cursor.pop(leaf, None)
