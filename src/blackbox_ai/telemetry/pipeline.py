"""The out-of-band telemetry pipeline: a bounded queue drained by workers.

This is the heart of the telemetry plane. The relay calls :meth:`submit` with a
:class:`RawCapture`; that call is non-blocking and fail-open - if the queue is
full the capture is dropped and counted, never blocking the request path. A pool
of worker tasks pulls captures, builds Intent Documents, and bulk-writes them to
the sink, batching by size or time whichever comes first.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from blackbox_ai.cache.store import CacheStore
from blackbox_ai.logging import get_logger
from blackbox_ai.telemetry.builder import build_document
from blackbox_ai.telemetry.capture import RawCapture
from blackbox_ai.telemetry.embeddings import Embedder, NullEmbedder
from blackbox_ai.telemetry.parsers.base import StreamParser
from blackbox_ai.telemetry.sink_mongo import TelemetrySink
from blackbox_ai.telemetry.stages import (
    CacheWriteStage,
    DocumentStage,
    EmbedStage,
    MongoWriteStage,
    PipelineMetrics,
    TelemetryRecord,
)

__all__ = ["PipelineMetrics", "TelemetryPipeline"]

_log = get_logger("blackbox_ai.telemetry")


class _Shutdown:
    """Sentinel enqueued to tell a worker to drain and exit."""


_SHUTDOWN = _Shutdown()


@dataclass(slots=True)
class TelemetryPipeline:
    """Bounded async queue plus a pool of draining workers."""

    sink: TelemetrySink
    parsers: dict[str, StreamParser]
    maxsize: int = 10_000
    worker_count: int = 2
    batch_size: int = 50
    flush_interval_s: float = 1.0
    embedder: Embedder = field(default_factory=NullEmbedder)
    cache_store: CacheStore | None = None

    metrics: PipelineMetrics = field(default_factory=PipelineMetrics)
    _queue: asyncio.Queue[RawCapture | _Shutdown] = field(init=False)
    _workers: list[asyncio.Task[None]] = field(init=False, default_factory=list)
    _stages: list[DocumentStage] = field(init=False, default_factory=list)
    _started: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._queue = asyncio.Queue(maxsize=self.maxsize)
        self._stages = self._build_stages()

    def _build_stages(self) -> list[DocumentStage]:
        """Assemble the ordered post-capture chain from the configured deps.

        Order matters: embed, then persist, then (only for persisted records)
        write cache entries. Adding a concern means adding a stage here.
        """
        stages: list[DocumentStage] = [
            EmbedStage(self.embedder, self.metrics),
            MongoWriteStage(self.sink, self.metrics),
        ]
        if self.cache_store is not None:
            stages.append(CacheWriteStage(self.cache_store, self.metrics))
        return stages

    @property
    def is_running(self) -> bool:
        return self._started and any(not w.done() for w in self._workers)

    @property
    def queue_size(self) -> int:
        """Captures currently waiting in the queue (for observability)."""
        return self._queue.qsize()

    def start(self) -> None:
        """Spawn the worker tasks (idempotent)."""
        if self._started:
            return
        self._started = True
        self._workers = [
            asyncio.create_task(self._worker(i), name=f"telemetry-worker-{i}")
            for i in range(self.worker_count)
        ]
        _log.info("telemetry_pipeline_started", workers=self.worker_count, maxsize=self.maxsize)

    def submit(self, capture: RawCapture) -> bool:
        """Enqueue a capture without blocking. Returns False if dropped."""
        try:
            self._queue.put_nowait(capture)
        except asyncio.QueueFull:
            self.metrics.dropped += 1
            _log.warning(
                "telemetry_dropped_queue_full",
                request_id=capture.request_id,
                provider=capture.provider,
                dropped_total=self.metrics.dropped,
            )
            return False
        self.metrics.submitted += 1
        return True

    async def stop(self, *, drain_timeout_s: float = 5.0) -> None:
        """Signal workers to drain remaining captures, then await their exit."""
        if not self._started:
            return
        for _ in self._workers:
            # Sentinels bypass the bound; queue allows transient over-capacity.
            await self._queue.put(_SHUTDOWN)
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._workers, return_exceptions=True),
                timeout=drain_timeout_s,
            )
        except TimeoutError:
            _log.warning("telemetry_pipeline_drain_timeout", timeout_s=drain_timeout_s)
            for worker in self._workers:
                worker.cancel()
        self._started = False
        _log.info("telemetry_pipeline_stopped", metrics=self.metrics.as_dict())

    async def _worker(self, worker_id: int) -> None:
        batch: list[RawCapture] = []
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=self.flush_interval_s)
            except TimeoutError:
                await self._flush(batch)
                continue
            self._queue.task_done()
            if isinstance(item, _Shutdown):
                break
            batch.append(item)
            if len(batch) >= self.batch_size:
                await self._flush(batch)
        await self._drain_remaining(batch)
        await self._flush(batch)

    async def _drain_remaining(self, batch: list[RawCapture]) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._queue.task_done()
            if isinstance(item, _Shutdown):
                continue
            batch.append(item)

    async def _flush(self, batch: list[RawCapture]) -> None:
        if not batch:
            return
        captures = list(batch)
        batch.clear()
        records = [
            TelemetryRecord(
                capture=capture,
                document=build_document(capture, self.parsers.get(capture.parser_name)),
            )
            for capture in captures
        ]
        for stage in self._stages:
            await stage.process(records)
