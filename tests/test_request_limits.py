"""The data plane rejects oversized request bodies with 413 before buffering."""

from __future__ import annotations

import httpx
import respx

from tests.conftest import build_harness, default_settings

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


@respx.mock
async def test_oversized_body_rejected_with_413() -> None:
    route = respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, content=b"{}"))
    big = {"model": "gpt-4o", "messages": [{"role": "user", "content": "x" * 500}]}

    async with build_harness(default_settings(max_request_bytes=64)) as h:
        response = await h.client.post(
            "/openai/v1/chat/completions", json=big, headers={"authorization": "Bearer t"}
        )
        assert response.status_code == 413
        assert response.json()["error"]["type"] == "request_too_large"
        # Rejected before any upstream contact.
        assert not route.called


@respx.mock
async def test_within_limit_passes_through() -> None:
    body = b'{"ok":true}'
    route = respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, headers={"content-type": "application/json"}, content=body)
    )

    async with build_harness(default_settings(max_request_bytes=10 * 1024 * 1024)) as h:
        response = await h.client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o", "messages": []},
            headers={"authorization": "Bearer t"},
        )
        assert response.status_code == 200
        assert response.content == body
        assert route.called
