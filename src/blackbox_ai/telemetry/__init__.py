"""The telemetry plane: capture, parse, and persist Intent Documents.

Everything in this package runs out-of-band relative to the request path. If any
of it fails, the client's request still completes - telemetry is best-effort.
"""

from __future__ import annotations

from blackbox_ai.telemetry.capture import CaptureBuffer, RawCapture
from blackbox_ai.telemetry.models import (
    IntentDocument,
    IntentTelemetry,
    Performance,
)
from blackbox_ai.telemetry.pipeline import TelemetryPipeline

__all__ = [
    "CaptureBuffer",
    "IntentDocument",
    "IntentTelemetry",
    "Performance",
    "RawCapture",
    "TelemetryPipeline",
]
