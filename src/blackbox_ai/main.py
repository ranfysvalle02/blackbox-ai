"""Application factory and lifespan wiring.

Startup connects MongoDB (best-effort, to preserve fail-open: the relay still
works if the database is down - only telemetry persistence is affected), builds
the provider registry and telemetry pipeline, and opens a shared, pooled HTTP
client. Shutdown drains the pipeline and closes both clients gracefully.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import orjson
from fastapi import FastAPI, Request
from pymongo.errors import PyMongoError
from starlette.responses import Response

from blackbox_ai.api.admin import router as admin_router
from blackbox_ai.api.health import router as health_router
from blackbox_ai.api.metrics import router as metrics_router
from blackbox_ai.api.relay_routes import router as relay_router
from blackbox_ai.bootstrap import build_embedder, build_encryption_manager, ensure_storage
from blackbox_ai.cache.gate import CacheGate, CachePolicy
from blackbox_ai.cache.store import CacheStore
from blackbox_ai.config import Settings, get_settings
from blackbox_ai.db.mongo import create_client, ping
from blackbox_ai.errors import GatewayError
from blackbox_ai.logging import configure_logging, get_logger
from blackbox_ai.metrics import register_pipeline_collector, unregister_pipeline_collector
from blackbox_ai.middleware.context import RequestContextMiddleware
from blackbox_ai.providers.registry import build_registry
from blackbox_ai.proxy.relay import Relay
from blackbox_ai.search import SearchService
from blackbox_ai.state import AppState
from blackbox_ai.telemetry.parsers import build_parser_registry
from blackbox_ai.telemetry.pipeline import TelemetryPipeline
from blackbox_ai.telemetry.sink_mongo import MongoSink

__all__ = ["app", "create_app"]

_log = get_logger("blackbox_ai.main")


def _build_http_client(settings: Settings) -> httpx.AsyncClient:
    timeout = httpx.Timeout(settings.http_timeout_s, connect=settings.http_connect_timeout_s)
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
    # follow_redirects stays off: a transparent proxy must relay 3xx verbatim.
    return httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=False)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    # Secure-by-default self-check: warn on a risky posture, and in production
    # fail-closed (e.g. refuse to run an open relay) before doing any work.
    report = settings.runtime_security_report()
    for warning in report.warnings:
        _log.warning("insecure_configuration", detail=warning)
    if not report.ok:
        for problem in report.fatal:
            _log.error("insecure_configuration_fatal", detail=problem)
        raise RuntimeError("; ".join(report.fatal))

    # Fail-closed: if QE is enabled but the key / crypt_shared is misconfigured,
    # this raises and aborts startup rather than silently writing plaintext.
    try:
        encryption = build_encryption_manager(settings)
    except ValueError as exc:
        _log.error("encryption_misconfigured", error=str(exc))
        raise

    embedder = build_embedder(settings)
    # Plain client handles health pings, key-vault bootstrap, and index setup.
    admin_client = create_client(settings)

    try:
        await ping(admin_client)
        await ensure_storage(
            settings,
            admin_client=admin_client,
            encryption=encryption,
            embedder=embedder,
        )
        _log.info(
            "mongo_connected",
            database=settings.mongo_db,
            collection=settings.mongo_collection,
        )
    except PyMongoError as exc:
        # Fail-open: start anyway. Readiness will report degraded; the relay still
        # serves traffic and telemetry will persist once Mongo recovers.
        _log.warning("mongo_unavailable_at_startup", error=str(exc))

    # Data client encrypts/decrypts QE fields transparently; without QE it is the
    # same plain client (no second connection pool).
    data_client = encryption.build_encrypting_client() if encryption else admin_client
    collection = data_client[settings.mongo_db][settings.mongo_collection]

    cache_store: CacheStore | None = None
    if settings.cache_enabled:
        cache_store = CacheStore(
            data_client[settings.mongo_db][settings.cache_collection],
            ttl_s=settings.cache_ttl_s,
        )

    registry = build_registry(settings)
    prune_null_paths = encryption.intent_encrypted_paths if encryption else ()
    pipeline = TelemetryPipeline(
        sink=MongoSink(collection, prune_null_paths=prune_null_paths),
        parsers=build_parser_registry(),
        maxsize=settings.telemetry_queue_maxsize,
        worker_count=settings.telemetry_workers,
        batch_size=settings.telemetry_batch_size,
        flush_interval_s=settings.telemetry_flush_interval_s,
        embedder=embedder,
        cache_store=cache_store,
    )
    pipeline.start()
    register_pipeline_collector(pipeline)

    http_client = _build_http_client(settings)
    cache_gate = CacheGate(
        cache_store,
        CachePolicy(
            default_on=settings.cache_default_on,
            lookup_timeout_s=settings.cache_lookup_timeout_s,
        ),
    )
    relay = Relay(http_client, pipeline, settings, cache=cache_gate)

    search_service: SearchService | None = None
    if embedder.dims > 0:
        search_service = SearchService(
            collection,
            embedder,
            vector_index_name=settings.vector_index_name,
            search_index_name=settings.search_index_name,
        )

    app.state.gateway = AppState(
        settings=settings,
        registry=registry,
        relay=relay,
        pipeline=pipeline,
        http_client=http_client,
        mongo_client=admin_client,
        collection=collection,
        cache_store=cache_store,
        search_service=search_service,
        encryption=encryption,
    )
    _log.info(
        "gateway_ready",
        env=settings.deployment_env.value,
        providers=registry.names(),
        auth=settings.effective_require_auth,
        rate_limited=settings.rate_limit_enabled,
        encryption=encryption is not None,
        embeddings=embedder.model_name,
        cache=settings.cache_enabled,
    )

    try:
        yield
    finally:
        unregister_pipeline_collector()
        await pipeline.stop()
        await http_client.aclose()
        await data_client.close()
        if data_client is not admin_client:
            await admin_client.close()
        _log.info("gateway_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construct and configure the FastAPI application."""
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    app = FastAPI(
        title="Blackbox AI",
        version="0.1.0",
        summary="Native pass-through LLM proxy that captures coding-intent telemetry.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(GatewayError)
    async def _gateway_error_handler(_request: Request, exc: GatewayError) -> Response:
        return Response(
            content=orjson.dumps(exc.to_dict()),
            status_code=exc.status_code,
            media_type="application/json",
            headers=exc.headers or None,
        )

    # Health, metrics, and admin first; the relay catch-all is registered last so
    # it never shadows the fixed routes.
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(admin_router)
    app.include_router(relay_router)
    return app


app = create_app()
