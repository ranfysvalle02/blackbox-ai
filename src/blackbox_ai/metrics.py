"""Prometheus instruments and the ``/metrics`` exposition source.

Two kinds of metric live here:

* **Request-path instruments** (``RELAY_*``) - counters, histograms, and a gauge
  the relay updates inline. They are module-level singletons in the default
  registry, created once at import, so repeated ``create_app()`` calls are safe.
* **Telemetry-plane metrics** - exposed by :class:`PipelineCollector`, which
  reads the live :class:`~blackbox_ai.telemetry.stages.PipelineMetrics` (and the
  queue depth) at scrape time. This keeps the pipeline counters the single
  source of truth instead of mirroring them into separate Prometheus counters.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

if TYPE_CHECKING:
    from blackbox_ai.telemetry.pipeline import TelemetryPipeline

__all__ = [
    "RELAY_INFLIGHT",
    "RELAY_LATENCY",
    "RELAY_RATE_LIMITED",
    "RELAY_REJECTED",
    "RELAY_REQUESTS",
    "RELAY_TTFT",
    "PipelineCollector",
    "register_pipeline_collector",
    "unregister_pipeline_collector",
]

# Counter names are given without the ``_total`` suffix; the client appends it.
RELAY_REQUESTS = Counter(
    "blackbox_relay_requests",
    "Relayed requests by provider, method, upstream status, and cache outcome.",
    ["provider", "method", "status", "cache"],
)
RELAY_REJECTED = Counter(
    "blackbox_relay_rejected",
    "Requests rejected by the in-flight concurrency cap (503).",
    ["provider"],
)
RELAY_RATE_LIMITED = Counter(
    "blackbox_relay_rate_limited",
    "Requests rejected by the per-client rate limiter (429).",
    ["scope"],
)
RELAY_INFLIGHT = Gauge(
    "blackbox_relay_inflight_requests",
    "Requests currently being relayed (holding a concurrency permit).",
)
# Latency can be long for streamed completions; TTFT is the first-byte signal.
RELAY_LATENCY = Histogram(
    "blackbox_relay_request_duration_seconds",
    "End-to-end relay duration, from request receipt to stream completion.",
    ["provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)
RELAY_TTFT = Histogram(
    "blackbox_relay_ttft_seconds",
    "Time to first byte from the upstream provider.",
    ["provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)


class PipelineCollector(Collector):
    """Exposes telemetry-plane counters and queue depth at scrape time."""

    def __init__(self, pipeline: TelemetryPipeline) -> None:
        self._pipeline = pipeline

    def collect(self) -> Iterator[Metric]:
        metrics = self._pipeline.metrics
        counters = {
            "blackbox_telemetry_submitted": (
                "Captures accepted onto the telemetry queue.",
                metrics.submitted,
            ),
            "blackbox_telemetry_dropped": (
                "Captures dropped because the queue was full (backpressure).",
                metrics.dropped,
            ),
            "blackbox_telemetry_written": (
                "Intent Documents persisted to MongoDB.",
                metrics.written,
            ),
            "blackbox_telemetry_failed": (
                "Intent Documents that failed to persist.",
                metrics.failed,
            ),
            "blackbox_telemetry_embedded": (
                "Documents that received an embedding vector.",
                metrics.embedded,
            ),
            "blackbox_telemetry_cached": (
                "Responses written to the token cache.",
                metrics.cached,
            ),
        }
        for name, (doc, value) in counters.items():
            yield CounterMetricFamily(name, doc, value=value)

        yield GaugeMetricFamily(
            "blackbox_telemetry_queue_size",
            "Captures currently waiting in the telemetry queue.",
            value=self._pipeline.queue_size,
        )
        yield GaugeMetricFamily(
            "blackbox_telemetry_queue_maxsize",
            "Maximum telemetry queue capacity before captures are dropped.",
            value=self._pipeline.maxsize,
        )


_current_collector: PipelineCollector | None = None


def register_pipeline_collector(pipeline: TelemetryPipeline) -> PipelineCollector:
    """Register a collector for ``pipeline``, replacing any previous one.

    Idempotent by design: a re-run lifespan (or a second app in the same
    process) cannot raise "Duplicated timeseries" because the prior collector is
    unregistered first.
    """
    unregister_pipeline_collector()
    collector = PipelineCollector(pipeline)
    REGISTRY.register(collector)
    global _current_collector
    _current_collector = collector
    return collector


def unregister_pipeline_collector(collector: PipelineCollector | None = None) -> None:
    """Unregister the given collector, or the currently-registered one."""
    global _current_collector
    target = collector or _current_collector
    if target is not None:
        REGISTRY.unregister(target)
        if target is _current_collector:
            _current_collector = None
