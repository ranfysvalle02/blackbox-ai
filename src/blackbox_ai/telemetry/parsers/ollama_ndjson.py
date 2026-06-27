"""Ollama parser for the ``/api/chat`` and ``/api/generate`` endpoints.

Streaming responses are newline-delimited JSON (one object per line); the final
object carries ``done: true`` along with token counts. Non-streaming responses
are a single JSON object of the same shape.
"""

from __future__ import annotations

from typing import Any

from blackbox_ai.telemetry.models import ParseStatus
from blackbox_ai.telemetry.parsers.base import (
    ParseResult,
    decode,
    iter_ndjson,
    join_text,
    safe_json,
)

__all__ = ["OllamaParser"]


class OllamaParser:
    """Parser for Ollama's NDJSON wire format."""

    name = "ollama"

    def parse(self, raw: bytes, *, content_type: str | None, streamed: bool) -> ParseResult:
        text = decode(raw)
        if not text.strip():
            return ParseResult(status=ParseStatus.UNPARSED, error="empty response body")

        result = ParseResult()
        content_parts: list[str] = []
        tools: list[dict[str, Any]] = []
        seen = False
        for line in iter_ndjson(text):
            obj = safe_json(line)
            if obj is None:
                result.status = ParseStatus.PARTIAL
                continue
            seen = True
            if obj.get("model"):
                result.model = obj["model"]
            message = obj.get("message") or {}
            if isinstance(message.get("content"), str):
                content_parts.append(message["content"])
            if isinstance(obj.get("response"), str):  # /api/generate
                content_parts.append(obj["response"])
            self._collect_tools(message.get("tool_calls"), tools)
            if obj.get("done"):
                self._apply_done(result, obj)
        result.content = join_text(content_parts)
        result.tools_called = tools
        if not seen and result.status is ParseStatus.OK:
            result.status = ParseStatus.UNPARSED
        return result

    @staticmethod
    def _collect_tools(tool_calls: Any, tools: list[dict[str, Any]]) -> None:
        if not isinstance(tool_calls, list):
            return
        for tool_call in tool_calls:
            function = tool_call.get("function") or {}
            tools.append(
                {
                    "id": tool_call.get("id"),
                    "name": function.get("name"),
                    "arguments": function.get("arguments"),
                }
            )

    @staticmethod
    def _apply_done(result: ParseResult, obj: dict[str, Any]) -> None:
        if obj.get("prompt_eval_count") is not None:
            result.input_tokens = obj["prompt_eval_count"]
        if obj.get("eval_count") is not None:
            result.output_tokens = obj["eval_count"]
        if result.input_tokens is not None and result.output_tokens is not None:
            result.total_tokens = result.input_tokens + result.output_tokens
        if obj.get("done_reason"):
            result.finish_reason = obj["done_reason"]
