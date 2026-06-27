"""Drive every supported provider through the gateway using its native SDK.

The whole point of the gateway is that adoption costs two lines: point the SDK's
``base_url`` at the gateway and use a gateway token. This script proves it - the
official OpenAI, Anthropic, Google GenAI, and Ollama SDKs are used unmodified.

Run the gateway first (``make up`` or ``make dev``), export whatever provider
keys you have, then::

    uv run --extra examples python examples/demo.py

Only providers whose credentials are present are exercised; the rest are
skipped. Each call sends the sovereign metadata headers so the captured Intent
Documents are grouped by project / session / developer.
"""

from __future__ import annotations

import os
from collections.abc import Callable

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000").rstrip("/")
GATEWAY_TOKEN = os.environ.get("GATEWAY_TOKEN", "demo-gateway-token")
PROMPT = "In one sentence, why does owning your own AI 'intent' data matter?"

METADATA_HEADERS = {
    "x-project-id": "blackbox-ai-demo",
    "x-agent-session": "demo-session-001",
    "x-developer-id": "demo-engineer",
}


def _banner(provider: str) -> None:
    print(f"\n{'=' * 70}\n  {provider}\n{'=' * 70}")


def demo_openai() -> None:
    from openai import OpenAI

    client = OpenAI(
        base_url=f"{GATEWAY_URL}/openai/v1",
        api_key=GATEWAY_TOKEN,
        default_headers=METADATA_HEADERS,
    )
    stream = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": PROMPT}],
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            print(delta, end="", flush=True)
    print()


def demo_anthropic() -> None:
    from anthropic import Anthropic

    client = Anthropic(
        base_url=f"{GATEWAY_URL}/anthropic",
        api_key=GATEWAY_TOKEN,
        default_headers=METADATA_HEADERS,
    )
    with client.messages.stream(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
        max_tokens=256,
        messages=[{"role": "user", "content": PROMPT}],
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
    print()


def demo_gemini() -> None:
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=GATEWAY_TOKEN,
        http_options=types.HttpOptions(
            base_url=f"{GATEWAY_URL}/gemini",
            headers=METADATA_HEADERS,
        ),
    )
    for chunk in client.models.generate_content_stream(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
        contents=PROMPT,
    ):
        if chunk.text:
            print(chunk.text, end="", flush=True)
    print()


def demo_azure() -> None:
    from openai import AzureOpenAI

    client = AzureOpenAI(
        azure_endpoint=f"{GATEWAY_URL}/azure",
        api_key=GATEWAY_TOKEN,
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        default_headers=METADATA_HEADERS,
    )
    stream = client.chat.completions.create(
        model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        messages=[{"role": "user", "content": PROMPT}],
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            print(delta, end="", flush=True)
    print()


def demo_ollama() -> None:
    import ollama

    client = ollama.Client(host=f"{GATEWAY_URL}/ollama", headers=METADATA_HEADERS)
    for chunk in client.chat(
        model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
        messages=[{"role": "user", "content": PROMPT}],
        stream=True,
    ):
        print(chunk["message"]["content"], end="", flush=True)
    print()


# (label, function, env var that must be present to attempt it)
_DEMOS: list[tuple[str, Callable[[], None], str | None]] = [
    ("OpenAI", demo_openai, "OPENAI_API_KEY"),
    ("Anthropic", demo_anthropic, "ANTHROPIC_API_KEY"),
    ("Google Gemini", demo_gemini, "GEMINI_API_KEY"),
    ("Azure OpenAI", demo_azure, "AZURE_OPENAI_DEPLOYMENT"),
    ("Ollama", demo_ollama, None),  # keyless; assumes a local Ollama is running
]


def main() -> None:
    print(f"Gateway: {GATEWAY_URL}")
    for label, func, required_env in _DEMOS:
        if required_env and not os.environ.get(required_env):
            print(f"\n-- Skipping {label} ({required_env} not set) --")
            continue
        _banner(label)
        try:
            func()
        except ImportError as exc:
            print(f"[skip] SDK not installed for {label}: {exc}")
        except Exception as exc:  # noqa: BLE001 - demo: report and continue to next provider
            print(f"[error] {label} failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
