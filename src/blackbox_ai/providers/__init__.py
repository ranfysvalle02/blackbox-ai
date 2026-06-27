"""Provider configuration: how to route, authenticate, and parse each backend."""

from __future__ import annotations

from blackbox_ai.providers.base import AuthScheme, ProviderConfig
from blackbox_ai.providers.registry import ProviderRegistry, build_registry

__all__ = ["AuthScheme", "ProviderConfig", "ProviderRegistry", "build_registry"]
