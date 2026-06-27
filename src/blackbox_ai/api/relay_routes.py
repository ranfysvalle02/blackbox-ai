"""The single catch-all relay route shared by every provider."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from blackbox_ai.errors import UnknownProviderError
from blackbox_ai.middleware.context import current_context
from blackbox_ai.state import AppState

__all__ = ["router"]

router = APIRouter(tags=["relay"])

_RELAY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


@router.api_route("/{provider}/{upstream_path:path}", methods=_RELAY_METHODS)
async def relay(provider: str, upstream_path: str, request: Request) -> StreamingResponse:
    """Forward ``/{provider}/{upstream_path}`` to the matching backend."""
    state: AppState = request.app.state.gateway
    provider_config = state.registry.get(provider)
    if provider_config is None:
        raise UnknownProviderError(
            f"Unknown provider '{provider}'.",
            details={"available": state.registry.names()},
        )
    context = current_context(request.scope)
    return await state.relay.handle(request, provider_config, upstream_path, context)
