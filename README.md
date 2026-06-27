# ghosts-in-the-code

---

# The Ghost in the Codebase: Managing the "Intent Trap" in the Age of AI Development

Picture a standard Tuesday morning: you pour a cup of coffee and open your laptop to find that your team's autonomous AI coding assistant spent the night building an entire microservice. It generated 15,000 lines of pristine, highly optimized code across 35 different files.

The system runs flawlessly. Productivity is at an all-time high.

But three weeks later, a bizarre edge-case bug brings production to a halt. You open the repository to fix it, and reality sets in: no human on your team actually understands this code. The architecture relies on abstractions and internal logic that no one on your staff designed. To safely debug it under pressure, you have to do the one thing you hoped to avoid: you have to ask the AI tool what it was thinking when it wrote it.

This is the reality of **AI Overwhelm**—a state where enterprises are rapidly scaling software faster than human developers can comprehend it. But beneath this operational challenge lies a deeper strategic shift, one that presents a massive window of opportunity for enterprises looking to secure their long-term digital sovereignty.

---

## Beyond Git: The Battle for "Coding Intent"

Historically, engineering teams have relied on Git to track the evolution of software. If you want to know why a line changed, you look at a Git commit. But Git only records the *final state* of the code, not the messy process of creation. It shows that line 42 was modified, accompanied by a brief human note: `"fixed login bug."`

In an ecosystem driven by AI-generated software, Git is no longer a sufficient flight recorder. When an AI agent builds software, it generates an immense trail of context: multi-turn prompt conversations, alternative architectures it evaluated and discarded, and its internal **Chain-of-Thought (CoT)** reasoning.

A recent [Business Insider analysis](https://www.businessinsider.com/openai-anthropic-ai-coding-database-intent-samuel-colvin-pydantic-2026-6) highlighted this exact paradigm shift. In an interview, Samuel Colvin, the creator and CEO of the widely used Python data-validation framework Pydantic, pointed out that the economics of the AI landscape are moving away from a raw performance race.

> *"A year ago, what they cared about was revenue,"* Colvin noted. *"Now, when one assumes they're both trying to IPO, their profit margin becomes really important."*

Because competing solely on frontier model intelligence is an extraordinarily expensive game of leapfrog, major AI providers are looking for structural moats. As Colvin explained, tech providers are *"doing their very best to find ways of locking people in that are not related to model quality."*

This strategy centers around owning the **"Database of Coding Intent."** To use a construction analogy:

* **Git** is a photo album showing major milestones (the foundation was poured, the roof went up).
* **The Database of Coding Intent** is a 24/7 audio and video recording of every single conversation the construction workers had on-site, coupled with a record of the architect's exact thoughts.

If your AI assistant writes 15,000 lines of code, the only way a human can maintain it over time is by querying that 24/7 recording. But if that historical context lives exclusively inside a third-party provider's walled garden, moving your codebase to an open-source or competitor model becomes incredibly difficult. Your code becomes too massive for humans to understand, and too context-dependent for a rival AI to fix blindly. The vendor lock-in trap quietly snaps shut.

---

## The Strategy: Capture and Replay

The solution isn't to pull the plug on AI productivity; it's to decouple the AI *model* from the AI *context*.

Forward-thinking enterprises are already moving to counter this ecosystem gravity. As Business Insider noted, companies like Walmart have developed internal frameworks (such as their "Code Puppy" system) explicitly designed to maintain model flexibility, control token costs, and avoid single-provider dependence.

By building an internal telemetry layer that intercepts and saves your own LLM prompts, metadata, and chains-of-thought, your enterprise retains its own sovereign vault of intellectual property.

The true architectural value of this approach is **Replayability**.

If a more efficient, cost-effective, or specialized AI model enters the market tomorrow, you aren't stranded. Because you own the complete historical trajectory of your codebase, you can "replay" that history into a new vendor's model. The new model instantly inherits the deep, institutional context of your software's evolution, allowing you to swap backends seamlessly without losing a shred of knowledge.

---

## The Architectural Blueprint

Building an effective, sovereign "AI Flight Recorder" requires a data layer capable of handling structural chaos, semantic querying, and absolute data privacy. Implementing this kind of architecture points toward a specific set of data capabilities—which is why developers are increasingly leveraging flexible document stores like **MongoDB Atlas** to anchor their AI telemetry.

Here is how those specific database mechanics solve the core challenges of intent tracking:

### 1. Document Flexibility for Polymorphic "Thought Logs"

An AI’s train of thought cannot be neatly mapped to a rigid, relational SQL schema. One prompt might involve a simple text string; the next might return a massive JSON tree of alternative architectural choices, token logs, and tool-calling parameters. MongoDB’s document model handles this variability natively. You can store the entire trajectory of a coding session in a single, polymorphic document—nested with all its rich metadata—without the friction of constant database migrations.

### 2. Unified Text and Vector Search

If an edge-case bug occurs six months from now, engineers shouldn't be digging through raw text logs. By storing intent telemetry in MongoDB Atlas, teams can utilize native **Atlas Search** and **Vector Search** within the same database layer. This allows developers to query their history semantically:

> *"Find me all past generations where the assistant expressed hesitation about the security of our database connection pooling."*

The database retrieves the exact historical context needed to diagnose the issue, or to prime a completely different AI model during a provider migration.

### 3. Scaling for High-Volume Telemetry

Autonomous AI agents are incredibly verbose. A single afternoon of automated coding can consume millions of tokens and generate gigabytes of log data. MongoDB’s ability to scale horizontally ensures that logging this data doesn't become a performance bottleneck. Furthermore, its **Aggregation Pipeline** allows teams to transform raw telemetry into operational insights, such as tracking token costs or identifying which microservices require the most prompt revisions.

### 4. Client-Side Queryable Encryption

The ultimate hurdle to tracking coding intent is security; your source code and the logic behind it are your company's crown jewels. Storing this telemetry in the cloud requires absolute privacy.

MongoDB’s **Queryable Encryption** addresses this by allowing organizations to encrypt sensitive fields—like raw system prompts or proprietary algorithms—on the client side *before* the data ever leaves the application server. The data remains encrypted at rest, in transit, in logs, and in backups. Crucially, teams can still run fast, expressive queries on that encrypted data without ever exposing the plaintext to the database server itself.

---

## Retaining Digital Sovereignty

The future of software engineering is no longer just about managing source code; it's about managing the **intent** behind that code.

As the AI market shifts toward enterprise retention strategies, letting external platforms hold the exclusive record of *why* your software works means slowly surrendering control of your digital infrastructure. By architecting a sovereign telemetry layer on a flexible foundation like MongoDB Atlas, enterprises can enjoy the massive productivity gains of modern AI tools while keeping their options completely open.

Don't let the walled gardens capture your codebase. Build your vault, secure your history, and ensure that the future of your software remains entirely in your hands.

---

# Blackbox AI

[![CI](https://github.com/ranfysvalle02/ghosts-in-the-code/actions/workflows/ci.yml/badge.svg)](https://github.com/ranfysvalle02/ghosts-in-the-code/actions/workflows/ci.yml)

This repository implements that vault: a **native pass-through LLM gateway** built on FastAPI and MongoDB. It sits between your developers/agents and the frontier providers, streams every response back with **zero added latency**, and captures a rich, queryable **Intent Document** for every interaction - entirely out-of-band and fail-open.

Supported providers: **OpenAI, Azure OpenAI, Anthropic, Google Gemini, Ollama.**

## The core idea: two planes that cannot break each other

The architecture is deliberately split so the part that is allowed to fail can never take down the part that must not.

```mermaid
flowchart LR
    SDK["Native SDK (OpenAI / Anthropic / Gemini / Ollama)"] -->|"base_url -> gateway"| Relay
    subgraph dataPlane [Data Plane: must never break]
      Relay["Generic Relay (httpx stream)"] -->|"forward native request"| Upstream["LLM Provider"]
      Upstream -->|"stream chunks"| Relay
      Relay -->|"instant tokens"| SDK
    end
    Relay -.->|"tee copy, fail-open"| Queue[("asyncio.Queue")]
    subgraph telemetryPlane [Telemetry Plane: safe to fail]
      Queue -.-> Worker["Telemetry Workers"]
      Worker -.-> Parser["Per-provider parser"]
      Parser -.-> Sink["Mongo Sink (bulk)"]
      Sink -.-> Mongo[("MongoDB Atlas Local 8.3")]
    end
```

- **Data plane** - a single generic relay forwards the native request, injects sovereign credentials, and streams the response straight back. It never parses, never blocks on the database.
- **Telemetry plane** - a bounded queue drained by worker tasks parses a *copy* of the stream and bulk-writes Intent Documents to MongoDB. If MongoDB is down, the queue is full, or a parser hits a bug, the client's request still completes. Telemetry is best-effort and isolated.

Adding a provider is additive: one `ProviderConfig` entry in [src/blackbox_ai/providers/catalog.py](src/blackbox_ai/providers/catalog.py) plus one parser in [src/blackbox_ai/telemetry/parsers/](src/blackbox_ai/telemetry/parsers/).

## The two-line drop-in

Point your existing, unmodified SDK at the gateway. No code rewrite, no new SDK to learn.

```python
# OpenAI
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/openai/v1", api_key="gateway-token")

# Anthropic
from anthropic import Anthropic
client = Anthropic(base_url="http://localhost:8000/anthropic", api_key="gateway-token")

# Google Gemini (google-genai)
from google import genai
from google.genai import types
client = genai.Client(api_key="gateway-token",
                      http_options=types.HttpOptions(base_url="http://localhost:8000/gemini"))

# Azure OpenAI
from openai import AzureOpenAI
client = AzureOpenAI(azure_endpoint="http://localhost:8000/azure",
                     api_key="gateway-token", api_version="2024-10-21")

# Ollama
import ollama
client = ollama.Client(host="http://localhost:8000/ollama")
```

If you configure a provider's real key in the gateway's environment, clients only ever need a gateway token and the sovereign key never leaves your infrastructure. If you leave it unset, the gateway transparently forwards whatever credential the client already sends (zero-config relay).

## Auto-injected context (no logging code required)

Send these optional headers and the gateway groups telemetry for you - no instrumentation in your app:

| Header | Field | Purpose |
| --- | --- | --- |
| `X-Project-ID` | `project_id` | Group telemetry by microservice / repository |
| `X-Agent-Session` | `session_id` | Tie one autonomous run together |
| `X-Developer-ID` | `developer_id` | Attribute cost / usage per engineer |
| `X-Request-ID` | `request_id` | Correlate a single hop (generated if absent) |

## Quickstart

The gateway is **secure-by-default**: Queryable Encryption is on, so it needs a
key (and a QE-capable MongoDB - the bundled Atlas Local image qualifies).

```bash
# 1. Mint a local encryption key into .env (QE is on by default)
make gen-key

# 2. Start the gateway + MongoDB Atlas Local 8.3, then bootstrap storage
make up                # docker compose up --build -d
make init              # encrypted collections, indexes, vector + text indexes

# 3. Drive every configured provider via its native SDK
export OPENAI_API_KEY=sk-...   # set whichever you have
make demo                      # uv run python examples/demo.py
```

Just kicking the tires on a plain MongoDB? Set `GATEWAY_ENCRYPTION_ENABLED=false`
in `.env` to skip QE. For production, set `GATEWAY_ENV=production` (enforces auth
and fails fast on an insecure config).

Health probes: `GET /healthz` (liveness) and `GET /readyz` (MongoDB + worker readiness, with live metrics).

## Configuration

Copy [.env.example](.env.example) to `.env`. Highlights:

| Variable | Default | Meaning |
| --- | --- | --- |
| `GATEWAY_ENV` | `dev` | `production` enforces auth + fails fast on an insecure config |
| `GATEWAY_MONGO_URI` | `mongodb://localhost:27017/?directConnection=true` | The sovereign vault |
| `GATEWAY_REQUIRE_AUTH` / `GATEWAY_TOKENS` | `false` / - | Enforce gateway tokens (always on in `production`) |
| `GATEWAY_RATE_LIMIT_ENABLED` / `_REQUESTS` / `_WINDOW_S` | `true` / `120` / `60` | Per-client sliding-window rate limit (429 + `Retry-After`) |
| `GATEWAY_ENCRYPTION_ENABLED` / `GATEWAY_ENCRYPTION_KEY` | `true` / - | Queryable Encryption (on by default, fail-closed without a key) |
| `GATEWAY_EMBEDDINGS_PROVIDER` / `VOYAGE_API_KEY` | `voyage` / - | Voyage AI embeddings for vector + hybrid search (active once a key is set) |
| `GATEWAY_VECTOR_INDEX_NAME` / `GATEWAY_SEARCH_INDEX_NAME` | `intent_vector_index` / `intent_text_index` | Atlas Vector + Search indexes backing hybrid `$rankFusion` |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `AZURE_OPENAI_API_KEY`/`AZURE_OPENAI_ENDPOINT`, `OLLAMA_BASE_URL` | - | Sovereign provider credentials / endpoints |
| `GATEWAY_MAX_REQUEST_BYTES` / `GATEWAY_MAX_CONCURRENT_REQUESTS` | `10 MiB` / `100` | Data-plane memory backpressure (413 on oversized bodies, 503 at the in-flight cap) |
| `GATEWAY_TELEMETRY_*` | see file | Queue size, worker count, batch size/interval, capture cap |

> Secrets (provider keys, `GATEWAY_TOKENS`, `GATEWAY_ADMIN_TOKEN`, `GATEWAY_ENCRYPTION_KEY`) are held as `SecretStr` and masked in logs/repr; gateway-token comparison is constant-time.

## Operations

- **Metrics**: `GET /metrics` exposes Prometheus exposition - request-path instruments (`blackbox_relay_requests_total`, request/TTFT latency histograms, the in-flight gauge, and rejections) plus live telemetry-plane counters and queue depth (`blackbox_telemetry_*`). It is open by default for scraping; set `GATEWAY_METRICS_PROTECTED=true` (with `GATEWAY_ADMIN_TOKEN` set) to require the admin token, or front it with network policy.
- **Backpressure**: each in-flight request can hold up to `GATEWAY_MAX_REQUEST_BYTES + GATEWAY_TELEMETRY_MAX_CAPTURE_BYTES` in memory, so `GATEWAY_MAX_CONCURRENT_REQUESTS` bounds worst-case data-plane memory. Excess requests fail fast with `503 service_overloaded` and a `Retry-After` header rather than queueing.
- **Auth in passthrough**: when no sovereign key is configured for a provider, the gateway authenticates only via the dedicated `x-gateway-token` header (stripped before forwarding), so a client's real upstream credential in `Authorization` is never consumed or leaked as the gateway secret.
- **Deployment profile**: `GATEWAY_ENV=production` makes the gateway secure-by-default - client auth is always enforced and startup runs a self-check that **fails fast** on an insecure config (e.g. missing `GATEWAY_TOKENS`) and warns on risky ones (QE or rate limiting off). `dev` stays permissive but logs a loud warning when the relay is open.
- **Rate limiting**: a per-client sliding-window limiter (keyed by gateway token, else client IP) blunts single-source abuse; over-limit callers get `429 rate_limit_exceeded` + `Retry-After` (counted as `blackbox_relay_rate_limited`).
- **Embedding resilience**: the Voyage embedder is fail-open per batch and fronted by a circuit breaker - after `GATEWAY_EMBEDDING_BREAKER_THRESHOLD` (5) consecutive batch failures it stops calling the provider for `GATEWAY_EMBEDDING_BREAKER_COOLDOWN_S` (30s), then half-opens a trial. Documents are still written during an outage, just without vectors (re-embed later); no telemetry is lost.
- **Security headers**: every response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy: no-referrer` (added only when absent, so relayed upstream headers are preserved).
- **Hybrid search**: `POST /admin/search` (and `blackbox-ai search`) default to `mode: "hybrid"`, fusing `$vectorSearch` (semantic) with an Atlas `$search` full-text leg over plaintext metadata via `$rankFusion`. Under QE the free-text fields are ciphertext and can't be text-indexed, so the vector leg carries meaning while the text leg sharpens metadata matches; the call transparently falls back to vector-only if the cluster lacks `$rankFusion`.

### Scaling horizontally

The two abuse controls are **per-process by design**, which is the correct semantics for each:

- **Concurrency cap** (`GATEWAY_MAX_CONCURRENT_REQUESTS`) bounds *this process's* memory (each in-flight request holds the buffered body + capture). Memory is inherently per-replica, so a per-process cap is exactly right - set it from each pod's memory budget and let your orchestrator scale replicas.
- **Rate limiter** is per-replica defense-in-depth, not a global quota. For a true cross-replica limit, enforce it at the edge (ingress / API gateway), **or** implement the `RateLimiter` protocol ([src/blackbox_ai/security/rate_limit.py](src/blackbox_ai/security/rate_limit.py)) against a shared store (e.g. Redis) - the relay depends only on that seam, so it is a drop-in with zero relay changes.

### Extending

- **KMS**: swap the local key for a managed KMS by changing only `kms_providers` and the per-DEK `master_key` ([src/blackbox_ai/security/encryption.py](src/blackbox_ai/security/encryption.py)) - see [Production security](#production-security-swap-the-local-key-for-a-cloud-kms) for AWS / Azure / GCP / KMIP snippets.
- **Distributed rate limiting**: implement `RateLimiter` against a shared backend (above).
- **Tracing**: OTLP tracing is a clean future add at the relay/worker boundary (the `Relay.handle` span and the telemetry-pipeline `submit`). It is intentionally not bundled (a heavy dependency and instrumentation surface), but the seam is already there.

## The Intent Document

Every interaction becomes one polymorphic, indexable document. The request `raw_payload` is preserved verbatim for replay; normalised fields are lifted alongside it. Indexes: compound `(project_id, timestamp)`, single `session_id`, and a partial index on `developer_id`. The `embedding`, `embedding_model`, `cache_key`, and `served_from_cache` fields power the [Phase 4 sovereign features](#sovereign-features-vector-search-token-caching-queryable-encryption) below.

## Quality bar

- **Strict typing** - `mypy --strict` clean across the codebase.
- **No blind exceptions** - enforced by `ruff` (`BLE`); every failure path catches specific, typed exceptions.
- **Tested** - unit tests for streaming fidelity, fail-open behaviour (MongoDB down still returns 200), per-provider parsers, plus an integration test against a live `mongodb-atlas-local:8.3`.

```bash
make check       # ruff + mypy + unit tests
make test-int    # integration tests (set MONGO_TEST_URI)
```

> Driver note: this project uses **PyMongo's native async API** (`AsyncMongoClient`). The Motor driver reached end-of-life in 2026 and PyMongo async is its officially recommended replacement, so the plan's intent (a non-blocking async MongoDB driver) is honoured with the current best-practice driver.

## Project layout

```
src/blackbox_ai/
  main.py            # app factory + lifespan (Mongo, pipeline, HTTP client)
  bootstrap.py       # shared component construction for server + CLI
  cli.py             # serve / gen-key / init / search dispatcher
  config.py          # pydantic-settings configuration
  search.py          # vector + hybrid ($rankFusion) search (auto-decrypts under QE)
  providers/         # ProviderConfig, catalog, registry (one entry per backend)
  proxy/             # the generic fail-open relay + streaming tee (data plane)
  telemetry/         # capture buffer, queue/workers, parsers, embeddings, sink
  cache/             # canonical request keys + TTL-indexed cache store
  security/          # Queryable Encryption manager + per-client rate limiter
  metrics.py         # Prometheus instruments + telemetry-plane collector
  db/                # AsyncMongoClient + vector & full-text index setup
  middleware/        # sovereign metadata -> context, security headers, logs
  api/               # health, metrics, admin search, the catch-all relay route
examples/demo.py     # all five native SDKs pointed at the gateway
tests/               # unit + integration tests with recorded fixtures
```

---

# Sovereign Features: Vector Search, Token Caching, Queryable Encryption

Phase 4 adds the three capabilities that turn the flight recorder into a sovereign
vault. All three are **opt-in** and **independently toggleable**; with them off the
gateway behaves exactly as above. Critically, the heavy work stays in the
**telemetry plane** - only the (optional) cache *lookup* touches the request path,
and it is time-bounded and fail-open.

```mermaid
flowchart LR
    SDK["Native SDK"] --> Relay
    subgraph dataPlane [Data Plane]
      Relay -->|"cache lookup (opt-in, fail-open)"| Cache[("cache coll (TTL)")]
      Cache -->|"HIT: replay stored stream"| Relay
      Relay -->|"MISS: forward"| Upstream["LLM Provider"]
      Upstream --> Relay --> SDK
    end
    Relay -.->|"tee + cache_key"| Queue[("queue")]
    subgraph telemetryPlane [Telemetry Plane]
      Queue -.-> Worker["Workers"]
      Worker -.->|"embed batch"| Voyage["Voyage AI"]
      Worker -.->|"write cache entry on miss"| Cache
      Worker -.->|"encrypting client (auto QE)"| Mongo[("MongoDB: intents + vector index")]
    end
    CLI["blackbox-ai search"] -.->|"$vectorSearch + auto-decrypt"| Mongo
```

## Self-host quickstart (sovereign mode)

The Docker image bakes in MongoDB's `crypt_shared` library, so turning on
encryption is a pure config flip. End to end:

```bash
cp .env.example .env

# 1. Generate a local 96-byte master key (appended to .env), then enable QE.
make gen-key
#    set GATEWAY_ENCRYPTION_ENABLED=true in .env
#    (optionally) set GATEWAY_EMBEDDINGS_PROVIDER=voyage + VOYAGE_API_KEY,
#                 GATEWAY_CACHE_ENABLED=true, GATEWAY_ADMIN_TOKEN=<token>

# 2. Bring up the gateway + MongoDB Atlas Local 8.3.
make up

# 3. Bootstrap encrypted collections, indexes, TTL, and the vector index.
make init

# 4. Time-travel through your agents' intent.
make search q="why did the agent change the connection pool limits?"
```

`make init` / `make search` run inside the gateway container (where `crypt_shared`
lives). You can also call the admin API directly:

```bash
curl -s http://localhost:8000/admin/search \
  -H "X-Admin-Token: $GATEWAY_ADMIN_TOKEN" \
  -H "content-type: application/json" \
  -d '{"query":"hesitation about database connection pooling","k":5,"project_id":"auth-service-migration"}'
```

## 1. Vector "time-travel" search

Each captured Intent Document is embedded (in a worker, off the hot path) with
**Voyage AI** `voyage-code-3` (1024-dim) - Voyage is a MongoDB company and the
model is tuned for code/agent reasoning. The embedded text prefers the model's
hidden **chain-of-thought**, then its visible **content**, then the user prompt.
An Atlas **`vectorSearch`** index over the `embedding` field (with `project_id`,
`session_id`, `provider`, `developer_id` as pre-filters) powers semantic recall:

> *"Find me all past generations where the assistant expressed hesitation about
> the security of our database connection pooling."*

- **Backend**: `GATEWAY_EMBEDDINGS_PROVIDER=voyage` + `VOYAGE_API_KEY`. Pluggable
  via the `Embedder` protocol - a local model can be added without touching the
  pipeline. With embeddings off, everything else still works.
- **Fail-open**: an embedding error simply writes the document without a vector
  (it is invisible to vector search until re-embedded), never losing telemetry.
- **Tuning**: `GATEWAY_EMBEDDING_DIMS` (256/512/1024/2048 for `voyage-code-3`)
  and `GATEWAY_VECTOR_INDEX_NAME`. The service requests `numCandidates =
  max(k*20, 100)`; raise it for higher recall at the cost of latency.

> **Atlas-native alternative.** If you prefer not to call an external embedding
> API, MongoDB Atlas can auto-embed via an **Atlas Vector Search auto-embedding**
> index (server-side embedding on `voyage-code-3`). To use it, leave
> `GATEWAY_EMBEDDINGS_PROVIDER=none`, store the raw text, and define the index
> with an auto-embedding `text` field instead of a `vector` field. This gateway
> ships the client-side path by default because it keeps the deployment fully
> self-contained and provider-agnostic.

## 2. Token caching (exact-match, opt-in)

A normalized hash of `provider + method + path + body` (minus the `stream` flag)
keys an exact-match cache backed by a TTL-indexed collection.

- **Opt-in** per request via `X-Intent-Cache: on`, or flip
  `GATEWAY_CACHE_DEFAULT_ON=true` to cache every eligible request. Only JSON-body
  `POST`s (the shape of every generation call) are cacheable.
- **Format-safe**: the entry's identity is `(cache_key, streamed)`, so an SSE
  response is never replayed to a client expecting a single JSON document, or
  vice versa.
- **Fail-open and off the hot path**: the lookup is bounded by
  `GATEWAY_CACHE_LOOKUP_TIMEOUT_S` (default 250ms) and any error/timeout falls
  through to upstream. The cache *write* happens in a worker. Responses carry
  `X-Intent-Cache: HIT|MISS`, and a HIT is still recorded as telemetry with
  `served_from_cache: true`.
- **Tuning**: `GATEWAY_CACHE_TTL_S`, `GATEWAY_CACHE_COLLECTION`.

## 3. Queryable Encryption (client-side)

With `GATEWAY_ENCRYPTION_ENABLED=true`, the crown-jewel fields are encrypted
**client-side, before they reach MongoDB**, via automatic Queryable Encryption:

| Collection | Encrypted (random, unqueryable) | Stays plaintext (queryable) |
| --- | --- | --- |
| `intents` | `raw_payload`, `intent_telemetry.content`, `intent_telemetry.chain_of_thought` | routing metadata, `performance`, `embedding` |
| `cache` | `response_body`, `request_payload` | `cache_key`, `streamed`, `expires_at` |

- **Local KMS for self-hosting**: one base64 96-byte master key
  (`GATEWAY_ENCRYPTION_KEY`, via `make gen-key`) wraps the per-field Data
  Encryption Keys. The Docker image bakes in `crypt_shared` and sets
  `GATEWAY_CRYPT_SHARED_LIB_PATH`.
- **Fail-closed**: if encryption is enabled but the key is invalid/missing or
  `crypt_shared` is absent, the gateway **refuses to start** rather than silently
  writing plaintext.
- **Vector search still works**: the `embedding` field stays in plaintext, so
  `$vectorSearch` operates on the encrypted `intents` collection and returns the
  matching documents with their text **decrypted client-side** - sovereign search
  over sovereign data. *(Verified against `mongodb-atlas-local:8.3`.)*

### Production security: swap the local key for a cloud KMS

The local key is ideal for development and self-hosting. For production, point the
encryption manager at a managed KMS - the only change is `kms_providers` and the
per-DEK `master_key` ([src/blackbox_ai/security/encryption.py](src/blackbox_ai/security/encryption.py)):

```python
# AWS KMS
kms_providers = {"aws": {"accessKeyId": "...", "secretAccessKey": "..."}}
master_key = {"region": "us-east-1", "key": "arn:aws:kms:us-east-1:...:key/..."}

# Azure Key Vault
kms_providers = {"azure": {"tenantId": "...", "clientId": "...", "clientSecret": "..."}}
master_key = {"keyVaultEndpoint": "myvault.vault.azure.net", "keyName": "blackbox-ai"}

# GCP KMS
kms_providers = {"gcp": {"email": "...", "privateKey": "..."}}
master_key = {"projectId": "...", "location": "global",
              "keyRing": "blackbox-ai", "keyName": "master"}

# KMIP (e.g. HashiCorp Vault, Fortanix)
kms_providers = {"kmip": {"endpoint": "kmip.example.com:5696"}}
master_key = {"keyId": "1"}
```

Everything else - encrypted collections, DEK bootstrap, the encrypting client -
stays identical; only the KMS provider and master key change.

## Phase 4 configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `GATEWAY_ENCRYPTION_ENABLED` | `true` | Client-side Queryable Encryption (on by default, fail-closed) |
| `GATEWAY_ENCRYPTION_KEY` | - | Base64 96-byte local master key (`make gen-key`) |
| `GATEWAY_CRYPT_SHARED_LIB_PATH` | (baked in image) | Path to MongoDB `crypt_shared` for non-Docker runs |
| `GATEWAY_EMBEDDINGS_PROVIDER` | `voyage` | `none` or `voyage` |
| `VOYAGE_API_KEY` | - | Voyage AI key (required for `voyage`) |
| `GATEWAY_EMBEDDING_MODEL` / `_DIMS` | `voyage-code-3` / `1024` | Embedding model and dimensionality |
| `GATEWAY_EMBEDDING_BREAKER_THRESHOLD` / `_COOLDOWN_S` | `5` / `30` | Circuit breaker around the embedder (consecutive failures / cooldown) |
| `GATEWAY_VECTOR_INDEX_NAME` | `intent_vector_index` | Atlas vector index name |
| `GATEWAY_CACHE_ENABLED` / `_DEFAULT_ON` | `false` / `false` | Enable caching / cache without per-request opt-in |
| `GATEWAY_CACHE_TTL_S` / `_LOOKUP_TIMEOUT_S` | `3600` / `0.25` | Entry lifetime / request-path lookup budget |
| `GATEWAY_ADMIN_TOKEN` | - | Required to expose `POST /admin/search` |
| `GATEWAY_METRICS_PROTECTED` | `false` | Require the admin token on `GET /metrics` |

Integration tests for these features (QE round-trip, vector recall, TTL cache)
live in [tests/integration/test_phase4.py](tests/integration/test_phase4.py) and
skip cleanly when their prerequisites (atlas-local / `crypt_shared`) are absent.

