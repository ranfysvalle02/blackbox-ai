"""Structured logging setup built on structlog.

A single ``configure_logging`` call wires structlog to emit either machine
readable JSON (containers/production) or a human friendly console rendering
(local development). Request-scoped values such as ``request_id`` and
``session_id`` are merged automatically from contextvars, so every log line
emitted while handling a request is correlated without manual plumbing.
"""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog
from structlog.contextvars import merge_contextvars
from structlog.types import Processor

__all__ = ["configure_logging", "get_logger"]


def configure_logging(*, level: str = "INFO", json_logs: bool = True) -> None:
    """Configure structlog and the stdlib logging bridge.

    Args:
        level: Minimum log level name (e.g. ``"INFO"``, ``"DEBUG"``).
        json_logs: When True render JSON, otherwise a colourised console view.
    """
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    shared_processors: list[Processor] = [
        merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, pymongo, httpx) through the same sink/level.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=numeric_level,
        force=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger, optionally namespaced."""
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
