"""Shared application state assembled at startup and stored on ``app.state``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection

from blackbox_ai.config import Settings
from blackbox_ai.providers.registry import ProviderRegistry
from blackbox_ai.proxy.relay import Relay
from blackbox_ai.telemetry.pipeline import TelemetryPipeline

if TYPE_CHECKING:
    from blackbox_ai.cache.store import CacheStore
    from blackbox_ai.search import SearchService
    from blackbox_ai.security.encryption import EncryptionManager

__all__ = ["AppState"]


@dataclass(slots=True)
class AppState:
    """Process-wide singletons shared across requests."""

    settings: Settings
    registry: ProviderRegistry
    relay: Relay
    pipeline: TelemetryPipeline
    http_client: httpx.AsyncClient
    mongo_client: AsyncMongoClient[dict[str, Any]]
    collection: AsyncCollection[dict[str, Any]]
    # Phase 4 (optional, depending on configuration).
    cache_store: CacheStore | None = None
    search_service: SearchService | None = None
    encryption: EncryptionManager | None = None
