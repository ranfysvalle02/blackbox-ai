"""Unit tests for the CacheGate: opt-in, identity derivation, fail-open lookup."""

from __future__ import annotations

import json

from pymongo.errors import ServerSelectionTimeoutError
from starlette.requests import Request

from blackbox_ai.cache.gate import CACHE_HEADER, CacheGate, CachePolicy
from blackbox_ai.cache.keys import CacheIdentity, canonical_request_key
from blackbox_ai.providers.base import AuthScheme, ProviderConfig
from tests.test_cache import FakeCacheStore

PROVIDER = ProviderConfig(
    name="openai",
    upstream_base_url="https://api.openai.com",
    auth_scheme=AuthScheme.BEARER,
    parser_name="openai",
)
PATH = "v1/chat/completions"
BODY = json.dumps({"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}).encode()


def _request(method: str = "POST", headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": method, "headers": raw, "query_string": b""})


def _gate(
    store: FakeCacheStore | None = None, *, default_on: bool = False, timeout_s: float = 0.25
) -> CacheGate:
    return CacheGate(store, CachePolicy(default_on=default_on, lookup_timeout_s=timeout_s))  # type: ignore[arg-type]


def test_disabled_gate_is_inert() -> None:
    gate = _gate(None)
    assert gate.enabled is False
    req = _request(headers={CACHE_HEADER: "on"})
    assert gate.identity_for(req, PROVIDER, PATH, BODY) is None


def test_non_post_is_not_cacheable() -> None:
    gate = _gate(FakeCacheStore())
    req = _request(method="GET", headers={CACHE_HEADER: "on"})
    assert gate.identity_for(req, PROVIDER, PATH, BODY) is None


def test_non_json_body_is_not_cacheable() -> None:
    gate = _gate(FakeCacheStore())
    req = _request(headers={CACHE_HEADER: "on"})
    assert gate.identity_for(req, PROVIDER, PATH, b"not json") is None


def test_opt_in_required_by_default() -> None:
    gate = _gate(FakeCacheStore(), default_on=False)
    assert gate.identity_for(_request(), PROVIDER, PATH, BODY) is None


def test_opt_in_header_on_yields_canonical_identity() -> None:
    gate = _gate(FakeCacheStore())
    identity = gate.identity_for(_request(headers={CACHE_HEADER: "on"}), PROVIDER, PATH, BODY)
    assert identity is not None
    assert identity.key == canonical_request_key("openai", "POST", PATH, BODY)
    assert identity.streamed is False


def test_header_off_overrides_default_on() -> None:
    gate = _gate(FakeCacheStore(), default_on=True)
    assert gate.identity_for(_request(headers={CACHE_HEADER: "off"}), PROVIDER, PATH, BODY) is None


def test_default_on_caches_without_header() -> None:
    gate = _gate(FakeCacheStore(), default_on=True)
    assert gate.identity_for(_request(), PROVIDER, PATH, BODY) is not None


def test_identity_streamed_inferred_from_body() -> None:
    gate = _gate(FakeCacheStore())
    body = json.dumps({"model": "gpt-4o", "messages": [], "stream": True}).encode()
    identity = gate.identity_for(_request(headers={CACHE_HEADER: "on"}), PROVIDER, PATH, body)
    assert identity is not None
    assert identity.streamed is True


def test_identity_streamed_inferred_from_gemini_path() -> None:
    gate = _gate(FakeCacheStore())
    path = "v1beta/models/gemini-2.0-flash:streamGenerateContent"
    identity = gate.identity_for(_request(headers={CACHE_HEADER: "on"}), PROVIDER, path, BODY)
    assert identity is not None
    assert identity.streamed is True


async def test_lookup_returns_hit() -> None:
    store = FakeCacheStore()
    identity = CacheIdentity(key="k", streamed=False)
    await store.put(
        identity,
        provider="openai",
        endpoint=PATH,
        status_code=200,
        content_type="application/json",
        response_body=b"cached",
        request_payload=None,
    )
    hit = await _gate(store).lookup(identity)
    assert hit is not None
    assert hit.response_body == b"cached"


async def test_lookup_fail_open_on_error() -> None:
    store = FakeCacheStore()
    store.lookup_error = ServerSelectionTimeoutError("mongo down")
    assert await _gate(store).lookup(CacheIdentity(key="k", streamed=False)) is None


async def test_lookup_fail_open_on_timeout() -> None:
    store = FakeCacheStore()
    store.lookup_delay_s = 0.5
    gate = _gate(store, timeout_s=0.05)
    assert await gate.lookup(CacheIdentity(key="k", streamed=False)) is None
