"""OpenAI (and Azure OpenAI) Chat Completions parser.

Handles both the streaming Server-Sent Events form (``stream=true``) and the
single-object non-streaming response. Token usage is read from the ``usage``
object, which streaming clients receive when ``stream_options.include_usage`` is
set; otherwise token counts remain ``None``.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from blackbox_ai.telemetry.models import ParseStatus
from blackbox_ai.telemetry.parsers.base import (
    ParseResult,
    decode,
    iter_data_payloads,
    join_text,
    looks_like_sse,
    safe_json,
)

__all__ = ["OpenAIParser"]


class _ToolAccumulator:
    """Reassembles streamed tool-call fragments keyed by their index."""

    def __init__(self) -> None:
        self._by_index: dict[int, dict[str, Any]] = {}

    def add_fragment(self, fragment: dict[str, Any]) -> None:
        index = fragment.get("index", 0)
        slot = self._by_index.setdefault(index, {"id": None, "name": None, "arguments": ""})
        if fragment.get("id"):
            slot["id"] = fragment["id"]
        function = fragment.get("function") or {}
        if function.get("name"):
            slot["name"] = function["name"]
        if function.get("arguments"):
            slot["arguments"] += function["arguments"]

    def add_complete(self, tool_call: dict[str, Any]) -> None:
        index = len(self._by_index)
        function = tool_call.get("function") or {}
        self._by_index[index] = {
            "id": tool_call.get("id"),
            "name": function.get("name"),
            "arguments": function.get("arguments", ""),
        }

    def finalize(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for slot in self._by_index.values():
            arguments = slot["arguments"]
            # Keep the raw string when streamed arguments are not valid JSON.
            if isinstance(arguments, str) and arguments:
                with contextlib.suppress(json.JSONDecodeError):
                    arguments = json.loads(arguments)
            tools.append({"id": slot["id"], "name": slot["name"], "arguments": arguments})
        return tools


class OpenAIParser:
    """Parser for the OpenAI Chat Completions wire format."""

    name = "openai"

    def parse(self, raw: bytes, *, content_type: str | None, streamed: bool) -> ParseResult:
        text = decode(raw)
        if not text.strip():
            return ParseResult(status=ParseStatus.UNPARSED, error="empty response body")
        if streamed or looks_like_sse(text, content_type):
            return self._parse_stream(text)
        return self._parse_single(text)

    def _parse_single(self, text: str) -> ParseResult:
        obj = safe_json(text)
        if obj is None:
            return ParseResult(status=ParseStatus.UNPARSED, error="response was not JSON")
        result = ParseResult(model=obj.get("model"))
        self._apply_usage(result, obj.get("usage"))
        choices = obj.get("choices") or []
        if choices:
            choice = choices[0]
            message = choice.get("message") or {}
            result.content = message.get("content")
            result.finish_reason = choice.get("finish_reason")
            accumulator = _ToolAccumulator()
            for tool_call in message.get("tool_calls") or []:
                accumulator.add_complete(tool_call)
            result.tools_called = accumulator.finalize()
        return result

    def _parse_stream(self, text: str) -> ParseResult:
        result = ParseResult()
        content_parts: list[str] = []
        accumulator = _ToolAccumulator()
        seen = False
        for payload in iter_data_payloads(text):
            obj = safe_json(payload)
            if obj is None:
                result.status = ParseStatus.PARTIAL
                continue
            seen = True
            if obj.get("model"):
                result.model = obj["model"]
            self._apply_usage(result, obj.get("usage"))
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or {}
                if isinstance(delta.get("content"), str):
                    content_parts.append(delta["content"])
                for fragment in delta.get("tool_calls") or []:
                    accumulator.add_fragment(fragment)
                if choice.get("finish_reason"):
                    result.finish_reason = choice["finish_reason"]
        result.content = join_text(content_parts)
        result.tools_called = accumulator.finalize()
        if not seen and result.status is ParseStatus.OK:
            result.status = ParseStatus.UNPARSED
        return result

    @staticmethod
    def _apply_usage(result: ParseResult, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        if usage.get("prompt_tokens") is not None:
            result.input_tokens = usage["prompt_tokens"]
        if usage.get("completion_tokens") is not None:
            result.output_tokens = usage["completion_tokens"]
        if usage.get("total_tokens") is not None:
            result.total_tokens = usage["total_tokens"]
