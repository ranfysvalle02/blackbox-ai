# Building an AI Gateway with Python and MongoDB

Every team shipping AI features eventually hits the same wall. You have three
SDKs (OpenAI, Anthropic, Gemini), four sets of API keys scattered across `.env`
files, no idea what your agents actually said to the model last Tuesday, and a
security team asking where all that prompt data lives. You bolt on a logging
wrapper here, a retry decorator there, a Redis cache somewhere else, and six
months later you have a pile of middleware nobody wants to own.

The cleaner answer is an **AI gateway**: a single service that sits between your
code and every model provider. Your apps point their SDKs at the gateway; the
gateway relays the traffic, captures what happened, enforces your policies, and
gives you one place to reason about cost, latency, and behavior.

This post is a guided tour of building one in **Python** with **MongoDB** as the
backbone. We won't ship a toy. We'll build the thing you'd actually deploy:
streaming, observable, encrypted, searchable, and secure by default. The full
project is open source ("Ghosts in the Code"); here we focus on *why* the
pieces fit together — and why MongoDB turns out to be a near-perfect fit for
this particular shape of problem.

---

## The one decision that shapes everything else

Before any code, you make a single architectural choice that determines whether
your gateway is a help or a liability: **what happens when your own
infrastructure fails?**

A gateway sits on the critical path. If your database hiccups, your logging
backend falls over, or your embedding provider rate-limits you, what happens to
the user's request? If the answer is "it fails," you've just made every AI
feature in your company less reliable than calling the provider directly. You've
become the bottleneck you were trying to remove.

So we split the system into two planes:

- The **data plane** is the relay. It takes the client's request, forwards it
  upstream, and streams the response back. It is ruthlessly simple and it is
  **fail-open**: nothing it depends on — not the database, not embeddings, not
  the cache — is allowed to break the proxy. If MongoDB is on fire, the user
  still gets their tokens.
- The **telemetry plane** is everything else: parsing the response, computing
  embeddings, writing to the database, populating the cache. It runs
  **out-of-band**, on a background worker pool, fed by an in-memory queue. If it
  falls behind or fails entirely, it drops work and logs it — it never reaches
  back and touches the request.

In Python this maps beautifully onto `asyncio`. The relay streams bytes to the
client while *teeing* a copy into a bounded `asyncio.Queue`. Workers drain the
queue and do the slow, fallible work. The handoff between planes is a single
non-blocking `put_nowait`:

```python
def submit(self, capture: RawCapture) -> None:
    try:
        self._queue.put_nowait(capture)
    except asyncio.QueueFull:
        self.metrics.dropped += 1          # shed load; never block the relay
        _log.warning("telemetry_queue_full")
```

That `except asyncio.QueueFull` is the whole philosophy in three lines. The
worst thing that happens under load is we lose a *recording* — never a
*response*. Keep this distinction sacred and the rest of the design gets easy.

---

## Why MongoDB, specifically

Here's the property that makes this problem hard with a traditional database:
**every provider speaks a different dialect, and the dialects keep changing.**

An OpenAI chat completion, an Anthropic message, a Gemini `generateContent`, an
embedding request, a streamed tool call — these have wildly different shapes.
Some have a `chain_of_thought`. Some have `tool_calls`. Some return usage stats
in the body, some in headers. Tomorrow a provider ships a new field and you find
out when it appears in production.

In a relational world, this is a migration treadmill: a wide table full of
nullable columns, or a `JSONB` escape hatch you end up querying awkwardly, or a
forest of join tables. In MongoDB, it's just... a document. Each captured
interaction becomes one polymorphic **Intent Document**: the normalized fields
you care about lifted to the top level, the raw provider payload preserved
verbatim alongside them, and whatever provider-specific extras simply *present
when they're present*.

```json
{
  "request_id": "…",
  "provider": "anthropic",
  "model_requested": "claude-sonnet-4",
  "timestamp": "2026-06-26T18:00:00Z",
  "project_id": "billing-service",
  "intent_telemetry": {
    "content": "Here's the refactor…",
    "chain_of_thought": "The user wants… so I'll…",
    "finish_reason": "end_turn"
  },
  "performance": { "latency_ms": 2310, "ttft_ms": 240 },
  "raw_payload": { "...": "the exact request body, for replay" },
  "embedding": [0.013, -0.041, ...]
}
```

One collection. No schema migration when a provider adds a field — you just
start capturing it. This is the document model earning its keep: the data is
genuinely semi-structured, so a database that treats documents as first-class
removes an entire category of busywork.

And then the same database keeps paying off. Because once those documents are in
MongoDB, the features you'd otherwise glue together from four different systems
are all *right there*: secondary indexes for your access patterns, a TTL index
for cache expiry, **Atlas Vector Search** for semantic queries, **Atlas Search**
for full-text, `$rankFusion` to combine them, and **Queryable Encryption** to
keep the sensitive bits encrypted the whole time. One data layer, not five.

---

## The async relay

The data plane is a single handler that serves *every* provider. There's no
per-provider code on the hot path — just a small declarative table describing
each backend (base URL, auth scheme, credential header) and a generic relay that
reads it.

The relay's job: rebuild the upstream URL from the client's path, swap in the
sovereign credential if you've configured one, stream the response straight
back, and mirror a copy to telemetry. Using `httpx` with `stream=True` keeps
memory flat even for long generations, and FastAPI's `StreamingResponse` hands
bytes to the client as they arrive:

```python
async def _stream_and_capture(self, response, buffer, ...):
    try:
        async for chunk in tee_stream(response.aiter_raw(), buffer.observe):
            yield chunk                      # → client, immediately
        completed = True
    finally:
        await response.aclose()
        capture = buffer.finalize(...)
        self._pipeline.submit(capture)       # → telemetry, out of band
```

`tee_stream` is a tiny generator, and the word "tee" usually sets off alarm
bells: forking an async byte stream into two independent consumers is exactly
where you get a slow reader stalling a fast one, or an unbounded fan-out buffer
quietly eating the heap on a long generation. This version dodges all of that by
*not* forking at all — there's one consumer (the client) and a side-effecting
callback:

```python
async def tee_stream(source, observe):
    async for chunk in source:
        observe(chunk)        # synchronous, bounded, non-raising
        yield chunk           # client pulls next → we advance
```

The `observe` argument is `buffer.observe`. It appends to a `bytearray` capped
at `max_capture_bytes` — past the cap it just flips a `truncated` flag instead of
growing without bound — stamps time-to-first-token on the first chunk, and never
raises. Because it's synchronous and allocation-cheap it never yields the event
loop, and because the generator only advances when the client pulls the next
chunk, capture inherits the client's backpressure for free: no second queue, no
second task, no way for the recording to outrun (or back up) the response. The
client sees native streaming latency; the recording assembles itself as a free
side effect. The `finally` block then guarantees we finalize and submit the
capture even if the client disconnects mid-stream — that's a data point you very
much want ("the user bailed after 12 seconds").

For the database driver, we use **PyMongo's native async API**
(`AsyncMongoClient`). Motor — the old async wrapper — reached end-of-life, and
PyMongo's built-in async support is its recommended replacement. It's
`async`/`await` all the way down, so the telemetry workers never block the event
loop while writing batches.

---

## Time-travel debugging with vector search

Here's where the gateway stops being a proxy and starts being a superpower.

You've captured thousands of AI interactions. A bug report comes in: "the agent
keeps trying to delete the wrong file." You don't know the request ID. You don't
know the timestamp. You know the *vibe* of what went wrong. Traditional logging
is useless here — you can't `grep` for a vibe.

But you can search by *meaning*. When each Intent Document is captured, a
telemetry worker embeds its most meaningful text — the model's reasoning if it
exposed any, otherwise its output, otherwise the user's prompt — using
[Voyage AI](https://www.voyageai.com/) (a MongoDB company; `voyage-code-3` is
tuned for code and agent intent). That vector is stored right on the document.

Then "find me moments like this" is one aggregation stage:

```python
pipeline = [
    {"$vectorSearch": {
        "index": "intent_vector_index",
        "path": "embedding",
        "queryVector": await embedder.embed_query("deleting the wrong file"),
        "numCandidates": 100,
        "limit": 5,
        "filter": {"project_id": "billing-service"},
    }},
    {"$project": {"intent_telemetry": 1, "timestamp": 1,
                  "score": {"$meta": "vectorSearchScore"}}},
]
```

Crucially, the embedding lives *in the same collection* as the data. There's no
separate vector database to keep in sync, no dual-write consistency problem, no
"the vector store says this document exists but Postgres disagrees." You query
your operational data and your semantic index in one place, with metadata
pre-filters (`project_id`, `session_id`) applied right inside the vector search.
That's the kind of thing that's a weekend project to build badly and a
multi-system headache to build well — unless your database does both natively.

---

## Hybrid search: meaning *and* keywords

Vector search is brilliant at "things that mean similar things." It's terrible
at "this exact model name" or "this specific project." Sometimes you want both:
the semantic recall of vectors *and* the precision of full-text matching.

That's hybrid search, and on MongoDB 8.1+ it's a single stage: `$rankFusion`.
You give it multiple ranked pipelines and it fuses their results with
reciprocal-rank fusion, weighting each leg:

```python
{"$rankFusion": {
    "input": {"pipelines": {
        "vector": [vector_search_stage],          # semantic similarity
        "text":   [{"$search": {...}}, {"$limit": k}],  # keyword precision
    }},
    "combination": {"weights": {"vector": 0.7, "text": 0.3}},
}}
```

One query, two retrieval strategies, fused into a single ranked list. No
application-side merging, no fragile "run two queries and interleave the results
by hand" code. And if you deploy against an older cluster that can't run
`$rankFusion`, the service catches the `OperationFailure` and transparently
falls back to vector-only — the call still works, it just degrades gracefully.

(There's a subtlety we'll get to in a moment: the *text* leg searches plaintext
metadata, because the juiciest fields are encrypted. Which brings us to the most
interesting part.)

---

## Keeping the crown jewels encrypted — without giving up search

Prompts and model outputs are some of the most sensitive data your company
handles. They contain source code, customer records, internal strategy, the
occasional pasted-in password. Storing all of that in plaintext in a database is
a breach waiting to happen, and "we encrypt the disk" doesn't help when the
threat model includes a leaked connection string or an over-privileged admin.

MongoDB's **Queryable Encryption (QE)** solves this at the right layer:
fields are encrypted **client-side**, by the driver, *before* they ever leave
your process. The database stores ciphertext and literally cannot read the
plaintext — it never has the keys. We encrypt the crown jewels (`raw_payload`
and the model's `content` / `chain_of_thought`) and leave routing metadata
(provider, model, project) in the clear.

The beautiful part is how little application code this takes. You declare which
fields are encrypted once, and the auto-encrypting client handles the rest —
writes get encrypted, reads get decrypted, all transparently:

```python
_INTENT_ENCRYPTED_FIELDS = (
    ("raw_payload", "object"),
    ("intent_telemetry.content", "string"),
    ("intent_telemetry.chain_of_thought", "string"),
)
```

And the gateway never hoards raw key material to make this work. QE uses a
two-level hierarchy: the per-field Data Encryption Keys (DEKs) live — themselves
encrypted — in a MongoDB key vault, and the single master key that wraps them is
held in an external KMS, orchestrated via AWS KMS, Azure Key Vault, GCP KMS, or
KMIP/HashiCorp Vault in production (the local key is strictly a dev convenience).
So the one secret that can unlock everything is owned by your KMS and never
written to the gateway's disk.

Now connect the dots with the previous sections. The free-text fields are
ciphertext at rest — so they can't be full-text indexed. But the **embedding**
is computed in your process *before* encryption and stored as a plain vector. So
vector search keeps working perfectly on encrypted data: the meaning is
preserved in a vector the database can index, while the words themselves stay
locked. That's why hybrid search uses vectors for semantics and full-text only
for the plaintext metadata. The architecture isn't fighting the encryption —
it's designed around it.

And we make QE **fail-closed**: if encryption is enabled but the key or the
`crypt_shared` library is missing, the gateway refuses to start rather than
quietly writing plaintext. Security defaults should fail loud, not silent.

---

## A cache that cleans up after itself

LLM calls are slow and they cost money. If two requests are byte-for-byte
identical, serving the second from cache is free latency and free dollars.

The naive version of this is a background job that sweeps expired entries. The
MongoDB version is a **TTL index**: you tell the collection "expire documents
this many seconds after their timestamp," and the server quietly deletes them
for you. Zero cron jobs, zero cleanup code.

```python
await collection.create_index("created_at", expireAfterSeconds=3600)
```

The lookup itself stays on-brand with the fail-open philosophy: it's
time-bounded with `asyncio.wait_for`, so a slow cache read can never stall the
relay. If the lookup doesn't return within a few hundred milliseconds, we give
up and forward upstream. The cache is an optimization, never a dependency.

---

## Secure by default, because you'll forget otherwise

The last 10% is what separates a demo from something you deploy at a company.
The pattern that matters: **make the secure choice the default, and make the
insecure choice loud.**

A few things the gateway does out of the box:

- **A deployment profile.** Set `GATEWAY_ENV=production` and the gateway enforces
  client auth automatically and runs a startup self-check that *fails fast* on an
  insecure config — refusing to boot as an open relay if you forgot to set
  access tokens. In `dev` it stays permissive but logs a loud warning so you're
  never quietly exposed.
- **Per-client rate limiting.** A sliding-window limiter (keyed by gateway token,
  or client IP when anonymous) blunts single-source abuse; over-limit callers get
  a clean `429` with a `Retry-After` instead of melting your provider quota.
- **Backpressure.** A hard in-flight cap bounds worst-case memory: each request
  buffers a bounded body and capture, so concurrency × size is your ceiling.
  Excess requests get a fast `503` rather than dragging everyone down.
- **Constant-time token comparison** (`hmac.compare_digest`), secrets wrapped in
  `SecretStr` so they're masked in logs, and security headers on every response.
- **Prometheus metrics** at `/metrics`: request counts, latency and
  time-to-first-token histograms, the in-flight gauge, rate-limit rejections, and
  live telemetry-queue depth — so you can actually see the two planes working.

None of these are exotic. They're the boring, correct defaults that you'd
*intend* to add and then never quite get to. Baking them in is the difference
between "a cool side project" and "the thing the platform team standardizes on."

---

## The shape of the whole thing

Step back and look at what we built, and notice how much of it is *MongoDB doing
the heavy lifting*:

| Need | Bolt-on approach | With MongoDB |
| --- | --- | --- |
| Store wildly varied provider payloads | Wide nullable tables / JSON columns | One polymorphic document collection |
| Semantic search over interactions | Separate vector database + sync | `$vectorSearch` on the same documents |
| Keyword + semantic together | App-side result merging | `$rankFusion` in one query |
| Protect sensitive prompt data | App-layer crypto + key plumbing | Queryable Encryption in the driver |
| Expire cached responses | Cron sweeper | TTL index |
| Non-blocking writes | Threadpool gymnastics | Native async PyMongo |

Six systems collapse into one data layer. That's not an accident of this
particular project — it's what happens when the database's data model actually
matches the shape of your data, and the database keeps growing capabilities
(search, vectors, encryption) into that same model instead of forcing you out to
a constellation of specialized services.

Python gives you the async glue — a streaming relay that tees its traffic, a
worker pool that does the slow work off the hot path, and `asyncio` primitives
that make "fail-open" a three-line idea instead of a distributed-systems essay.

If you're building anything that sits in front of AI models — a gateway, an
agent platform, an internal proxy — start with the two-plane split, pick a data
layer that won't fight your schema, and make the secure default the easy one.
The rest is surprisingly little code.

*The full implementation, with tests and a one-command Docker setup, is in the
[Ghosts in the Code](./blog.md) project.*
