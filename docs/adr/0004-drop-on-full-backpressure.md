# 4. Telemetry backpressure drops, it never blocks

- Status: Accepted
- Date: 2026-06-26

## Context

The telemetry plane drains a bounded in-memory queue
([telemetry/pipeline.py](../../src/blackbox_ai/telemetry/pipeline.py)). Under
a burst, or if the workers fall behind (slow MongoDB, slow embedding backend),
the queue can fill. There are only two options when a producer meets a full
bounded queue: block the producer until space frees up, or drop the item. The
producer here is the relay's request path.

## Decision

When the queue is full, `submit()` **drops** the capture and increments a
counter; it never blocks and never raises. Blocking is explicitly rejected
because it would couple request latency to telemetry-worker throughput - exactly
the coupling [ADR 0001](0001-data-plane-telemetry-plane-split.md) exists to
prevent.

Drops are observable: `PipelineMetrics.dropped` is exposed via `/readyz` and a
`telemetry_dropped_queue_full` warning is logged with a running total.

## Consequences

- Telemetry is lossy under sustained overload, by design. Availability and
  latency of the proxy are never sacrificed to record it.
- Loss is a tuning signal, not a silent failure: raise
  `GATEWAY_TELEMETRY_QUEUE_MAXSIZE` or `GATEWAY_TELEMETRY_WORKERS` (and the Mongo
  pool) when the dropped counter climbs.
- On graceful shutdown the queue is drained within a timeout; captures still
  queued past the timeout are dropped rather than hanging the process.
