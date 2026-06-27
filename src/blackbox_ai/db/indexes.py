"""Index definitions for the Intent Document collection.

Indexes mirror the access patterns described in the product blueprint: grouping
telemetry by project over time, tracing a single agent session, and attributing
usage per developer (a partial index, since ``developer_id`` is optional).
"""

from __future__ import annotations

from typing import Any

from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.asynchronous.collection import AsyncCollection

__all__ = ["INDEXES", "ensure_indexes"]

INDEXES: list[IndexModel] = [
    # Compound: "telemetry for project X, newest first".
    IndexModel(
        [("project_id", ASCENDING), ("timestamp", DESCENDING)],
        name="project_timestamp",
    ),
    # Single field: reassemble one autonomous run.
    IndexModel([("session_id", ASCENDING)], name="session_id"),
    # Partial: only index documents that actually carry a developer id.
    IndexModel(
        [("developer_id", ASCENDING)],
        name="developer_partial",
        partialFilterExpression={"developer_id": {"$type": "string"}},
    ),
    # Global recency scans / future TTL hook.
    IndexModel([("timestamp", DESCENDING)], name="timestamp"),
]


async def ensure_indexes(collection: AsyncCollection[dict[str, Any]]) -> None:
    """Create all indexes; idempotent and safe to call on every startup."""
    await collection.create_indexes(INDEXES)
