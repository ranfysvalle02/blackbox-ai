"""Tests for the generic relay: streaming fidelity, capture, and fail-open."""

from __future__ import annotations

import httpx
import respx

from tests.conftest import (
    FailingSink,
    Harness,
    build_harness,
    default_settings,
    load_fixture,
    wait_until,
)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


async def test_healthz(harness: Harness) -> None:
    response = await harness.client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@respx.mock
async def test_relay_streams_bytes_unchanged_and_captures(harness: Harness) -> None:
    body = load_fixture("openai_stream.sse")
    route = respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )
    )

    response = await harness.client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
        headers={
            "authorization": "Bearer client-token",
            "x-project-id": "auth-service-migration",
            "x-agent-session": "agent_run_99482f",
            "x-developer-id": "dev_clara",
        },
    )

    # The client sees the upstream bytes verbatim.
    assert response.status_code == 200
    assert response.content == body
    assert "x-request-id" in response.headers

    # The sovereign key was injected; the client's token never reached upstream.
    assert route.called
    upstream_request = route.calls.last.request
    assert upstream_request.headers["authorization"] == "Bearer sk-test-openai"

    # Telemetry was captured out-of-band.
    assert await wait_until(lambda: len(harness.sink.documents) == 1)
    doc = harness.sink.documents[0]
    assert doc.provider == "openai"
    assert doc.model_requested == "gpt-4o-mini"
    assert doc.model_responded == "gpt-4o-mini"
    assert doc.streamed is True
    assert doc.intent_telemetry.content == "Hello world"
    assert doc.intent_telemetry.finish_reason == "stop"
    assert doc.performance.output_tokens == 2
    assert doc.project_id == "auth-service-migration"
    assert doc.session_id == "agent_run_99482f"
    assert doc.developer_id == "dev_clara"
    assert doc.raw_payload is not None
    assert doc.raw_payload["model"] == "gpt-4o-mini"


@respx.mock
async def test_anthropic_native_passthrough(harness: Harness) -> None:
    body = load_fixture("anthropic_stream.sse")
    route = respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )
    )

    response = await harness.client.post(
        "/anthropic/v1/messages",
        json={"model": "claude-3-5-sonnet-20241022", "messages": [], "max_tokens": 100},
        headers={"x-api-key": "client-key", "anthropic-version": "2023-06-01"},
    )

    assert response.status_code == 200
    upstream_request = route.calls.last.request
    assert upstream_request.headers["x-api-key"] == "sk-test-anthropic"
    # Provider-specific client headers are preserved through the relay.
    assert upstream_request.headers["anthropic-version"] == "2023-06-01"

    assert await wait_until(lambda: len(harness.sink.documents) == 1)
    assert harness.sink.documents[0].provider == "anthropic"
    assert harness.sink.documents[0].intent_telemetry.content == "Hi there"


@respx.mock
async def test_gemini_model_extracted_from_path(harness: Harness) -> None:
    body = load_fixture("gemini_stream.sse")
    route = respx.route(
        method="POST",
        url__regex=r".*/v1beta/models/gemini-2\.0-flash:streamGenerateContent.*",
    ).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )
    )

    response = await harness.client.post(
        "/gemini/v1beta/models/gemini-2.0-flash:streamGenerateContent?alt=sse",
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        headers={"x-goog-api-key": "client-key"},
    )

    assert response.status_code == 200
    assert route.calls.last.request.headers["x-goog-api-key"] == "test-gemini"
    assert await wait_until(lambda: len(harness.sink.documents) == 1)
    doc = harness.sink.documents[0]
    assert doc.provider == "gemini"
    assert doc.model_requested == "gemini-2.0-flash"
    assert doc.intent_telemetry.content == "Hello"


async def test_unknown_provider_returns_404(harness: Harness) -> None:
    response = await harness.client.get("/bogus/v1/models")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["type"] == "unknown_provider"
    assert "openai" in payload["error"]["details"]["available"]


@respx.mock
async def test_relay_is_fail_open_when_sink_unavailable() -> None:
    body = load_fixture("openai_stream.sse")
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )
    )

    async with build_harness(default_settings(), sink=FailingSink()) as harness:
        response = await harness.client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": [], "stream": True},
            headers={"authorization": "Bearer client-token"},
        )
        # The database being down must not affect the client at all.
        assert response.status_code == 200
        assert response.content == body
        # The worker tried to persist and failed, isolated from the request path.
        assert await wait_until(lambda: harness.pipeline.metrics.failed >= 1)


@respx.mock
async def test_upstream_connection_error_maps_to_502(harness: Harness) -> None:
    respx.post(OPENAI_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    response = await harness.client.post(
        "/openai/v1/chat/completions",
        json={"model": "gpt-4o-mini", "messages": []},
        headers={"authorization": "Bearer client-token"},
    )
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "upstream_connection_error"


@respx.mock
async def test_auth_enforced_when_enabled() -> None:
    body = load_fixture("openai_completion.json")
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, headers={"content-type": "application/json"}, content=body)
    )
    settings = default_settings(require_auth=True, gateway_tokens_raw="secret-token")

    async with build_harness(settings) as harness:
        rejected = await harness.client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": []},
            headers={"authorization": "Bearer wrong"},
        )
        assert rejected.status_code == 401
        assert rejected.json()["error"]["type"] == "authentication_error"

        accepted = await harness.client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": []},
            headers={"authorization": "Bearer secret-token"},
        )
        assert accepted.status_code == 200
