"""Lookup of providers by their path segment."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from blackbox_ai.config import Settings
from blackbox_ai.providers.base import ProviderConfig
from blackbox_ai.providers.catalog import build_providers

__all__ = ["ProviderRegistry", "build_registry"]


class ProviderRegistry:
    """An immutable, case-insensitive mapping of name -> ProviderConfig."""

    def __init__(self, providers: Iterable[ProviderConfig]) -> None:
        self._by_name: dict[str, ProviderConfig] = {p.name.lower(): p for p in providers}

    def get(self, name: str) -> ProviderConfig | None:
        """Return the provider for ``name`` or ``None`` if unknown."""
        return self._by_name.get(name.lower())

    def names(self) -> list[str]:
        """Sorted list of registered provider names."""
        return sorted(self._by_name)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.lower() in self._by_name

    def __iter__(self) -> Iterator[ProviderConfig]:
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)


def build_registry(settings: Settings) -> ProviderRegistry:
    """Build the provider registry from application settings."""
    return ProviderRegistry(build_providers(settings))
