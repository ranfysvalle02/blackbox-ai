"""Tests for the declarative provider catalog."""

from __future__ import annotations

from blackbox_ai.providers.base import AuthScheme
from blackbox_ai.providers.catalog import build_providers
from tests.conftest import default_settings


def test_core_providers_always_present() -> None:
    providers = {p.name: p for p in build_providers(default_settings())}
    assert {"openai", "anthropic", "gemini", "ollama"} <= set(providers)
    # Azure has no universal host: absent unless an endpoint is configured.
    assert "azure" not in providers


def test_provider_fields_resolved_from_settings() -> None:
    providers = {p.name: p for p in build_providers(default_settings())}

    assert providers["openai"].auth_scheme is AuthScheme.BEARER
    assert providers["openai"].api_key == "sk-test-openai"

    assert providers["anthropic"].auth_scheme is AuthScheme.HEADER
    assert providers["anthropic"].auth_param == "x-api-key"
    assert providers["anthropic"].api_key == "sk-test-anthropic"

    assert providers["gemini"].auth_param == "x-goog-api-key"

    assert providers["ollama"].auth_scheme is AuthScheme.NONE
    assert providers["ollama"].api_key is None


def test_azure_enabled_only_with_endpoint() -> None:
    settings = default_settings(
        azure_openai_endpoint="https://x.openai.azure.com",
        azure_openai_api_key="ak",
    )
    azure = {p.name: p for p in build_providers(settings)}["azure"]
    assert azure.auth_scheme is AuthScheme.HEADER
    assert azure.auth_param == "api-key"
    # Azure is wire-compatible with OpenAI, so it shares the parser.
    assert azure.parser_name == "openai"
    assert azure.api_key == "ak"
    assert azure.upstream_base_url == "https://x.openai.azure.com"
