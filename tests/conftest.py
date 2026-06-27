"""Shared test fixtures and helpers.

The harness builds a real FastAPI app wired to an in-memory telemetry sink so we
can assert on captured Intent Documents, while ``respx`` mocks the upstream LLM
providers. Two transports are in play and they do not collide: the test client
talks to the app over an in-process ASGI transport, and the relay's own httpx
client talks "upstream" over the default transport that respx patches.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import httpx
import pytest
import pytest_asyncio

from blackbox_ai.cache.gate import CacheGate, CachePolicy
from blackbox_ai.cache.store import CacheStore
from blackbox_ai.config import Settings
from blackbox_ai.main import create_app
from blackbox_ai.providers.registry import build_registry
from blackbox_ai.proxy.relay import Relay
from blackbox_ai.telemetry.embeddings import Embedder, NullEmbedder
from blackbox_ai.telemetry.models import IntentDocument
from blackbox_ai.telemetry.parsers import build_parser_registry
from blackbox_ai.telemetry.pipeline import TelemetryPipeline

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> bytes:
    """Read a recorded provider response fixture as bytes."""
    return (FIXTURES_DIR / name).read_bytes()


class FakeSink:
    """In-memory sink capturing documents for assertions."""

    def __init__(self) -> None:
        self.documents: list[IntentDocument] = []

    async def write_many(self, documents: list[IntentDocument]) -> int:
        self.documents.extend(documents)
        return len(documents)


class FailingSink:
    """Sink that always fails, simulating an unreachable MongoDB."""

    def __init__(self) -> None:
        self.attempts = 0

    async def write_many(self, documents: list[IntentDocument]) -> int:
        from pymongo.errors import ServerSelectionTimeoutError

        self.attempts += 1
        raise ServerSelectionTimeoutError("mongo is down")


@dataclass(slots=True)
class Harness:
    """Bundle exposing the test client, sink, and pipeline."""

    client: httpx.AsyncClient
    sink: FakeSink | FailingSink
    pipeline: TelemetryPipeline
    relay_client: httpx.AsyncClient
    cache_store: object | None = None


def default_settings(**overrides: object) -> Settings:
    """Build deterministic settings, ignoring any real ``.env`` file."""
    base: dict[str, object] = {
        "openai_api_key": "sk-test-openai",
        "anthropic_api_key": "sk-test-anthropic",
        "gemini_api_key": "test-gemini",
        "ollama_base_url": "http://ollama.test",
        "telemetry_batch_size": 1,
        "telemetry_flush_interval_s": 0.05,
        "telemetry_workers": 1,
        # Keep unit tests on the explicit, deterministic posture: QE off (no
        # crypt_shared in CI) and rate limiting off (a shared in-process limiter
        # would otherwise leak request counts across tests). Dedicated tests opt
        # back in with their own settings.
        "encryption_enabled": False,
        "rate_limit_enabled": False,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


@asynccontextmanager
async def build_harness(
    settings: Settings,
    sink: FakeSink | FailingSink | None = None,
    *,
    cache_store: object | None = None,
    embedder: Embedder | None = None,
    search_service: object | None = None,
) -> AsyncIterator[Harness]:
    """Assemble an app + clients around an injectable telemetry sink."""
    sink = sink or FakeSink()
    pipeline = TelemetryPipeline(
        sink=sink,
        parsers=build_parser_registry(),
        maxsize=1000,
        worker_count=settings.telemetry_workers,
        batch_size=settings.telemetry_batch_size,
        flush_interval_s=settings.telemetry_flush_interval_s,
        embedder=embedder or NullEmbedder(),
        cache_store=cache_store,  # type: ignore[arg-type]
    )
    pipeline.start()

    relay_client = httpx.AsyncClient()
    cache_gate = CacheGate(
        cast("CacheStore | None", cache_store),
        CachePolicy(
            default_on=settings.cache_default_on,
            lookup_timeout_s=settings.cache_lookup_timeout_s,
        ),
    )
    relay = Relay(relay_client, pipeline, settings, cache=cache_gate)
    registry = build_registry(settings)

    # Reuse the real application (routes, middleware, error handler). The ASGI
    # transport does not run the lifespan, so no real MongoDB connection is made;
    # we inject a stand-in AppState exposing only what the routes read.
    app = create_app(settings)
    app.state.gateway = _GatewayStub(
        registry=registry,
        relay=relay,
        pipeline=pipeline,
        settings=settings,
        cache_store=cache_store,
        search_service=search_service,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        try:
            yield Harness(
                client=client,
                sink=sink,
                pipeline=pipeline,
                relay_client=relay_client,
                cache_store=cache_store,
            )
        finally:
            await pipeline.stop()
            await relay_client.aclose()


@dataclass(slots=True)
class _GatewayStub:
    registry: object
    relay: object
    pipeline: object
    # Present so readiness / admin checks have something to read in unit tests.
    mongo_client: object = field(default=None)
    collection: object = field(default=None)
    settings: object = field(default=None)
    cache_store: object = field(default=None)
    search_service: object = field(default=None)
    encryption: object = field(default=None)


async def wait_until(
    predicate: Callable[[], bool], *, timeout_s: float = 2.0, interval: float = 0.02
) -> bool:
    """Poll ``predicate`` until true or timeout; returns the final result."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


@pytest_asyncio.fixture
async def harness() -> AsyncIterator[Harness]:
    """Default harness with sovereign keys configured and a FakeSink."""
    async with build_harness(default_settings()) as h:
        yield h


@pytest.fixture
def settings() -> Settings:
    return default_settings()
