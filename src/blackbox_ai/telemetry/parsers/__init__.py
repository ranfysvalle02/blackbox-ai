"""Per-provider response parsers that distil raw bytes into telemetry.

Parsers are deliberately defensive: they never raise on malformed input, instead
returning a :class:`~blackbox_ai.telemetry.parsers.base.ParseResult` whose
``status`` reflects how much was understood. This keeps the telemetry worker
simple and guarantees raw payloads are persisted even when parsing degrades.
"""

from __future__ import annotations

from blackbox_ai.telemetry.parsers.anthropic_sse import AnthropicParser
from blackbox_ai.telemetry.parsers.base import ParseResult, StreamParser
from blackbox_ai.telemetry.parsers.gemini_sse import GeminiParser
from blackbox_ai.telemetry.parsers.ollama_ndjson import OllamaParser
from blackbox_ai.telemetry.parsers.openai_sse import OpenAIParser

__all__ = [
    "AnthropicParser",
    "GeminiParser",
    "OllamaParser",
    "OpenAIParser",
    "ParseResult",
    "StreamParser",
    "build_parser_registry",
]


def build_parser_registry() -> dict[str, StreamParser]:
    """Map provider ``parser_name`` values to parser instances."""
    parsers: tuple[StreamParser, ...] = (
        OpenAIParser(),
        AnthropicParser(),
        GeminiParser(),
        OllamaParser(),
    )
    return {parser.name: parser for parser in parsers}
