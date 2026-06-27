"""Parser protocol, result type, and shared SSE/NDJSON/JSON helpers."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from blackbox_ai.telemetry.models import ParseStatus

__all__ = [
    "ParseResult",
    "StreamParser",
    "decode",
    "iter_data_payloads",
    "iter_ndjson",
    "iter_sse_events",
    "join_text",
    "looks_like_sse",
    "safe_json",
]


@dataclass(slots=True)
class ParseResult:
    """Normalised telemetry extracted from a provider response."""

    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    content: str | None = None
    chain_of_thought: str | None = None
    tools_called: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    status: ParseStatus = ParseStatus.OK
    error: str | None = None


@runtime_checkable
class StreamParser(Protocol):
    """Translates a provider's raw response bytes into a :class:`ParseResult`."""

    name: str

    def parse(self, raw: bytes, *, content_type: str | None, streamed: bool) -> ParseResult:
        """Parse ``raw`` bytes; must never raise."""
        ...


def decode(raw: bytes) -> str:
    """Decode bytes as UTF-8, replacing undecodable sequences."""
    return raw.decode("utf-8", errors="replace")


def looks_like_sse(text: str, content_type: str | None) -> bool:
    """Heuristically detect Server-Sent Events framing."""
    if content_type and "event-stream" in content_type:
        return True
    return "data:" in text[:512]


def iter_sse_events(text: str) -> Iterator[tuple[str | None, str]]:
    """Yield ``(event_name, data)`` tuples from an SSE stream.

    Multiple ``data:`` lines within one event are joined with newlines, per the
    SSE spec. ``event_name`` is ``None`` when no ``event:`` field was present.
    """
    event_name: str | None = None
    data_lines: list[str] = []

    def flush() -> Iterator[tuple[str | None, str]]:
        nonlocal event_name, data_lines
        if data_lines:
            yield event_name, "\n".join(data_lines)
        event_name = None
        data_lines = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            yield from flush()
            continue
        if line.startswith(":"):  # SSE comment / keep-alive
            continue
        field_name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field_name == "event":
            event_name = value
        elif field_name == "data":
            data_lines.append(value)
    yield from flush()


def iter_data_payloads(text: str) -> Iterator[str]:
    """Yield the ``data:`` payloads of an SSE stream, skipping ``[DONE]``."""
    for _event, data in iter_sse_events(text):
        if data == "[DONE]":
            continue
        yield data


def iter_ndjson(text: str) -> Iterator[str]:
    """Yield non-empty lines from newline-delimited JSON."""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            yield line


def safe_json(payload: str) -> dict[str, Any] | None:
    """Parse a JSON object, returning ``None`` on failure or non-object types."""
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def join_text(parts: list[str]) -> str | None:
    """Join collected text fragments into a single string, or ``None``."""
    return "".join(parts) if parts else None
