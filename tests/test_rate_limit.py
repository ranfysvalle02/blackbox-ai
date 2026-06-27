"""Per-client rate limiting: the sliding window and its relay integration."""

from __future__ import annotations

import httpx
import respx

from blackbox_ai.security.rate_limit import (
    RateLimitDecision,
    RateLimiter,
    SlidingWindowRateLimiter,
)
from tests.conftest import build_harness, default_settings, load_fixture

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def test_sliding_window_conforms_to_rate_limiter_protocol() -> None:
    # The seam the relay depends on: any RateLimiter is a drop-in replacement.
    limiter = SlidingWindowRateLimiter(1, 1.0)
    assert isinstance(limiter, RateLimiter)

    class _AlwaysDeny:
        def check(self, key: str) -> RateLimitDecision:
            return RateLimitDecision(allowed=False, retry_after_s=1.0)

    assert isinstance(_AlwaysDeny(), RateLimiter)


def test_sliding_window_allows_up_to_limit_then_blocks() -> None:
    clock = {"now": 0.0}
    limiter = SlidingWindowRateLimiter(2, 10.0, clock=lambda: clock["now"])

    assert limiter.check("client").allowed
    assert limiter.check("client").allowed
    blocked = limiter.check("client")
    assert not blocked.allowed
    assert blocked.retry_after_s == 10.0

    # A different key has its own budget.
    assert limiter.check("other").allowed

    # Once the window rolls past the oldest hit, capacity frees up.
    clock["now"] = 10.001
    assert limiter.check("client").allowed


@respx.mock
async def test_relay_returns_429_when_rate_limited() -> None:
    body = load_fixture("openai_completion.json")
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, headers={"content-type": "application/json"}, content=body)
    )
    settings = default_settings(
        rate_limit_enabled=True, rate_limit_requests=2, rate_limit_window_s=60.0
    )

    async with build_harness(settings) as harness:
        payload = {"model": "gpt-4o-mini", "messages": []}
        first = await harness.client.post("/openai/v1/chat/completions", json=payload)
        second = await harness.client.post("/openai/v1/chat/completions", json=payload)
        third = await harness.client.post("/openai/v1/chat/completions", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["error"]["type"] == "rate_limit_exceeded"
    assert int(third.headers["retry-after"]) >= 1
