"""Built-in provider definitions as a declarative table.

Each row is a :class:`ProviderSpec` binding a path segment to an upstream base
URL, an authentication scheme, and a telemetry parser - all by reference to
:class:`~blackbox_ai.config.Settings` field names, so the catalog stays pure
data. Adding a provider is one row; nothing else in the relay changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from blackbox_ai.config import Settings
from blackbox_ai.providers.base import AuthScheme, ProviderConfig

__all__ = ["ProviderSpec", "build_providers"]


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """A declarative provider row resolved against settings at startup.

    Attributes:
        name: Path segment that selects this provider (``/{name}/...``).
        base_url_attr: ``Settings`` field holding the upstream base URL.
        auth_scheme: How the credential is carried upstream.
        parser_name: Telemetry parser key for this wire format.
        auth_param: Header/query parameter name (HEADER/QUERY schemes).
        api_key_attr: ``Settings`` field holding the sovereign key, if any.
        requires_base_url: When true, the provider is only registered if its
            base URL is configured (Azure has no universal host).
    """

    name: str
    base_url_attr: str
    auth_scheme: AuthScheme
    parser_name: str
    auth_param: str = ""
    api_key_attr: str | None = None
    requires_base_url: bool = False

    def enabled(self, settings: Settings) -> bool:
        if not self.requires_base_url:
            return True
        return bool(getattr(settings, self.base_url_attr))

    def to_config(self, settings: Settings) -> ProviderConfig:
        # Unwrap the SecretStr at this boundary so ProviderConfig and the relay
        # keep working with a plain str credential.
        secret = getattr(settings, self.api_key_attr) if self.api_key_attr else None
        api_key = secret.get_secret_value() if secret is not None else None
        return ProviderConfig(
            name=self.name,
            upstream_base_url=getattr(settings, self.base_url_attr),
            auth_scheme=self.auth_scheme,
            parser_name=self.parser_name,
            auth_param=self.auth_param,
            api_key=api_key,
        )


# A missing ``api_key`` simply switches a provider into transparent credential
# pass-through. Azure is wire-compatible with the OpenAI API, hence the shared
# "openai" parser; it is the only row gated on a configured endpoint.
_PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="openai",
        base_url_attr="openai_base_url",
        auth_scheme=AuthScheme.BEARER,
        parser_name="openai",
        api_key_attr="openai_api_key",
    ),
    ProviderSpec(
        name="anthropic",
        base_url_attr="anthropic_base_url",
        auth_scheme=AuthScheme.HEADER,
        parser_name="anthropic",
        auth_param="x-api-key",
        api_key_attr="anthropic_api_key",
    ),
    ProviderSpec(
        name="gemini",
        base_url_attr="gemini_base_url",
        auth_scheme=AuthScheme.HEADER,
        parser_name="gemini",
        auth_param="x-goog-api-key",
        api_key_attr="gemini_api_key",
    ),
    ProviderSpec(
        name="ollama",
        base_url_attr="ollama_base_url",
        auth_scheme=AuthScheme.NONE,
        parser_name="ollama",
    ),
    ProviderSpec(
        name="azure",
        base_url_attr="azure_openai_endpoint",
        auth_scheme=AuthScheme.HEADER,
        parser_name="openai",
        auth_param="api-key",
        api_key_attr="azure_openai_api_key",
        requires_base_url=True,
    ),
)


def build_providers(settings: Settings) -> list[ProviderConfig]:
    """Construct the list of configured providers from settings."""
    return [spec.to_config(settings) for spec in _PROVIDER_SPECS if spec.enabled(settings)]
