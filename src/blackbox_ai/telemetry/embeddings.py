"""Embedding backends for Atlas Vector Search ("time-travel" debugging).

Embeddings are generated in the **telemetry plane** (a worker, off the hot path),
batched per flush. The default :class:`NullEmbedder` is a no-op; configure
``GATEWAY_EMBEDDINGS_PROVIDER=voyage`` with a ``VOYAGE_API_KEY`` to enable
:class:`VoyageEmbedder` (Voyage AI is a MongoDB company; ``voyage-code-3`` is
tuned for code/agent intent).

Everything here is **fail-open**: an embedding failure yields ``None`` for the
affected document, which is then written without a vector. Nothing here can lose
a captured Intent Document.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable

import voyageai
from voyageai import error as voyage_error

from blackbox_ai.logging import get_logger
from blackbox_ai.telemetry.models import IntentDocument

__all__ = ["CircuitBreaker", "Embedder", "NullEmbedder", "VoyageEmbedder", "embedding_text"]

_log = get_logger("blackbox_ai.embeddings")

# Hard cap on characters fed to the embedder. The provider also truncates by
# token budget; this is cheap insurance against pathologically large payloads.
_MAX_EMBED_CHARS = 32_000


class CircuitBreaker:
    """Minimal failure-counting breaker with a cooldown.

    After ``threshold`` consecutive failures the breaker opens and
    :meth:`allow` returns ``False`` until ``cooldown_s`` elapses, at which point
    it half-opens to permit a single trial call. A success resets it; a failed
    trial re-opens it. Single-threaded (event-loop) use, so no locking.
    """

    def __init__(
        self,
        *,
        threshold: int,
        cooldown_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = max(1, threshold)
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        # Half-open once the cooldown has elapsed: allow a single trial call.
        return self._clock() - self._opened_at >= self._cooldown_s

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = self._clock()


@runtime_checkable
class Embedder(Protocol):
    """Turns text into vectors. Implementations must be fail-open."""

    @property
    def model_name(self) -> str: ...

    @property
    def dims(self) -> int: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float] | None]:
        """Embed a batch of documents; returns one vector (or None) per input."""
        ...

    async def embed_query(self, text: str) -> list[float] | None:
        """Embed a single search query, or None on failure."""
        ...


class NullEmbedder:
    """No-op embedder used when vector search is disabled."""

    model_name = "none"
    dims = 0

    async def embed_documents(self, texts: list[str]) -> list[list[float] | None]:
        return [None] * len(texts)

    async def embed_query(self, text: str) -> list[float] | None:
        return None


class VoyageEmbedder:
    """Voyage AI embedder using the async client, batched and fail-open."""

    def __init__(
        self,
        api_key: str,
        model: str,
        dims: int,
        *,
        batch_size: int = 128,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        breaker_threshold: int = 5,
        breaker_cooldown_s: float = 30.0,
    ) -> None:
        self._client = voyageai.AsyncClient(
            api_key=api_key, timeout=timeout_s, max_retries=max_retries
        )
        self._model = model
        self._dims = dims
        self._batch_size = batch_size
        # Stop hammering Voyage during a sustained outage; stays fail-open.
        self._breaker = CircuitBreaker(threshold=breaker_threshold, cooldown_s=breaker_cooldown_s)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dims(self) -> int:
        return self._dims

    async def embed_documents(self, texts: list[str]) -> list[list[float] | None]:
        return await self._embed_many(texts, input_type="document")

    async def embed_query(self, text: str) -> list[float] | None:
        result = await self._embed_many([text], input_type="query")
        return result[0]

    async def _embed_many(self, texts: list[str], *, input_type: str) -> list[list[float] | None]:
        out: list[list[float] | None] = [None] * len(texts)
        # Only embed non-empty inputs; remember their positions to scatter back.
        indexed = [(i, _clip(t)) for i, t in enumerate(texts) if t and t.strip()]
        for start in range(0, len(indexed), self._batch_size):
            window = indexed[start : start + self._batch_size]
            vectors = await self._embed_batch([t for _, t in window], input_type)
            if vectors is None:
                continue
            for (pos, _), vector in zip(window, vectors, strict=False):
                out[pos] = vector
        return out

    async def _embed_batch(self, batch: list[str], input_type: str) -> list[list[float]] | None:
        if not self._breaker.allow():
            # Breaker is open: skip the call entirely (fail-open, no vectors).
            _log.warning("embedding_circuit_open", batch_size=len(batch))
            return None
        try:
            result = await self._client.embed(
                batch,
                model=self._model,
                input_type=input_type,
                output_dimension=self._dims,
            )
        except voyage_error.VoyageError as exc:
            # Fail-open: log and skip; documents are written without vectors.
            self._breaker.record_failure()
            _log.warning("embedding_failed", error=str(exc), batch_size=len(batch))
            return None
        self._breaker.record_success()
        embeddings: list[list[float]] = result.embeddings
        return embeddings


def embedding_text(doc: IntentDocument) -> str | None:
    """Pick the most semantically meaningful text to embed for a document.

    Preference order mirrors the debugging use case: the model's hidden
    reasoning best captures *why* it acted, then its visible output, then -
    failing both (e.g. an error or unparsed response) - the user's prompt.
    """
    telemetry = doc.intent_telemetry
    if telemetry.chain_of_thought and telemetry.chain_of_thought.strip():
        return telemetry.chain_of_thought.strip()
    if telemetry.content and telemetry.content.strip():
        return telemetry.content.strip()
    return _prompt_from_payload(doc.raw_payload)


def _clip(text: str) -> str:
    return text[:_MAX_EMBED_CHARS]


def _prompt_from_payload(payload: dict[str, object] | None) -> str | None:
    """Best-effort extraction of the user prompt across provider request shapes."""
    if not payload:
        return None
    # OpenAI / Anthropic / Ollama chat: {"messages": [{"role", "content"}]}.
    messages = payload.get("messages")
    if isinstance(messages, list):
        text = _join_user_messages(messages)
        if text:
            return text
    # Gemini: {"contents": [{"role", "parts": [{"text"}]}]}.
    contents = payload.get("contents")
    if isinstance(contents, list):
        text = _join_gemini_contents(contents)
        if text:
            return text
    # Ollama generate / legacy completions: {"prompt": "..."}.
    prompt = payload.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    return None


def _join_user_messages(messages: list[object]) -> str | None:
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        parts.append(_message_content_to_text(message.get("content")))
    joined = "\n".join(p for p in parts if p).strip()
    return joined or None


def _message_content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    # Multimodal content arrays: [{"type": "text", "text": "..."}].
    if isinstance(content, list):
        chunks = [
            part["text"]
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        return "\n".join(chunks)
    return ""


def _join_gemini_contents(contents: list[object]) -> str | None:
    parts: list[str] = []
    for entry in contents:
        if not isinstance(entry, dict):
            continue
        if entry.get("role") not in (None, "user"):
            continue
        for part in entry.get("parts", []):
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
    joined = "\n".join(parts).strip()
    return joined or None
