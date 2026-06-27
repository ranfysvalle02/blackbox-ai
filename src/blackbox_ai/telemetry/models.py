"""Pydantic models for the polymorphic Intent Document persisted to MongoDB.

The document deliberately keeps ``raw_payload`` free-form (a parsed copy of the
client's request body) so that any provider's request shape - prompts, tools,
hidden reasoning configuration - is retained verbatim for replay. Normalised,
indexable fields are lifted alongside it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CaptureStatus",
    "IntentDocument",
    "IntentTelemetry",
    "ParseStatus",
    "Performance",
]


class CaptureStatus(StrEnum):
    """Outcome of the relay from the telemetry plane's point of view."""

    OK = "ok"
    UPSTREAM_ERROR = "upstream_error"
    CLIENT_DISCONNECT = "client_disconnect"


class ParseStatus(StrEnum):
    """How completely the provider parser understood the captured bytes."""

    OK = "ok"
    PARTIAL = "partial"
    ERROR = "error"
    UNPARSED = "unparsed"


class Performance(BaseModel):
    """Latency and token-usage metrics for a single interaction."""

    model_config = ConfigDict(extra="forbid")

    latency_ms: float | None = None
    ttft_ms: float | None = Field(
        default=None, description="Time to first streamed byte, in milliseconds."
    )
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class IntentTelemetry(BaseModel):
    """The distilled 'why' behind an interaction, extracted from the response."""

    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(
        default=None, description="Assistant-visible output text, concatenated."
    )
    chain_of_thought: str | None = Field(
        default=None, description="Hidden reasoning / thinking tokens, if exposed."
    )
    tools_called: list[dict[str, Any]] = Field(default_factory=list)
    finish_reason: str | None = None
    parse_status: ParseStatus = ParseStatus.OK
    parse_error: str | None = None


class IntentDocument(BaseModel):
    """A single, highly-indexable record of one moment of AI intent."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1

    # --- Sovereign metadata (from headers) ---------------------------------
    request_id: str
    session_id: str | None = None
    project_id: str | None = None
    developer_id: str | None = None

    # --- Routing / identity ------------------------------------------------
    timestamp: datetime
    provider: str
    method: str
    endpoint: str
    model_requested: str | None = None
    model_responded: str | None = None
    streamed: bool = False

    # --- Outcome -----------------------------------------------------------
    status: CaptureStatus = CaptureStatus.OK
    http_status: int | None = None
    error: str | None = None
    response_truncated: bool = False

    # --- Payloads & distilled telemetry ------------------------------------
    performance: Performance = Field(default_factory=Performance)
    raw_payload: dict[str, Any] | None = None
    intent_telemetry: IntentTelemetry = Field(default_factory=IntentTelemetry)

    # --- Vector search / caching (Phase 4); set on ingestion ----------------
    embedding: list[float] | None = None
    embedding_model: str | None = None
    cache_key: str | None = None
    # True when this interaction was served from the gateway cache rather than
    # forwarded upstream (the replay still produces a telemetry record).
    served_from_cache: bool = False

    def to_mongo(self) -> dict[str, Any]:
        """Serialise to a BSON-friendly dict (datetimes preserved as objects)."""
        return self.model_dump(mode="python", exclude_none=False)
