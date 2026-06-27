"""Search service: vector vs hybrid pipeline construction and fallback."""

from __future__ import annotations

from typing import Any

import pytest
from pymongo.errors import OperationFailure

from blackbox_ai.errors import SearchUnavailableError
from blackbox_ai.search import SearchMode, SearchService

_QUERY_VECTOR = [0.1, 0.2, 0.3, 0.4]


class _StubEmbedder:
    model_name = "stub"
    dims = 4

    async def embed_documents(self, texts: list[str]) -> list[list[float] | None]:
        return [_QUERY_VECTOR for _ in texts]

    async def embed_query(self, text: str) -> list[float] | None:
        return _QUERY_VECTOR


class _NullEmbedder:
    model_name = "none"
    dims = 0

    async def embed_documents(self, texts: list[str]) -> list[list[float] | None]:
        return [None for _ in texts]

    async def embed_query(self, text: str) -> list[float] | None:
        return None


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._it = iter(docs)

    def __aiter__(self) -> _FakeCursor:
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._it)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeCollection:
    """Records the pipelines it is given; optionally fails on $rankFusion."""

    def __init__(self, docs: list[dict[str, Any]], *, fail_rank_fusion: bool = False) -> None:
        self._docs = docs
        self._fail_rank_fusion = fail_rank_fusion
        self.pipelines: list[list[dict[str, Any]]] = []

    async def aggregate(self, pipeline: list[dict[str, Any]]) -> _FakeCursor:
        self.pipelines.append(pipeline)
        if self._fail_rank_fusion and pipeline and "$rankFusion" in pipeline[0]:
            raise OperationFailure("$rankFusion not supported")
        return _FakeCursor(self._docs)


def _service(collection: Any, embedder: Any = None) -> SearchService:
    return SearchService(
        collection,
        embedder or _StubEmbedder(),
        vector_index_name="vec_idx",
        search_index_name="txt_idx",
    )


async def test_vector_mode_runs_vector_search() -> None:
    coll = _FakeCollection([{"request_id": "a", "score": 0.91}])
    service = _service(coll)

    results = await service.search("why did it refactor", mode=SearchMode.VECTOR, k=3)

    assert results.mode is SearchMode.VECTOR
    assert results.hits[0].document["request_id"] == "a"
    assert results.hits[0].score == 0.91
    assert "$vectorSearch" in coll.pipelines[0][0]


async def test_hybrid_mode_runs_rank_fusion() -> None:
    coll = _FakeCollection([{"request_id": "b", "score": 0.5}])
    service = _service(coll)

    results = await service.search("openai timeout", mode=SearchMode.HYBRID, k=3)

    assert results.mode is SearchMode.HYBRID
    assert results.hits[0].document["request_id"] == "b"
    fusion = coll.pipelines[0][0]["$rankFusion"]
    assert set(fusion["input"]["pipelines"]) == {"vector", "text"}


async def test_hybrid_falls_back_to_vector_when_unsupported() -> None:
    coll = _FakeCollection([{"request_id": "c", "score": 0.7}], fail_rank_fusion=True)
    service = _service(coll)

    results = await service.search("anything", mode=SearchMode.HYBRID, k=3)

    assert results.mode is SearchMode.VECTOR
    assert results.hits[0].document["request_id"] == "c"
    # First a $rankFusion attempt, then the vector fallback.
    assert "$rankFusion" in coll.pipelines[0][0]
    assert "$vectorSearch" in coll.pipelines[1][0]


async def test_empty_query_rejected() -> None:
    service = _service(_FakeCollection([]))
    with pytest.raises(SearchUnavailableError):
        await service.search("   ")


async def test_unconfigured_embedder_rejected() -> None:
    service = _service(_FakeCollection([]), embedder=_NullEmbedder())
    with pytest.raises(SearchUnavailableError):
        await service.search("real query")
