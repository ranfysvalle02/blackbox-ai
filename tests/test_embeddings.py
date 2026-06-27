"""Tests for embedding text selection and the VoyageEmbedder (mocked client)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from voyageai import error as voyage_error

from blackbox_ai.telemetry import embeddings as emb
from blackbox_ai.telemetry.embeddings import (
    CircuitBreaker,
    NullEmbedder,
    VoyageEmbedder,
    embedding_text,
)
from blackbox_ai.telemetry.models import IntentDocument, IntentTelemetry
from tests.conftest import build_harness, default_settings, load_fixture, wait_until


def _doc(**kwargs: Any) -> IntentDocument:
    base: dict[str, Any] = {
        "request_id": "r",
        "timestamp": datetime.now(UTC),
        "provider": "openai",
        "method": "POST",
        "endpoint": "v1/chat/completions",
    }
    base.update(kwargs)
    return IntentDocument(**base)


def test_embedding_text_prefers_chain_of_thought() -> None:
    doc = _doc(
        intent_telemetry=IntentTelemetry(content="visible", chain_of_thought="  the reasoning  "),
    )
    assert embedding_text(doc) == "the reasoning"


def test_embedding_text_falls_back_to_content_then_prompt() -> None:
    content_doc = _doc(intent_telemetry=IntentTelemetry(content="the answer"))
    assert embedding_text(content_doc) == "the answer"

    prompt_doc = _doc(
        raw_payload={"messages": [{"role": "user", "content": "why did it fail?"}]},
        intent_telemetry=IntentTelemetry(),
    )
    assert embedding_text(prompt_doc) == "why did it fail?"


def test_embedding_text_handles_gemini_and_ollama_shapes() -> None:
    gemini = _doc(raw_payload={"contents": [{"role": "user", "parts": [{"text": "gem"}]}]})
    assert embedding_text(gemini) == "gem"
    ollama = _doc(raw_payload={"prompt": "generate this"})
    assert embedding_text(ollama) == "generate this"


def test_embedding_text_none_when_empty() -> None:
    assert embedding_text(_doc()) is None


async def test_null_embedder_returns_nones() -> None:
    embedder = NullEmbedder()
    assert embedder.dims == 0
    assert await embedder.embed_documents(["a", "b"]) == [None, None]
    assert await embedder.embed_query("x") is None


class _FakeVoyageResult:
    def __init__(self, embeddings: list[list[float]]) -> None:
        self.embeddings = embeddings


class _FakeVoyageClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_error = False

    async def embed(self, texts: list[str], **kwargs: Any) -> _FakeVoyageResult:
        self.calls.append({"texts": texts, **kwargs})
        if self.raise_error:
            raise voyage_error.RateLimitError("slow down")
        return _FakeVoyageResult([[float(len(t)), 0.5] for t in texts])


async def test_voyage_embedder_embeds_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[_FakeVoyageClient] = []

    def factory(*args: Any, **kwargs: Any) -> _FakeVoyageClient:
        client = _FakeVoyageClient()
        captured.append(client)
        return client

    monkeypatch.setattr(emb.voyageai, "AsyncClient", factory)
    embedder = VoyageEmbedder(api_key="k", model="voyage-code-3", dims=2)
    vectors = await embedder.embed_documents(["abc", "", "de"])
    # Empty strings are skipped (None), non-empty get vectors.
    assert vectors[0] == [3.0, 0.5]
    assert vectors[1] is None
    assert vectors[2] == [2.0, 0.5]
    # Only non-empty inputs were sent, with the document input type.
    assert captured[0].calls[0]["texts"] == ["abc", "de"]
    assert captured[0].calls[0]["input_type"] == "document"


async def test_voyage_embedder_is_fail_open_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(*args: Any, **kwargs: Any) -> _FakeVoyageClient:
        client = _FakeVoyageClient()
        client.raise_error = True
        return client

    monkeypatch.setattr(emb.voyageai, "AsyncClient", factory)
    embedder = VoyageEmbedder(api_key="k", model="voyage-code-3", dims=2)
    assert await embedder.embed_documents(["abc", "de"]) == [None, None]
    assert await embedder.embed_query("q") is None


def test_circuit_breaker_opens_short_circuits_and_recovers() -> None:
    now = {"t": 0.0}
    breaker = CircuitBreaker(threshold=2, cooldown_s=10.0, clock=lambda: now["t"])

    assert breaker.allow()
    breaker.record_failure()
    assert breaker.allow()  # one failure, still closed
    breaker.record_failure()  # second consecutive failure -> opens
    assert not breaker.allow()  # within cooldown: short-circuit

    now["t"] = 10.0  # cooldown elapsed -> half-open trial allowed
    assert breaker.allow()
    breaker.record_success()  # trial succeeded -> reset
    assert breaker.allow()


async def test_voyage_embedder_breaker_stops_calling_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[_FakeVoyageClient] = []

    def factory(*args: Any, **kwargs: Any) -> _FakeVoyageClient:
        client = _FakeVoyageClient()
        client.raise_error = True
        captured.append(client)
        return client

    monkeypatch.setattr(emb.voyageai, "AsyncClient", factory)
    embedder = VoyageEmbedder(
        api_key="k", model="voyage-code-3", dims=2, breaker_threshold=2, breaker_cooldown_s=60.0
    )

    # Each call is one batch -> one failure. After the threshold the breaker
    # opens and later batches short-circuit without touching the provider.
    for _ in range(4):
        assert await embedder.embed_documents(["x"]) == [None]
    assert len(captured[0].calls) == 2


class _FakeEmbedder:
    """Deterministic embedder for pipeline wiring tests."""

    model_name = "fake-embed"
    dims = 3

    async def embed_documents(self, texts: list[str]) -> list[list[float] | None]:
        return [[1.0, 2.0, 3.0] if t else None for t in texts]

    async def embed_query(self, text: str) -> list[float] | None:
        return [1.0, 2.0, 3.0]


@respx.mock
async def test_pipeline_attaches_embeddings_to_documents() -> None:
    body = load_fixture("openai_completion.json")
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, headers={"content-type": "application/json"}, content=body)
    )
    async with build_harness(default_settings(), embedder=_FakeEmbedder()) as h:
        response = await h.client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
            headers={"authorization": "Bearer t"},
        )
        assert response.status_code == 200
        assert await wait_until(lambda: len(h.sink.documents) == 1)
        doc = h.sink.documents[0]
        assert doc.embedding == [1.0, 2.0, 3.0]
        assert doc.embedding_model == "fake-embed"
        assert h.pipeline.metrics.embedded == 1
