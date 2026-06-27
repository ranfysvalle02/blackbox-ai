"""MongoDB connection management and index setup."""

from __future__ import annotations

from blackbox_ai.db.indexes import ensure_indexes
from blackbox_ai.db.mongo import create_client, ping

__all__ = ["create_client", "ensure_indexes", "ping"]
