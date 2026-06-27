"""Unit tests for the per-provider telemetry parsers."""

from __future__ import annotations

from blackbox_ai.telemetry.models import ParseStatus
from blackbox_ai.telemetry.parsers import (
    AnthropicParser,
    GeminiParser,
    OllamaParser,
    OpenAIParser,
)
from tests.conftest import load_fixture


def test_openai_streaming() -> None:
    result = OpenAIParser().parse(
        load_fixture("openai_stream.sse"), content_type="text/event-stream", streamed=True
    )
    assert result.content == "Hello world"
    assert result.finish_reason == "stop"
    assert result.model == "gpt-4o-mini"
    assert result.input_tokens == 9
    assert result.output_tokens == 2
    assert result.total_tokens == 11
    assert result.status is ParseStatus.OK


def test_openai_non_streaming() -> None:
    result = OpenAIParser().parse(
        load_fixture("openai_completion.json"), content_type="application/json", streamed=False
    )
    assert result.content == "The answer is 42."
    assert result.finish_reason == "stop"
    assert result.input_tokens == 15
    assert result.output_tokens == 6


def test_openai_tool_calls_reassembled() -> None:
    result = OpenAIParser().parse(
        load_fixture("openai_tools_stream.sse"), content_type="text/event-stream", streamed=True
    )
    assert result.finish_reason == "tool_calls"
    assert len(result.tools_called) == 1
    tool = result.tools_called[0]
    assert tool["name"] == "read_file"
    # Fragmented JSON arguments are reassembled and decoded.
    assert tool["arguments"] == {"path": "db.py"}


def test_anthropic_streaming() -> None:
    result = AnthropicParser().parse(
        load_fixture("anthropic_stream.sse"), content_type="text/event-stream", streamed=True
    )
    assert result.content == "Hi there"
    assert result.finish_reason == "end_turn"
    assert result.model == "claude-3-5-sonnet-20241022"
    assert result.input_tokens == 12
    assert result.output_tokens == 5
    assert result.total_tokens == 17


def test_anthropic_thinking_is_captured_separately() -> None:
    result = AnthropicParser().parse(
        load_fixture("anthropic_thinking_stream.sse"),
        content_type="text/event-stream",
        streamed=True,
    )
    assert result.chain_of_thought == "The pool leak is in max_overflow."
    assert result.content == "Set max_overflow to 10."
    assert result.output_tokens == 14


def test_gemini_streaming() -> None:
    result = GeminiParser().parse(
        load_fixture("gemini_stream.sse"), content_type="text/event-stream", streamed=True
    )
    assert result.content == "Hello"
    assert result.finish_reason == "STOP"
    assert result.model == "gemini-2.0-flash"
    assert result.input_tokens == 7
    assert result.output_tokens == 2
    assert result.total_tokens == 9


def test_ollama_streaming() -> None:
    result = OllamaParser().parse(
        load_fixture("ollama_stream.ndjson"), content_type="application/x-ndjson", streamed=True
    )
    assert result.content == "Hey you"
    assert result.finish_reason == "stop"
    assert result.model == "llama3.2"
    assert result.input_tokens == 18
    assert result.output_tokens == 3
    assert result.total_tokens == 21


def test_parsers_are_resilient_to_garbage() -> None:
    # A malformed body must degrade gracefully, never raise.
    result = OpenAIParser().parse(
        b"data: {not json}\n\n", content_type="text/event-stream", streamed=True
    )
    assert result.status in {ParseStatus.PARTIAL, ParseStatus.UNPARSED}


def test_empty_body_is_unparsed() -> None:
    result = OpenAIParser().parse(b"", content_type=None, streamed=False)
    assert result.status is ParseStatus.UNPARSED
