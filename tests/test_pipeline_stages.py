"""Unit tests for the composable telemetry stages and the capture-buffer factory."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from starlette.requests import Request

from blackbox_ai.cache.keys import CacheIdentity
from blackbox_ai.middleware.context import RequestContext
from blackbox_ai.providers.base import AuthScheme, ProviderConfig
from blackbox_ai.telemetry.capture import CaptureBuffer, RawCapture
from blackbox_ai.telemetry.models import CaptureStatus, IntentDocument, IntentTelemetry
from blackbox_ai.telemetry.stages import (
    CacheWriteStage,
    EmbedStage,
    MongoWriteStage,
    PipelineMetrics,
    TelemetryRecord,
    _cacheable_identity,
)
from tests.conftest import FailingSink, FakeSink
from tests.test_cache import FakeCacheStore

PROVIDER = ProviderConfig(
    name="openai",
    upstream_base_url="https://api.openai.com",
    auth_scheme=AuthScheme.BEARER,
    parser_name="openai",
)


def _capture(**over: Any) -> RawCapture:
    base: dict[str, Any] = {
        "request_id": "r",
        "context": RequestContext(request_id="r"),
        "provider": "openai",
        "parser_name": "openai",
        "method": "POST",
        "endpoint": "v1/chat/completions",
        "query_string": "",
        "timestamp": datetime.now(UTC),
        "request_body": b"{}",
        "response_body": b"resp",
        "response_content_type": "application/json",
        "http_status": 200,
        "streamed": False,
        "latency_ms": 1.0,
        "ttft_ms": 1.0,
        "status": CaptureStatus.OK,
        "error": None,
        "response_truncated": False,
        "cache_key": "k",
        "cache_requested": True,
        "served_from_cache": False,
    }
    base.update(over)
    return RawCapture(**base)


def _record(**over: Any) -> TelemetryRecord:
    capture = _capture(**over)
    document = IntentDocument(
        request_id=capture.request_id,
        timestamp=capture.timestamp,
        provider=capture.provider,
        method=capture.method,
        endpoint=capture.endpoint,
        raw_payload={"model": "gpt-4o"},
        intent_telemetry=IntentTelemetry(content="hi"),
    )
    return TelemetryRecord(capture=capture, document=document)


# --- _cacheable_identity ---------------------------------------------------


def test_cacheable_identity_valid() -> None:
    identity = _cacheable_identity(_capture())
    assert identity == CacheIdentity(key="k", streamed=False)


def test_cacheable_identity_rejects_replayed() -> None:
    assert _cacheable_identity(_capture(served_from_cache=True)) is None


def test_cacheable_identity_rejects_truncated() -> None:
    assert _cacheable_identity(_capture(response_truncated=True)) is None


def test_cacheable_identity_rejects_non_2xx() -> None:
    assert _cacheable_identity(_capture(http_status=503)) is None


def test_cacheable_identity_rejects_not_opted_in() -> None:
    assert _cacheable_identity(_capture(cache_requested=False)) is None


# --- stage behavior --------------------------------------------------------


async def test_mongo_write_marks_persisted_and_counts() -> None:
    metrics = PipelineMetrics()
    records = [_record(), _record()]
    await MongoWriteStage(FakeSink(), metrics).process(records)
    assert metrics.written == 2
    assert all(r.persisted for r in records)


async def test_mongo_write_is_fail_open() -> None:
    metrics = PipelineMetrics()
    records = [_record()]
    # Must not raise even though the sink always errors.
    await MongoWriteStage(FailingSink(), metrics).process(records)
    assert metrics.failed == 1
    assert records[0].persisted is False


async def test_cache_write_only_for_persisted_records() -> None:
    metrics = PipelineMetrics()
    store = FakeCacheStore()
    persisted = _record()
    persisted.persisted = True
    skipped = _record()  # persisted defaults to False
    await CacheWriteStage(store, metrics).process([persisted, skipped])  # type: ignore[arg-type]
    assert len(store.puts) == 1
    assert metrics.cached == 1


async def test_embed_stage_attaches_vectors() -> None:
    class _FakeEmbedder:
        model_name = "fake"
        dims = 3

        async def embed_documents(self, texts: list[str]) -> list[list[float] | None]:
            return [[1.0, 2.0, 3.0] for _ in texts]

        async def embed_query(self, text: str) -> list[float] | None:
            return [1.0, 2.0, 3.0]

    metrics = PipelineMetrics()
    record = _record()
    await EmbedStage(_FakeEmbedder(), metrics).process([record])
    assert record.document.embedding == [1.0, 2.0, 3.0]
    assert record.document.embedding_model == "fake"
    assert metrics.embedded == 1


# --- CaptureBuffer.for_request factory -------------------------------------


def test_capture_buffer_for_request_maps_all_fields() -> None:
    context = RequestContext(request_id="req-1")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/openai/v1/chat/completions",
            "headers": [],
            "query_string": b"alt=sse",
        }
    )
    buffer = CaptureBuffer.for_request(
        context=context,
        provider=PROVIDER,
        request=request,
        upstream_path="v1/chat/completions",
        body=b"{}",
        max_bytes=4096,
        cache_key="ck",
        cache_requested=True,
        served_from_cache=True,
    )
    assert buffer.request_id == "req-1"
    assert buffer.provider == "openai"
    assert buffer.parser_name == "openai"
    assert buffer.method == "POST"
    assert buffer.endpoint == "v1/chat/completions"
    assert buffer.query_string == "alt=sse"
    assert buffer.request_body == b"{}"
    assert buffer.max_capture_bytes == 4096
    assert buffer.cache_key == "ck"
    assert buffer.cache_requested is True
    assert buffer.served_from_cache is True
