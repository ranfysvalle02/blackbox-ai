"""Blackbox AI.

A native pass-through LLM proxy that streams provider responses with zero added
latency on the request path, while capturing polymorphic "Intent Documents" into
MongoDB entirely out-of-band and fail-open.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
