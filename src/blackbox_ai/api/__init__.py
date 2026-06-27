"""HTTP routers: health probes and the generic relay."""

from __future__ import annotations

from blackbox_ai.api.health import router as health_router
from blackbox_ai.api.relay_routes import router as relay_router

__all__ = ["health_router", "relay_router"]
