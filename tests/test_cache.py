"""Tests for the opt-in exact-match cache: keys, HIT/MISS, replay, fail-open."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import respx
from pymongo.errors import ServerSelectionTimeoutError

from blackbox_ai.cache.keys import CacheIdentity, canonical_request_key
from blackbox_ai.cache.store import CacheEntry
from tests.conftest import build_harness, default_settings, load_fixture, wait_until

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


class FakeCacheStore:
    """In-memory CacheStore stand-in (duck-typed) for relay/pipeline tests."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, bool], CacheEntry] = {}
        self.puts: list[dict[str, object]] = []
        self.lookup_error: Exception | None = None
        self.lookup_delay_s: float = 0.0

    async def ensure_indexes(self) -> None:
        return None

    async def lookup(self, identity: CacheIdentity) -> CacheEntry | None:
        if self.lookup_delay_s:
            await asyncio.sleep(self.lookup_delay_s)
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.entries.get((identity.key, identity.streamed))

    async def put(
        self,
        identity: CacheIdentity,
        *,
        provider: str,
        endpoint: str,
        status_code: int,
        content_type: str | None,
        response_body: bytes,
        request_payload: dict[str, object] | None,
    ) -> None:
        self.puts.append(
            {
                "cache_key": identity.key,
                "streamed": identity.streamed,
                "provider": provider,
                "endpoint": endpoint,
                "status_code": status_code,
                "content_type": content_type,
                "response_body": response_body,
                "request_payload": request_payload,
            }
        )
        now = datetime.now(UTC)
        self.entries[(identity.key, identity.streamed)] = CacheEntry(
            cache_key=identity.key,
            streamed=identity.streamed,
            provider=provider,
            endpoint=endpoint,
            status_code=status_code,
            content_type=content_type,
            response_body=response_body,
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )


# --- Pure key tests --------------------------------------------------------


def test_cache_key_is_stream_agnostic_and_order_insensitive() -> None:
    msgs = [{"role": "user", "content": "hi"}]
    a = json.dumps({"model": "gpt-4o", "messages": msgs, "stream": True})
    b = json.dumps({"messages": msgs, "model": "gpt-4o"})
    assert canonical_request_key("openai", "POST", "/v1/chat/completions", a.encode()) == (
        canonical_request_key("openai", "POST", "v1/chat/completions", b.encode())
    )


def test_cache_key_distinguishes_semantic_changes() -> None:
    base = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    other = {"model": "gpt-4o", "messages": [{"role": "user", "content": "bye"}]}
    k1 = canonical_request_key("openai", "POST", "x", json.dumps(base).encode())
    k2 = canonical_request_key("openai", "POST", "x", json.dumps(other).encode())
    assert k1 != k2


def test_cache_key_none_for_non_json() -> None:
    assert canonical_request_key("openai", "POST", "x", b"") is None
    assert canonical_request_key("openai", "POST", "x", b"not json") is None
    assert canonical_request_key("openai", "POST", "x", b"[1,2,3]") is None


# --- Relay integration -----------------------------------------------------


def _cache_settings(**overrides: object) -> object:
    return default_settings(cache_enabled=True, **overrides)


@respx.mock
async def test_cache_miss_forwards_and_writes_entry() -> None:
    body = load_fixture("openai_completion.json")
    route = respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, headers={"content-type": "application/json"}, content=body)
    )
    store = FakeCacheStore()
    payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}

    async with build_harness(_cache_settings(), cache_store=store) as h:
        response = await h.client.post(
            "/openai/v1/chat/completions",
            json=payload,
            headers={"authorization": "Bearer t", "x-intent-cache": "on"},
        )
        assert response.status_code == 200
        assert response.content == body
        assert response.headers["x-intent-cache"] == "MISS"
        assert route.called

        # The worker persists a cache entry out-of-band and threads the key.
        assert await wait_until(lambda: len(store.puts) == 1)
        assert await wait_until(lambda: len(h.sink.documents) == 1)
        doc = h.sink.documents[0]
        assert doc.cache_key is not None
        assert doc.served_from_cache is False
        assert store.puts[0]["cache_key"] == doc.cache_key


@respx.mock
async def test_cache_hit_replays_without_upstream() -> None:
    cached_bytes = b'{"id":"cached","choices":[{"message":{"content":"from cache"}}]}'
    payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}
    body_bytes = json.dumps(payload).encode()
    key = canonical_request_key("openai", "POST", "v1/chat/completions", body_bytes)
    assert key is not None

    store = FakeCacheStore()
    now = datetime.now(UTC)
    store.entries[(key, False)] = CacheEntry(
        cache_key=key,
        streamed=False,
        provider="openai",
        endpoint="v1/chat/completions",
        status_code=200,
        content_type="application/json",
        response_body=cached_bytes,
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    route = respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, content=b"SHOULD NOT BE CALLED")
    )

    async with build_harness(_cache_settings(), cache_store=store) as h:
        response = await h.client.post(
            "/openai/v1/chat/completions",
            json=payload,
            headers={"authorization": "Bearer t", "x-intent-cache": "on"},
        )
        assert response.status_code == 200
        assert response.content == cached_bytes
        assert response.headers["x-intent-cache"] == "HIT"
        # Upstream was never contacted.
        assert not route.called
        # The hit is still recorded as telemetry, flagged as served-from-cache.
        assert await wait_until(lambda: len(h.sink.documents) == 1)
        doc = h.sink.documents[0]
        assert doc.served_from_cache is True
        assert doc.cache_key == key
        # A replayed hit must not be re-written to the cache.
        assert store.puts == []


@respx.mock
async def test_cache_not_used_without_opt_in() -> None:
    body = load_fixture("openai_completion.json")
    route = respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, headers={"content-type": "application/json"}, content=body)
    )
    store = FakeCacheStore()
    async with build_harness(_cache_settings(), cache_store=store) as h:
        response = await h.client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": []},
            headers={"authorization": "Bearer t"},
        )
        assert response.status_code == 200
        assert "x-intent-cache" not in response.headers
        assert route.called
        # No opt-in -> no cache write.
        assert await wait_until(lambda: len(h.sink.documents) == 1)
        assert store.puts == []


@respx.mock
async def test_cache_lookup_failure_is_fail_open() -> None:
    body = load_fixture("openai_completion.json")
    route = respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, headers={"content-type": "application/json"}, content=body)
    )
    store = FakeCacheStore()
    store.lookup_error = ServerSelectionTimeoutError("mongo down")

    async with build_harness(_cache_settings(), cache_store=store) as h:
        response = await h.client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": []},
            headers={"authorization": "Bearer t", "x-intent-cache": "on"},
        )
        # Lookup blew up, but the request still succeeds via upstream.
        assert response.status_code == 200
        assert response.content == body
        assert response.headers["x-intent-cache"] == "MISS"
        assert route.called


@respx.mock
async def test_cache_lookup_timeout_is_fail_open() -> None:
    body = load_fixture("openai_completion.json")
    route = respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, headers={"content-type": "application/json"}, content=body)
    )
    store = FakeCacheStore()
    store.lookup_delay_s = 0.5  # exceeds the lookup timeout below

    async with build_harness(_cache_settings(cache_lookup_timeout_s=0.05), cache_store=store) as h:
        response = await h.client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": []},
            headers={"authorization": "Bearer t", "x-intent-cache": "on"},
        )
        assert response.status_code == 200
        assert response.content == body
        assert route.called
