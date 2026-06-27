# Running the Ollama Demo

This guide walks through running an end-to-end demo of the AI Gateway using a local Ollama instance. It demonstrates how the gateway proxies the request, captures the intent out-of-band, and encrypts the sensitive data client-side before storing it in MongoDB.

## 1. Prerequisites

You need to have Ollama running locally with a model pulled. For this demo, we'll use `qwen3:14b`, but any model will work.

```bash
# Pull the model in your local Ollama instance
ollama pull qwen3:14b
```

## 2. Start the Gateway and MongoDB

First, generate the local encryption key. Queryable Encryption is enabled by default, so the gateway needs this key to start.

```bash
# Generate a 96-byte master key and append it to .env
make gen-key
```

Next, start the gateway and the MongoDB Atlas Local container in the background:

```bash
make up
```

Finally, bootstrap the encrypted collections, TTL indexes, and vector search index:

```bash
make init
```

## 3. Run the Demo Script

The `examples/demo.py` script uses the native Ollama Python SDK, but points it at the gateway instead of directly at Ollama. It also injects sovereign metadata headers (`x-project-id`, `x-agent-session`, `x-developer-id`).

Since Ollama is running on your host machine and the gateway is running in Docker, we need to tell the gateway how to reach Ollama by setting `OLLAMA_BASE_URL` in the `.env` file or inline.

```bash
OLLAMA_BASE_URL=http://host.docker.internal:11434 OLLAMA_MODEL="qwen3:14b" make demo
```

### Output

The gateway streams the response from your local Qwen model back to the terminal in real-time:

```text
Gateway: http://localhost:8000

-- Skipping OpenAI (OPENAI_API_KEY not set) --
-- Skipping Anthropic (ANTHROPIC_API_KEY not set) --
-- Skipping Google Gemini (GEMINI_API_KEY not set) --
-- Skipping Azure OpenAI (AZURE_OPENAI_DEPLOYMENT not set) --

======================================================================
  Ollama
======================================================================
Owning your own AI 'intent' data ensures control over how user goals and behaviors are interpreted, enabling ethical customization, privacy protection, and competitive advantage in AI-driven interactions.
```

## 4. Investigating the Captured Intent

While the response was streaming to your terminal, the gateway's **Telemetry Plane** captured the NDJSON stream, parsed it, and saved an Intent Document to MongoDB. 

Crucially, because Queryable Encryption is enabled, the sensitive fields were encrypted *client-side* before being sent to the database.

### The Database View (Encrypted)

If we look directly at the database using `mongosh` (which does not have the encryption keys), we can see that the routing metadata is in plaintext, but the crown jewels are encrypted as `BinData` (Subtype 6):

```bash
docker compose exec mongodb mongosh blackbox_ai --eval 'db.intents.find().sort({_id: -1}).limit(1).toArray()'
```

**Output:**

```javascript
[
  {
    _id: ObjectId('6a3f2405ec46053a1eac2573'),
    schema_version: 1,
    request_id: 'f9834f51-7b58-4ba5-ab8d-ba5e5855b5e3',
    session_id: 'demo-session-001',
    project_id: 'blackbox-ai-demo',
    developer_id: 'demo-engineer',
    timestamp: ISODate('2026-06-27T01:14:32.683Z'),
    provider: 'ollama',
    model_requested: 'qwen3:14b',
    model_responded: 'qwen3:14b',
    streamed: true,
    status: 'ok',
    http_status: 200,
    performance: {
      latency_ms: 11828.283088980243,
      ttft_ms: 1.9645830616354942,
      input_tokens: 26,
      output_tokens: 405,
      total_tokens: 431
    },
    // SENSITIVE DATA IS ENCRYPTED
    raw_payload: Binary.createFromBase64('EK1WdC3X1Ut9iAwO3vU5Kb8D3gpdNZVICrykRuSs5ko8k2e+cXGRuQj3hzfD+n1O6A9pKrjOP1U1uTDsR9oZi3H0WohFTmEQZiEDG73DrNG9RjY+oRKavyLTeljUBW7gwOjcEqRvQrstGFGW4TgXuwfj24Teqj7kJB8OyhhF8eJhA26ljqkOY2V/091OzjW0nXlFJ5MgGxDM2JobIFIB5+tPLRLdb2r/aw81vxg05DT/wG67c4JOUklHYJC443BmKzw+YcfbfWbjIs6c9TtoIjlQzP8Gg36DvU3GuzkbGKUSSWNyJMsLt+dAom/FMe5Zxto=', 6),
    intent_telemetry: {
      content: Binary.createFromBase64('EPtx8WJ4Gk6SmreGThGK0gYCbpME8dsttgZom8K5N2o+48kjX+pw3qHnLyhbfFNxt3LnqTUuTRMJSeZrwq8Oqbrbks7VFw+DMDhAfDnwcfPwD1K2MGyipdfWTmd/SVkTr9O0sWSWiFERrJ3gUGEjol9x9NZHf5tJ6VCTUYsjxvCl2kY6wm9dKAFkNFI+sX9DFZeON/kg9sc7lkfvxBEwTQpyJegSFlld9U/sUtT7WbU4TTPa6sItbR1Pw0KHeIwqfbJ26W7GoV9FLTuVShLd8NOmX7WK8dxagwSC2ve1a3hRMrKnCjtXlnB8j/ZI3m60WF3hDlEcQino+eJ/KLZj69iTJInCoIOC44XV1kvPJ9USkrEXlZaBrxVlaOK5aMBEO8g=', 6),
      tools_called: [],
      finish_reason: 'stop',
      parse_status: 'ok',
      parse_error: null
    }
  }
]
```

### The Gateway View (Decrypted)

When the gateway fetches the document, the MongoDB driver uses the master key to transparently decrypt the fields on the fly. 

If we run a script inside the gateway container to fetch the exact same document:

```text
--- DECRYPTED DOCUMENT ---
Provider: ollama
Model: qwen3:14b
Project ID: blackbox-ai-demo
Latency: 11828.28 ms

[Decrypted] Raw Payload:
{'model': 'qwen3:14b', 'stream': True, 'messages': [{'role': 'user', 'content': "In one sentence, why does owning your own AI 'intent' data matter?"}], 'tools': []}

[Decrypted] Intent Content:
Owning your own AI 'intent' data ensures control over how user goals and behaviors are interpreted, enabling ethical customization, privacy protection, and competitive advantage in AI-driven interactions.
```

The gateway successfully captured the full context of the interaction without ever exposing the sensitive prompt or response to the database in plaintext.