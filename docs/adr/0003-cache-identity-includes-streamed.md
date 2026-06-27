# 3. Cache identity is (key, streamed), not just key

- Status: Accepted
- Date: 2026-06-26

## Context

The same logical request can be answered in two incompatible wire formats: a
single JSON document, or a Server-Sent-Events stream of deltas. The content hash
deliberately ignores the `stream` flag, so a streamed and a non-streamed call to
the same model share one logical key (see
[cache/keys.py](../../src/blackbox_ai/cache/keys.py)). If the cache were keyed
on that hash alone, a stored SSE body could be replayed to a client that asked
for a single JSON object - a corrupt response the client's SDK cannot parse.

## Decision

A cached response is identified by the pair `(key, streamed)`, modelled as a
single frozen value object, `CacheIdentity`, in
[cache/keys.py](../../src/blackbox_ai/cache/keys.py). The store's compound
index is `(cache_key, streamed)`; lookup and write both take a `CacheIdentity`.

The gate derives the lookup format from what the client is asking for (body
`"stream": true`, or Gemini's `:streamGenerateContent` path); the write uses the
format actually observed on the upstream response.

## Consequences

- A streaming request can never hit a non-streaming entry, or vice versa - the
  invariant is carried by the type, not by remembering to pass two arguments
  consistently.
- The two formats are cached independently and may both be warm at once.
- Format inference lives in one place (`CacheGate`), so adding a provider's
  streaming signal is a single edit.
