"""Atlas Search index management for the intents collection.

Two indexes power "time-travel" debugging:

* a **vectorSearch** index over the ``embedding`` field (semantic similarity);
* a **search** (full-text) index over plaintext metadata, which combines with
  the vector index in hybrid search via ``$rankFusion``.

The full-text index deliberately covers only plaintext fields. Under Queryable
Encryption the free-text intent fields are stored as ciphertext and cannot be
text-indexed, so semantic recall comes from the (plaintext) embedding while the
text leg matches metadata like provider, model, and project.

Both index creations are **best-effort** - on a plain MongoDB server (no
``mongot``) they simply fail and are logged; the gateway keeps relaying and
capturing telemetry regardless.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import OperationFailure, PyMongoError
from pymongo.operations import SearchIndexModel

from blackbox_ai.logging import get_logger

__all__ = [
    "TEXT_SEARCH_PATHS",
    "SearchIndexResult",
    "VectorIndexResult",
    "ensure_search_index",
    "ensure_vector_index",
    "text_index_definition",
    "vector_index_definition",
]

_log = get_logger("blackbox_ai.search_index")

# Pre-filter fields exposed to ``$vectorSearch``. These let a search be scoped to
# one project or agent session - the common debugging entry points.
_FILTER_FIELDS = ("project_id", "session_id", "provider", "developer_id")

# Plaintext fields covered by the full-text index and searched in hybrid mode.
# (Encrypted free-text fields are intentionally excluded - see module docstring.)
TEXT_SEARCH_PATHS: tuple[str, ...] = (
    "model_requested",
    "model_responded",
    "provider",
    "endpoint",
    "project_id",
    "session_id",
    "developer_id",
    "intent_telemetry.finish_reason",
)


class SearchIndexResult:
    """Outcome of an ``ensure_*_index`` call."""

    def __init__(self, *, created: bool, queryable: bool, error: str | None = None) -> None:
        self.created = created
        self.queryable = queryable
        self.error = error

    def __repr__(self) -> str:
        return (
            f"SearchIndexResult(created={self.created}, "
            f"queryable={self.queryable}, error={self.error!r})"
        )


# Backwards-compatible alias (both index types share the same result shape).
VectorIndexResult = SearchIndexResult


def vector_index_definition(dims: int, similarity: str = "cosine") -> dict[str, Any]:
    """Build the ``vectorSearch`` index definition for the embedding field."""
    fields: list[dict[str, Any]] = [
        {
            "type": "vector",
            "path": "embedding",
            "numDimensions": dims,
            "similarity": similarity,
        }
    ]
    fields.extend({"type": "filter", "path": field} for field in _FILTER_FIELDS)
    return {"fields": fields}


def text_index_definition() -> dict[str, Any]:
    """Build the Atlas Search (full-text) index over plaintext metadata fields."""
    string_fields = {path: {"type": "string"} for path in TEXT_SEARCH_PATHS if "." not in path}
    # ``intent_telemetry.finish_reason`` lives under a nested document mapping.
    return {
        "mappings": {
            "dynamic": False,
            "fields": {
                **string_fields,
                "intent_telemetry": {
                    "type": "document",
                    "fields": {"finish_reason": {"type": "string"}},
                },
            },
        }
    }


async def _existing_index(
    collection: AsyncCollection[dict[str, Any]], name: str
) -> dict[str, Any] | None:
    cursor = await collection.list_search_indexes(name)
    async for index in cursor:
        return dict(index)
    return None


async def ensure_vector_index(
    collection: AsyncCollection[dict[str, Any]],
    *,
    name: str,
    dims: int,
    wait: bool = False,
    timeout_s: float = 120.0,
    poll_interval_s: float = 3.0,
) -> SearchIndexResult:
    """Create the vector index if absent; optionally wait until it is queryable.

    Idempotent and best-effort. Returns a :class:`SearchIndexResult` describing
    what happened; never raises for an unsupported backend.
    """
    model = SearchIndexModel(
        definition=vector_index_definition(dims),
        name=name,
        type="vectorSearch",
    )
    return await _ensure_index(
        collection,
        model,
        name=name,
        kind="vector",
        wait=wait,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )


async def ensure_search_index(
    collection: AsyncCollection[dict[str, Any]],
    *,
    name: str,
    wait: bool = False,
    timeout_s: float = 120.0,
    poll_interval_s: float = 3.0,
) -> SearchIndexResult:
    """Create the full-text search index if absent (best-effort, idempotent)."""
    model = SearchIndexModel(
        definition=text_index_definition(),
        name=name,
        type="search",
    )
    return await _ensure_index(
        collection,
        model,
        name=name,
        kind="text",
        wait=wait,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )


async def _ensure_index(
    collection: AsyncCollection[dict[str, Any]],
    model: SearchIndexModel,
    *,
    name: str,
    kind: str,
    wait: bool,
    timeout_s: float,
    poll_interval_s: float,
) -> SearchIndexResult:
    try:
        existing = await _existing_index(collection, name)
        if existing is not None:
            queryable = bool(existing.get("queryable"))
            _log.info("search_index_exists", kind=kind, name=name, queryable=queryable)
            if wait and not queryable:
                queryable = await _wait_until_queryable(
                    collection, name, timeout_s, poll_interval_s
                )
            return SearchIndexResult(created=False, queryable=queryable)

        await collection.create_search_index(model)
        _log.info("search_index_created", kind=kind, name=name)
        queryable = False
        if wait:
            queryable = await _wait_until_queryable(collection, name, timeout_s, poll_interval_s)
        return SearchIndexResult(created=True, queryable=queryable)
    except (OperationFailure, PyMongoError) as exc:
        # Plain MongoDB (no Atlas Search) or insufficient privileges. Log and
        # continue: search is an optional capability.
        _log.warning("search_index_unavailable", kind=kind, name=name, error=str(exc))
        return SearchIndexResult(created=False, queryable=False, error=str(exc))


async def _wait_until_queryable(
    collection: AsyncCollection[dict[str, Any]],
    name: str,
    timeout_s: float,
    poll_interval_s: float,
) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        index = await _existing_index(collection, name)
        if index is not None and index.get("queryable"):
            _log.info("vector_index_queryable", name=name)
            return True
        await asyncio.sleep(poll_interval_s)
    _log.warning("vector_index_not_queryable_in_time", name=name, timeout_s=timeout_s)
    return False
