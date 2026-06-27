"""Secure-by-default behaviour: headers, deployment profile, and startup checks."""

from __future__ import annotations

import httpx
import respx

from blackbox_ai.config import DeploymentEnv
from tests.conftest import build_harness, default_settings, load_fixture

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


async def test_security_headers_on_every_response(harness) -> None:  # type: ignore[no-untyped-def]
    response = await harness.client.get("/healthz")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "x-request-id" in response.headers


def test_production_without_tokens_is_fatal() -> None:
    settings = default_settings(deployment_env=DeploymentEnv.PRODUCTION)
    report = settings.runtime_security_report()
    assert not report.ok
    assert any("GATEWAY_TOKENS" in problem for problem in report.fatal)
    # Production always enforces auth, regardless of require_auth.
    assert settings.effective_require_auth is True


def test_production_with_tokens_passes_with_warnings() -> None:
    settings = default_settings(
        deployment_env=DeploymentEnv.PRODUCTION, gateway_tokens_raw="prod-token"
    )
    report = settings.runtime_security_report()
    assert report.ok
    # QE is off in the test profile, so production should warn about plaintext.
    assert any("Queryable Encryption" in w for w in report.warnings)


def test_dev_open_relay_warns() -> None:
    report = default_settings().runtime_security_report()
    assert report.ok
    assert any("Auth is disabled" in w for w in report.warnings)


@respx.mock
async def test_production_profile_enforces_auth_end_to_end() -> None:
    body = load_fixture("openai_completion.json")
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, headers={"content-type": "application/json"}, content=body)
    )
    settings = default_settings(
        deployment_env=DeploymentEnv.PRODUCTION, gateway_tokens_raw="prod-token"
    )

    async with build_harness(settings) as harness:
        payload = {"model": "gpt-4o-mini", "messages": []}
        unauth = await harness.client.post("/openai/v1/chat/completions", json=payload)
        assert unauth.status_code == 401

        authed = await harness.client.post(
            "/openai/v1/chat/completions",
            json=payload,
            headers={"x-gateway-token": "prod-token"},
        )
        assert authed.status_code == 200
