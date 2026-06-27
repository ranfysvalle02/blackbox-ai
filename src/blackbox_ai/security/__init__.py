"""Security primitives: MongoDB Queryable Encryption support."""

from __future__ import annotations

from blackbox_ai.security.encryption import EncryptionManager, generate_local_key

__all__ = ["EncryptionManager", "generate_local_key"]
