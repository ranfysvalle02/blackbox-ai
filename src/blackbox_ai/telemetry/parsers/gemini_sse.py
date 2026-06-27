"""Google Gemini (Generative Language API) parser.

Supports three shapes: SSE (``?alt=sse`` streaming, the google-genai default for
``generate_content_stream``), a single ``generateContent`` JSON object, and the
JSON-array form returned by ``streamGenerateContent`` without ``alt=sse``.
Reasoning ("thought") parts are captured separately from visible text.
"""

from __future__ import annotations

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

__all__ = ["GeminiParser"]


class GeminiParser:
    """Parser for Gemini's ``generateContent`` family of responses."""

    name = "gemini"

    def parse(self, raw: bytes, *, content_type: str | None, streamed: bool) -> ParseResult:
        text = decode(raw)
        stripped = text.strip()
        if not stripped:
            return ParseResult(status=ParseStatus.UNPARSED, error="empty response body")
        if looks_like_sse(text, content_type):
            return self._parse_chunks(list(iter_data_payloads(text)))
        if stripped.startswith("["):
            return self._parse_json_array(stripped)
        return self._parse_chunks([stripped])

    def _parse_json_array(self, text: str) -> ParseResult:
        try:
            array = json.loads(text)
        except json.JSONDecodeError:
            return ParseResult(status=ParseStatus.UNPARSED, error="response was not JSON")
        if not isinstance(array, list):
            return ParseResult(status=ParseStatus.UNPARSED, error="expected a JSON array")
        return self._parse_chunks([json.dumps(item) for item in array])

    def _parse_chunks(self, payloads: list[str]) -> ParseResult:
        result = ParseResult()
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tools: list[dict[str, Any]] = []
        seen = False
        for payload in payloads:
            obj = safe_json(payload)
            if obj is None:
                result.status = ParseStatus.PARTIAL
                continue
            seen = True
            if obj.get("modelVersion"):
                result.model = obj["modelVersion"]
            self._apply_usage(result, obj.get("usageMetadata"))
            for candidate in obj.get("candidates") or []:
                if candidate.get("finishReason"):
                    result.finish_reason = candidate["finishReason"]
                parts = (candidate.get("content") or {}).get("parts") or []
                self._collect_parts(parts, content_parts, thinking_parts, tools)
        result.content = join_text(content_parts)
        result.chain_of_thought = join_text(thinking_parts)
        result.tools_called = tools
        if not seen and result.status is ParseStatus.OK:
            result.status = ParseStatus.UNPARSED
        return result

    @staticmethod
    def _collect_parts(
        parts: list[dict[str, Any]],
        content_parts: list[str],
        thinking_parts: list[str],
        tools: list[dict[str, Any]],
    ) -> None:
        for part in parts:
            if "text" in part:
                if part.get("thought"):
                    thinking_parts.append(part.get("text", ""))
                else:
                    content_parts.append(part.get("text", ""))
            function_call = part.get("functionCall")
            if function_call:
                tools.append(
                    {
                        "id": function_call.get("id"),
                        "name": function_call.get("name"),
                        "arguments": function_call.get("args"),
                    }
                )

    @staticmethod
    def _apply_usage(result: ParseResult, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        if usage.get("promptTokenCount") is not None:
            result.input_tokens = usage["promptTokenCount"]
        if usage.get("candidatesTokenCount") is not None:
            result.output_tokens = usage["candidatesTokenCount"]
        if usage.get("totalTokenCount") is not None:
            result.total_tokens = usage["totalTokenCount"]
