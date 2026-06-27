"""Auth hardening: timing-safe gateway tokens and no token leakage upstream."""

from __future__ import annotations

import httpx
import respx

from tests.conftest import build_harness, default_settings, load_fixture

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OLLAMA_URL = "http://ollama.test/api/chat"


@respx.mock
async def test_invalid_token_rejected_valid_accepted() -> None:
    body = load_fixture("openai_completion.json")
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, headers={"content-type": "application/json"}, content=body)
    )
    settings = default_settings(require_auth=True, gateway_tokens_raw="a-token,b-token")

    async with build_harness(settings) as harness:
        bad = await harness.client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": []},
            headers={"x-gateway-token": "not-a-token"},
        )
        assert bad.status_code == 401

        good = await harness.client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": []},
            headers={"x-gateway-token": "b-token"},
        )
        assert good.status_code == 200


@respx.mock
async def test_passthrough_requires_dedicated_header_and_never_leaks_token() -> None:
    route = respx.post(OLLAMA_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "application/json"}, content=b'{"ok": true}'
        )
    )
    # Ollama is a keyless passthrough provider (no sovereign key), so the
    # client's Authorization is their own upstream credential.
    settings = default_settings(require_auth=True, gateway_tokens_raw="gw-secret")

    async with build_harness(settings) as harness:
        # Authorization alone must NOT authenticate the gateway in passthrough.
        rejected = await harness.client.post(
            "/ollama/api/chat",
            json={"model": "llama3", "messages": []},
            headers={"authorization": "Bearer gw-secret"},
        )
        assert rejected.status_code == 401
        assert rejected.json()["error"]["type"] == "authentication_error"

        accepted = await harness.client.post(
            "/ollama/api/chat",
            json={"model": "llama3", "messages": []},
            headers={
                "x-gateway-token": "gw-secret",
                "authorization": "Bearer client-own-key",
            },
        )
        assert accepted.status_code == 200

    # The rejected request never reached upstream; the accepted one did.
    assert route.call_count == 1
    upstream = route.calls.last.request
    assert "x-gateway-token" not in upstream.headers
    # The client's own credential is forwarded untouched; the gateway secret
    # never appears upstream.
    assert upstream.headers.get("authorization") == "Bearer client-own-key"
