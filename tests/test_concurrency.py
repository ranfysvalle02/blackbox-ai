"""Data-plane backpressure: the in-flight concurrency cap rejects with 503."""

from __future__ import annotations

import asyncio

import httpx
import respx

from tests.conftest import build_harness, default_settings

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


@respx.mock
async def test_inflight_cap_rejects_then_recovers() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_upstream(request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=b'{"ok": true}'
        )

    respx.post(OPENAI_URL).mock(side_effect=slow_upstream)
    settings = default_settings(max_concurrent_requests=1)

    async with build_harness(settings) as harness:
        payload = {"model": "gpt-4o-mini", "messages": []}

        # First request takes the only permit and blocks inside the upstream.
        first = asyncio.create_task(
            harness.client.post("/openai/v1/chat/completions", json=payload)
        )
        await asyncio.wait_for(started.wait(), timeout=2.0)

        # Second request is rejected fast - the cap is full.
        second = await harness.client.post("/openai/v1/chat/completions", json=payload)
        assert second.status_code == 503
        assert second.json()["error"]["type"] == "service_overloaded"
        assert second.headers.get("retry-after") == "1"

        # Release the first; the permit returns.
        release.set()
        first_response = await asyncio.wait_for(first, timeout=2.0)
        assert first_response.status_code == 200

        # A subsequent request now succeeds (permit was released exactly once).
        third = await harness.client.post("/openai/v1/chat/completions", json=payload)
        assert third.status_code == 200
