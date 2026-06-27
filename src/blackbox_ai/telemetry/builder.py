"""Assemble an :class:`IntentDocument` from a raw capture and its parser."""

from __future__ import annotations

import re

from blackbox_ai.telemetry.capture import RawCapture
from blackbox_ai.telemetry.models import (
    IntentDocument,
    IntentTelemetry,
    ParseStatus,
    Performance,
)
from blackbox_ai.telemetry.parsers.base import ParseResult, StreamParser, safe_json

__all__ = ["build_document"]

# Gemini carries the model in the URL path (".../models/<model>:generateContent").
_GEMINI_MODEL_RE = re.compile(r"models/([^:/]+)")


def build_document(capture: RawCapture, parser: StreamParser | None) -> IntentDocument:
    """Combine routing metadata, the request payload, and parsed telemetry."""
    raw_payload = safe_json(capture.request_body.decode("utf-8", errors="replace"))
    model_requested = _extract_requested_model(capture, raw_payload)
    parse_result = _run_parser(parser, capture)

    performance = Performance(
        latency_ms=capture.latency_ms,
        ttft_ms=capture.ttft_ms,
        input_tokens=parse_result.input_tokens,
        output_tokens=parse_result.output_tokens,
        total_tokens=parse_result.total_tokens,
    )
    telemetry = IntentTelemetry(
        content=parse_result.content,
        chain_of_thought=parse_result.chain_of_thought,
        tools_called=parse_result.tools_called,
        finish_reason=parse_result.finish_reason,
        parse_status=parse_result.status,
        parse_error=parse_result.error,
    )
    context = capture.context
    return IntentDocument(
        request_id=capture.request_id,
        session_id=context.session_id,
        project_id=context.project_id,
        developer_id=context.developer_id,
        timestamp=capture.timestamp,
        provider=capture.provider,
        method=capture.method,
        endpoint=capture.endpoint,
        model_requested=model_requested,
        model_responded=parse_result.model,
        streamed=capture.streamed,
        status=capture.status,
        http_status=capture.http_status,
        error=capture.error,
        response_truncated=capture.response_truncated,
        performance=performance,
        raw_payload=raw_payload,
        intent_telemetry=telemetry,
        cache_key=capture.cache_key,
        served_from_cache=capture.served_from_cache,
    )


def _run_parser(parser: StreamParser | None, capture: RawCapture) -> ParseResult:
    if parser is None:
        return ParseResult(status=ParseStatus.UNPARSED, error="no parser registered")
    # Parsers are written to be non-raising, but we defend the worker regardless:
    # a parser bug must never lose the raw payload we already captured.
    try:
        return parser.parse(
            capture.response_body,
            content_type=capture.response_content_type,
            streamed=capture.streamed,
        )
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        return ParseResult(status=ParseStatus.ERROR, error=f"{type(exc).__name__}: {exc}")


def _extract_requested_model(
    capture: RawCapture, raw_payload: dict[str, object] | None
) -> str | None:
    if raw_payload is not None:
        model = raw_payload.get("model")
        if isinstance(model, str) and model:
            return model
    match = _GEMINI_MODEL_RE.search(capture.endpoint)
    return match.group(1) if match else None
