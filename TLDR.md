# TL;DR: Ghosts in the Code (Blackbox AI Gateway)

## 🚨 The Problem (Why)
When an AI agent writes 15,000 lines of code overnight, it leaves no explanation. If a bizarre edge-case bug occurs 3 weeks later, your engineers are flying blind because the AI's reasoning evaporated the moment the API stream closed. 

Furthermore, if that historical context lives exclusively inside OpenAI or Anthropic's walled gardens, you are locked in. You need an **AI Flight Recorder** to capture the *intent* and *chain-of-thought* behind the code, ensuring digital sovereignty.

## 💡 The Solution (What)
A **native pass-through LLM gateway** built on FastAPI and MongoDB. It sits between your developers/agents and the frontier models (OpenAI, Anthropic, Gemini, Ollama, Azure). 

It streams responses back to the client with **zero added latency** while capturing a rich, queryable "Intent Document" for every interaction out-of-band.

## 🏗️ The Architecture (How)
To ensure the gateway never crashes your application, it is deliberately split into two isolated planes:

1. **Data Plane (Fail-Open):** A dumb, fast proxy. It forwards the request, streams the response back to the client, and tees a copy of the bytes. If the database crashes or an API key expires, the user's request *still succeeds*.
2. **Telemetry Plane (Best-Effort):** Async background workers drain the copied bytes, parse the provider-specific formats, compute embeddings, and save the data to MongoDB.

### The 2-Line Drop-In
You don't need to rewrite your apps or learn a new SDK. Just point your existing SDK at the gateway:

```python
from openai import OpenAI

# Point base_url to the gateway, keep using the exact same SDK
client = OpenAI(base_url="http://localhost:8000/openai/v1", api_key="gateway-token")

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Refactor the billing module."}],
    stream=True
)
```

## 🍃 Why MongoDB? (The Magic)

Building this with a traditional stack requires 5 systems (Relational DB, Vector DB, Redis Cache, Sync pipelines, Encryption proxy). MongoDB collapses this into **one data layer**:

### 1. Polymorphic Documents (No Schema Migrations)
Every AI provider speaks a different dialect and changes their API constantly. MongoDB's document model allows us to store an OpenAI response and an Anthropic response in the same collection without rigid schemas, migrations, or empty columns.

### 2. Time-Travel Debugging (Vector + Hybrid Search)
You can't `grep` for a "vibe". By embedding the AI's reasoning using Voyage AI, you can search your history semantically using `$vectorSearch` and `$rankFusion`. 
*Example Query:* "Find me when the agent hesitated about connection pooling security."

```javascript
// A single query combining semantic meaning and exact keyword matches
{
  "$rankFusion": {
    "input": {
      "pipelines": {
        "vector": [ { "$vectorSearch": { /* semantic similarity */ } } ],
        "text": [ { "$search": { /* keyword precision */ } } ]
      }
    }
  }
}
```

### 3. Secure by Default (Queryable Encryption)
Prompts contain proprietary code and secrets. The gateway uses **Queryable Encryption** to encrypt the crown jewels (prompts, chain-of-thought) *client-side* before they ever reach the database. 

The database stores ciphertext and literally cannot read it. However, because the embedding vector is computed pre-encryption and stored in plaintext, **you can still perform semantic searches over fully encrypted data.**

### 4. Self-Cleaning Cache (TTL Indexes)
Caching identical LLM calls saves money and latency. Instead of standing up a separate Redis server, we use MongoDB TTL (Time-To-Live) indexes to automatically delete stale cache entries after a set time.

```python
# The database cleans up after itself
await collection.create_index("created_at", expireAfterSeconds=3600)
```