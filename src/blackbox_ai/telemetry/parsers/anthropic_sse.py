"""Anthropic Messages API parser.

Streaming uses named SSE events (``message_start``, ``content_block_delta``,
``message_delta`` ...). Extended-thinking deltas are captured separately as the
chain of thought, and ``tool_use`` blocks are reassembled from their streamed
``input_json_delta`` fragments.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from blackbox_ai.telemetry.models import ParseStatus
from blackbox_ai.telemetry.parsers.base import (
    ParseResult,
    decode,
    iter_sse_events,
    join_text,
    looks_like_sse,
    safe_json,
)

__all__ = ["AnthropicParser"]


class AnthropicParser:
    """Parser for Anthropic's ``/v1/messages`` wire format."""

    name = "anthropic"

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
        result = ParseResult(model=obj.get("model"), finish_reason=obj.get("stop_reason"))
        self._apply_usage(result, obj.get("usage"))
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tools: list[dict[str, Any]] = []
        for block in obj.get("content") or []:
            block_type = block.get("type")
            if block_type == "text":
                content_parts.append(block.get("text", ""))
            elif block_type == "thinking":
                thinking_parts.append(block.get("thinking", ""))
            elif block_type == "tool_use":
                tools.append(
                    {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "arguments": block.get("input"),
                    }
                )
        result.content = join_text(content_parts)
        result.chain_of_thought = join_text(thinking_parts)
        result.tools_called = tools
        return result

    def _parse_stream(self, text: str) -> ParseResult:
        result = ParseResult()
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_blocks: dict[int, dict[str, Any]] = {}
        seen = False
        for event_name, data in iter_sse_events(text):
            obj = safe_json(data)
            if obj is None:
                result.status = ParseStatus.PARTIAL
                continue
            seen = True
            event = event_name or obj.get("type")
            if event == "message_start":
                message = obj.get("message") or {}
                result.model = message.get("model") or result.model
                self._apply_usage(result, message.get("usage"))
            elif event == "content_block_start":
                block = obj.get("content_block") or {}
                if block.get("type") == "tool_use":
                    tool_blocks[obj.get("index", 0)] = {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "arguments": "",
                    }
            elif event == "content_block_delta":
                self._apply_block_delta(obj, content_parts, thinking_parts, tool_blocks)
            elif event == "message_delta":
                delta = obj.get("delta") or {}
                if delta.get("stop_reason"):
                    result.finish_reason = delta["stop_reason"]
                self._apply_usage(result, obj.get("usage"))
        result.content = join_text(content_parts)
        result.chain_of_thought = join_text(thinking_parts)
        result.tools_called = self._finalize_tools(tool_blocks)
        if not seen and result.status is ParseStatus.OK:
            result.status = ParseStatus.UNPARSED
        return result

    @staticmethod
    def _apply_block_delta(
        obj: dict[str, Any],
        content_parts: list[str],
        thinking_parts: list[str],
        tool_blocks: dict[int, dict[str, Any]],
    ) -> None:
        delta = obj.get("delta") or {}
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            content_parts.append(delta.get("text", ""))
        elif delta_type == "thinking_delta":
            thinking_parts.append(delta.get("thinking", ""))
        elif delta_type == "input_json_delta":
            block = tool_blocks.get(obj.get("index", 0))
            if block is not None:
                block["arguments"] += delta.get("partial_json", "")

    @staticmethod
    def _finalize_tools(tool_blocks: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for block in tool_blocks.values():
            arguments = block["arguments"]
            # Keep the raw string when streamed tool input is not valid JSON.
            if isinstance(arguments, str) and arguments:
                with contextlib.suppress(json.JSONDecodeError):
                    arguments = json.loads(arguments)
            tools.append({"id": block["id"], "name": block["name"], "arguments": arguments})
        return tools

    @staticmethod
    def _apply_usage(result: ParseResult, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        if usage.get("input_tokens") is not None:
            result.input_tokens = usage["input_tokens"]
        if usage.get("output_tokens") is not None:
            result.output_tokens = usage["output_tokens"]
        if result.input_tokens is not None and result.output_tokens is not None:
            result.total_tokens = result.input_tokens + result.output_tokens
