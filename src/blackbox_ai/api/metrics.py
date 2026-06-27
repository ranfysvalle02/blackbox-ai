"""Prometheus ``/metrics`` exposition endpoint.

Renders the default registry in the Prometheus text format. The request-path
instruments live in :mod:`blackbox_ai.metrics`; telemetry-plane counters and
queue depth are contributed by the ``PipelineCollector`` registered at startup.

The endpoint is unauthenticated by default (the usual posture for scrape
targets on a private network). Set ``GATEWAY_METRICS_PROTECTED=true`` to require
the admin token, or front it with network policy.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from blackbox_ai.api._auth import require_admin_token
from blackbox_ai.config import Settings

__all__ = ["router"]

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    """Expose all registered metrics in the Prometheus text format."""
    settings: Settings = request.app.state.settings
    if settings.metrics_protected:
        require_admin_token(request, settings)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
