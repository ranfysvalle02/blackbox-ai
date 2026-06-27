"""Composable post-capture processing stages.

Each flushed batch becomes a list of :class:`TelemetryRecord` - a raw capture
paired with the Intent Document built from it. The pipeline runs an ordered list
of :class:`DocumentStage` objects over that batch. Every stage is fail-open by
contract: a stage's failure is logged and counted, never propagated, so it can
neither lose the batch nor stop the stages behind it.

The payoff is that a new concern (PII redaction, sampling, a second sink) is a
new stage in this file plus one line where the pipeline assembles its chain -
the flush loop itself never changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pymongo.errors import PyMongoError

from blackbox_ai.cache.keys import CacheIdentity
from blackbox_ai.cache.store import CacheStore
from blackbox_ai.logging import get_logger
from blackbox_ai.telemetry.capture import RawCapture
from blackbox_ai.telemetry.embeddings import Embedder, embedding_text
from blackbox_ai.telemetry.models import CaptureStatus, IntentDocument
from blackbox_ai.telemetry.sink_mongo import TelemetrySink

__all__ = [
    "CacheWriteStage",
    "DocumentStage",
    "EmbedStage",
    "MongoWriteStage",
    "PipelineMetrics",
    "TelemetryRecord",
]

_log = get_logger("blackbox_ai.telemetry")


@dataclass(slots=True)
class PipelineMetrics:
    """Lightweight in-process counters for observability."""

    submitted: int = 0
    dropped: int = 0
    written: int = 0
    failed: int = 0
    embedded: int = 0
    cached: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "submitted": self.submitted,
            "dropped": self.dropped,
            "written": self.written,
            "failed": self.failed,
            "embedded": self.embedded,
            "cached": self.cached,
        }


@dataclass(slots=True)
class TelemetryRecord:
    """A capture and the document built from it, flowing through the stages.

    ``persisted`` is set by the write stage so later stages (e.g. cache writes)
    only act on records that actually reached the database.
    """

    capture: RawCapture
    document: IntentDocument
    persisted: bool = False


@runtime_checkable
class DocumentStage(Protocol):
    """One step in the post-capture chain. Must be fail-open."""

    async def process(self, records: list[TelemetryRecord]) -> None:
        """Process a batch in place; must never raise."""
        ...


class EmbedStage:
    """Attaches embedding vectors to documents (off the hot path, fail-open).

    The :class:`Embedder` never raises; on any backend failure it returns
    ``None`` per document, which simply means that document is persisted without
    a vector (and so is invisible to vector search until re-embedded).
    """

    def __init__(self, embedder: Embedder, metrics: PipelineMetrics) -> None:
        self._embedder = embedder
        self._metrics = metrics

    async def process(self, records: list[TelemetryRecord]) -> None:
        if self._embedder.dims == 0 or not records:
            return
        texts = [embedding_text(record.document) or "" for record in records]
        vectors = await self._embedder.embed_documents(texts)
        model = self._embedder.model_name
        for record, vector in zip(records, vectors, strict=False):
            if vector is not None:
                record.document.embedding = vector
                record.document.embedding_model = model
                self._metrics.embedded += 1


class MongoWriteStage:
    """Persists the batch to the telemetry sink (the terminal write)."""

    def __init__(self, sink: TelemetrySink, metrics: PipelineMetrics) -> None:
        self._sink = sink
        self._metrics = metrics

    async def process(self, records: list[TelemetryRecord]) -> None:
        if not records:
            return
        documents = [record.document for record in records]
        try:
            written = await self._sink.write_many(documents)
        except PyMongoError as exc:
            self._metrics.failed += len(documents)
            _log.error("telemetry_write_failed", error=str(exc), batch_size=len(documents))
            return
        for record in records:
            record.persisted = True
        self._metrics.written += written
        _log.debug("telemetry_batch_written", count=written)


class CacheWriteStage:
    """Writes cache entries for persisted, cacheable, successful misses."""

    def __init__(self, cache_store: CacheStore, metrics: PipelineMetrics) -> None:
        self._cache_store = cache_store
        self._metrics = metrics

    async def process(self, records: list[TelemetryRecord]) -> None:
        for record in records:
            if not record.persisted:
                continue
            identity = _cacheable_identity(record.capture)
            if identity is None:
                continue
            capture = record.capture
            try:
                await self._cache_store.put(
                    identity,
                    provider=capture.provider,
                    endpoint=capture.endpoint,
                    status_code=capture.http_status or 200,
                    content_type=capture.response_content_type,
                    response_body=capture.response_body,
                    request_payload=record.document.raw_payload,
                )
            except PyMongoError as exc:
                _log.warning("cache_write_failed", error=str(exc), cache_key=identity.key)
                continue
            self._metrics.cached += 1


def _cacheable_identity(capture: RawCapture) -> CacheIdentity | None:
    """Return the cache identity when a capture should be written to the cache.

    Only fresh (non-replayed), opted-in, fully-captured 2xx responses qualify.
    """
    if (
        not capture.cache_requested
        or capture.served_from_cache
        or capture.cache_key is None
        or capture.status is not CaptureStatus.OK
        or capture.http_status is None
        or not (200 <= capture.http_status < 300)
        or capture.response_truncated
        or not capture.response_body
    ):
        return None
    return CacheIdentity(key=capture.cache_key, streamed=capture.streamed)
