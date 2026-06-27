"""Time-travel search over captured Intent Documents.

Two retrieval modes share one entry point:

* **vector** - embed the question and run an Atlas ``$vectorSearch`` against the
  ``embedding`` field (pure semantic similarity).
* **hybrid** (default) - fuse that vector search with a full-text ``$search``
  over plaintext metadata using ``$rankFusion`` (MongoDB 8.1+), so exact
  keyword hits (a provider, model, or project name) and semantic matches are
  combined into one ranked list. If the server can't run ``$rankFusion`` (older
  MongoDB, or no full-text index), it transparently falls back to vector search.

When Queryable Encryption is enabled the collection is read through the
encrypting client, so the ``intent_telemetry`` text comes back decrypted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import OperationFailure

from blackbox_ai.db.search_indexes import TEXT_SEARCH_PATHS
from blackbox_ai.errors import SearchUnavailableError
from blackbox_ai.logging import get_logger
from blackbox_ai.telemetry.embeddings import Embedder

__all__ = ["SearchHit", "SearchMode", "SearchResults", "SearchService"]

_log = get_logger("blackbox_ai.search")

# Relative influence of each leg when fusing. Semantics lead; keyword hits
# sharpen precision for metadata queries.
_VECTOR_WEIGHT = 0.7
_TEXT_WEIGHT = 0.3

# Fields returned for each hit. Encrypted fields (intent_telemetry text) are
# decrypted by the client; raw_payload is intentionally omitted to keep results
# lean - fetch the full document by request_id if you need it.
_DOCUMENT_FIELDS: dict[str, Any] = {
    "_id": 0,
    "request_id": 1,
    "timestamp": 1,
    "provider": 1,
    "endpoint": 1,
    "model_requested": 1,
    "model_responded": 1,
    "project_id": 1,
    "session_id": 1,
    "developer_id": 1,
    "status": 1,
    "performance": 1,
    "intent_telemetry": 1,
}


class SearchMode(StrEnum):
    """Retrieval strategy for :meth:`SearchService.search`."""

    VECTOR = "vector"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One search result: the score plus the projected document."""

    score: float
    document: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SearchResults:
    """A ranked result set plus the mode that actually ran (after any fallback)."""

    mode: SearchMode
    hits: list[SearchHit]


class SearchService:
    """Runs vector and hybrid search with optional metadata pre-filters."""

    def __init__(
        self,
        collection: AsyncCollection[dict[str, Any]],
        embedder: Embedder,
        *,
        vector_index_name: str,
        search_index_name: str,
    ) -> None:
        self._collection = collection
        self._embedder = embedder
        self._vector_index = vector_index_name
        self._search_index = search_index_name

    async def search(
        self,
        query: str,
        *,
        mode: SearchMode = SearchMode.HYBRID,
        project_id: str | None = None,
        session_id: str | None = None,
        provider: str | None = None,
        developer_id: str | None = None,
        k: int = 5,
        num_candidates: int | None = None,
    ) -> SearchResults:
        """Return the ``k`` most relevant Intent Documents for ``query``."""
        if not query.strip():
            raise SearchUnavailableError("Search query must not be empty.")
        query_vector = await self._embedder.embed_query(query)
        if query_vector is None:
            raise SearchUnavailableError(
                "Could not embed the query. Is the embedding backend configured "
                "(GATEWAY_EMBEDDINGS_PROVIDER / VOYAGE_API_KEY)?"
            )

        pre_filter = _build_filter(
            project_id=project_id,
            session_id=session_id,
            provider=provider,
            developer_id=developer_id,
        )

        if mode is SearchMode.HYBRID:
            hits = await self._run_hybrid(query, query_vector, pre_filter, k, num_candidates)
            if hits is not None:
                _log.info("hybrid_search", query_len=len(query), results=len(hits))
                return SearchResults(mode=SearchMode.HYBRID, hits=hits)
            # Fall back to vector-only when $rankFusion / full-text is unavailable.

        hits = await self._run_vector(query_vector, pre_filter, k, num_candidates)
        _log.info("vector_search", query_len=len(query), results=len(hits))
        return SearchResults(mode=SearchMode.VECTOR, hits=hits)

    def _vector_stage(
        self,
        query_vector: list[float],
        pre_filter: dict[str, Any],
        k: int,
        num_candidates: int | None,
    ) -> dict[str, Any]:
        stage: dict[str, Any] = {
            "index": self._vector_index,
            "path": "embedding",
            "queryVector": query_vector,
            "numCandidates": num_candidates or max(k * 20, 100),
            "limit": k,
        }
        if pre_filter:
            stage["filter"] = pre_filter
        return {"$vectorSearch": stage}

    async def _run_vector(
        self,
        query_vector: list[float],
        pre_filter: dict[str, Any],
        k: int,
        num_candidates: int | None,
    ) -> list[SearchHit]:
        pipeline: list[dict[str, Any]] = [
            self._vector_stage(query_vector, pre_filter, k, num_candidates),
            {"$project": {**_DOCUMENT_FIELDS, "score": {"$meta": "vectorSearchScore"}}},
        ]
        return await self._collect(pipeline)

    async def _run_hybrid(
        self,
        query: str,
        query_vector: list[float],
        pre_filter: dict[str, Any],
        k: int,
        num_candidates: int | None,
    ) -> list[SearchHit] | None:
        """Run ``$rankFusion``; return ``None`` if the server can't (caller falls back)."""
        text_stage: dict[str, Any] = {
            "$search": {
                "index": self._search_index,
                "text": {"query": query, "path": list(TEXT_SEARCH_PATHS)},
            }
        }
        vector_stage = self._vector_stage(query_vector, pre_filter, k, num_candidates)
        pipeline: list[dict[str, Any]] = [
            {
                "$rankFusion": {
                    "input": {
                        "pipelines": {
                            "vector": [vector_stage],
                            "text": [text_stage, {"$limit": k}],
                        }
                    },
                    "combination": {"weights": {"vector": _VECTOR_WEIGHT, "text": _TEXT_WEIGHT}},
                }
            },
            {"$limit": k},
            {"$addFields": {"score": {"$meta": "score"}}},
            {"$project": {**_DOCUMENT_FIELDS, "score": 1}},
        ]
        try:
            return await self._collect(pipeline)
        except OperationFailure as exc:
            _log.warning("hybrid_search_unavailable", error=str(exc))
            return None

    async def _collect(self, pipeline: list[dict[str, Any]]) -> list[SearchHit]:
        cursor = await self._collection.aggregate(pipeline)
        hits: list[SearchHit] = []
        async for doc in cursor:
            score = float(doc.pop("score", 0.0))
            hits.append(SearchHit(score=score, document=dict(doc)))
        return hits


def _build_filter(
    *,
    project_id: str | None,
    session_id: str | None,
    provider: str | None,
    developer_id: str | None,
) -> dict[str, Any]:
    clause: dict[str, Any] = {}
    if project_id:
        clause["project_id"] = project_id
    if session_id:
        clause["session_id"] = session_id
    if provider:
        clause["provider"] = provider
    if developer_id:
        clause["developer_id"] = developer_id
    return clause
