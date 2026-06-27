# 1. Split the data plane from the telemetry plane

- Status: Accepted
- Date: 2026-06-26

## Context

The gateway has two jobs that pull in opposite directions. As a proxy it must be
invisible and reliable: relay bytes verbatim, never add latency, never fail a
request it could have served. As a flight recorder it must capture rich,
fallible telemetry: parse provider-specific streams, embed text, write to a
database that may be slow or down. If these share one code path, the recorder's
failures become the proxy's failures.

## Decision

Two planes with a one-way dependency:

- The **data plane** ([proxy/relay.py](../../src/blackbox_ai/proxy/relay.py))
  authenticates, forwards, and streams the response straight back to the client.
  It tees a copy of the bytes to the telemetry plane and moves on.
- The **telemetry plane**
  ([telemetry/pipeline.py](../../src/blackbox_ai/telemetry/pipeline.py) plus
  the [stages](../../src/blackbox_ai/telemetry/stages.py)) drains a bounded
  queue in background workers: parse, embed, persist, cache-write.

The relay hands work off via `pipeline.submit()`, which is non-blocking and
never raises. Nothing the telemetry plane does can delay or fail a relayed
request.

## Consequences

- Telemetry is best-effort. A parser bug, an embedding outage, or an unreachable
  MongoDB degrades observability, not availability (see
  [ADR 0004](0004-drop-on-full-backpressure.md) for the queue-full policy).
- Capture must be cheap and infallible on the hot path; all expensive, fallible
  work is deferred to the workers.
- The two planes can be reasoned about, tested, and scaled independently.
