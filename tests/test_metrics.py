"""Prometheus metrics: collector output and the /metrics endpoint."""

from __future__ import annotations

import httpx
import respx

from blackbox_ai.metrics import PipelineCollector
from blackbox_ai.telemetry.parsers import build_parser_registry
from blackbox_ai.telemetry.pipeline import TelemetryPipeline
from tests.conftest import FakeSink, build_harness, default_settings, load_fixture, wait_until

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def test_pipeline_collector_reads_live_metrics_and_queue() -> None:
    pipeline = TelemetryPipeline(sink=FakeSink(), parsers=build_parser_registry(), maxsize=2048)
    pipeline.metrics.submitted = 7
    pipeline.metrics.written = 5
    pipeline.metrics.dropped = 2

    families = {m.name: m for m in PipelineCollector(pipeline).collect()}

    submitted = families["blackbox_telemetry_submitted"]
    assert submitted.samples[0].name == "blackbox_telemetry_submitted_total"
    assert submitted.samples[0].value == 7
    assert families["blackbox_telemetry_dropped"].samples[0].value == 2
    assert families["blackbox_telemetry_written"].samples[0].value == 5
    # Queue depth and capacity are read live (nothing enqueued here).
    assert families["blackbox_telemetry_queue_size"].samples[0].value == 0
    assert families["blackbox_telemetry_queue_maxsize"].samples[0].value == 2048


@respx.mock
async def test_metrics_endpoint_exposes_relay_counters() -> None:
    body = load_fixture("openai_completion.json")
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, headers={"content-type": "application/json"}, content=body)
    )

    async with build_harness(default_settings()) as harness:
        await harness.client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": []},
        )
        # Metrics are observed in the streaming finalize path; wait for capture.
        assert await wait_until(lambda: len(harness.sink.documents) == 1)
        metrics = await harness.client.get("/metrics")

    assert metrics.status_code == 200
    assert "text/plain" in metrics.headers["content-type"]
    text = metrics.text
    # Module-level relay instruments are present even without the lifespan
    # (which is what registers the pipeline collector).
    assert "blackbox_relay_requests_total" in text
    assert 'provider="openai"' in text
    assert "blackbox_relay_request_duration_seconds" in text


async def test_metrics_protected_requires_admin_token() -> None:
    settings = default_settings(metrics_protected=True, admin_token="metrics-secret")
    async with build_harness(settings) as harness:
        unauth = await harness.client.get("/metrics")
        assert unauth.status_code == 401

        authed = await harness.client.get("/metrics", headers={"x-admin-token": "metrics-secret"})
        assert authed.status_code == 200
        assert "text/plain" in authed.headers["content-type"]
