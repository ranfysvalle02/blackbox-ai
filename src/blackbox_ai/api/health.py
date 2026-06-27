"""Liveness and readiness probes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status
from pymongo.errors import PyMongoError

from blackbox_ai.db.mongo import ping
from blackbox_ai.state import AppState

__all__ = ["router"]

router = APIRouter(tags=["health"])


def _state(request: Request) -> AppState:
    state: AppState = request.app.state.gateway
    return state


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up and serving."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, Any]:
    """Readiness: MongoDB reachable and telemetry workers running."""
    state = _state(request)
    mongo_ok = True
    try:
        await ping(state.mongo_client)
    except PyMongoError:
        mongo_ok = False

    pipeline_ok = state.pipeline.is_running
    ready = mongo_ok and pipeline_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if ready else "degraded",
        "mongo": "ok" if mongo_ok else "unavailable",
        "telemetry_pipeline": "running" if pipeline_ok else "stopped",
        "telemetry_metrics": state.pipeline.metrics.as_dict(),
        "providers": state.registry.names(),
    }
