"""MongoDB Queryable Encryption (QE) wiring.

When ``GATEWAY_ENCRYPTION_ENABLED`` is set, the crown-jewel fields are encrypted
client-side *before* they ever reach the database, using automatic Queryable
Encryption. The gateway holds the keys; MongoDB only ever sees ciphertext for
those fields.

Design choices
--------------
* **Local KMS** - a single 96-byte base64 master key (``GATEWAY_ENCRYPTION_KEY``)
  wraps the per-field Data Encryption Keys (DEKs). Perfect for self-hosting and
  docker-compose; production should swap in AWS/Azure/GCP/KMIP KMS (the only
  change is :meth:`kms_providers`/``master_key`` - see the README).
* **Per-field random encryption** - ``raw_payload`` and the two free-text intent
  fields are encrypted with no ``queries`` clause (query type ``none``). They are
  never filtered on, so they stay maximally protected.
* **Fail-closed** - if encryption is enabled but the key is invalid or the
  ``crypt_shared`` library is missing, construction raises and startup aborts.
  We never silently fall back to writing plaintext.
* **crypt_shared required** - we set ``crypt_shared_lib_required=True`` so the
  driver refuses to spawn the legacy ``mongocryptd`` process; the shared library
  must be present (it is baked into the Docker image).

The encrypted collections are created once via
:meth:`EncryptionManager.ensure_encrypted_collections`, which also bootstraps the
DEKs. Subsequent connections auto-discover the ``encryptedFields`` schema from
the server, so the auto-encrypting client needs no explicit field map.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bson.binary import UuidRepresentation
from bson.codec_options import CodecOptions
from pymongo.asynchronous.encryption import AsyncClientEncryption
from pymongo.encryption_options import AutoEncryptionOpts
from pymongo.errors import EncryptedCollectionError, OperationFailure

from blackbox_ai.config import LOCAL_MASTER_KEY_BYTES, Settings
from blackbox_ai.db.mongo import create_client
from blackbox_ai.logging import get_logger

if TYPE_CHECKING:
    from pymongo import AsyncMongoClient

__all__ = ["EncryptionManager", "generate_local_key"]

_log = get_logger("blackbox_ai.encryption")

_LOCAL_KMS = "local"
# MongoDB server error code for "collection already exists".
_NAMESPACE_EXISTS = 48

# Encrypted (path -> bsonType) for each managed collection. All are unqueryable
# (query type ``none``); ``keyId`` is omitted so create_encrypted_collection
# mints a dedicated DEK per field.
_INTENT_ENCRYPTED_FIELDS: tuple[tuple[str, str], ...] = (
    ("raw_payload", "object"),
    ("intent_telemetry.content", "string"),
    ("intent_telemetry.chain_of_thought", "string"),
)
_CACHE_ENCRYPTED_FIELDS: tuple[tuple[str, str], ...] = (
    ("response_body", "binData"),
    ("request_payload", "object"),
)


def generate_local_key() -> str:
    """Return a fresh base64-encoded 96-byte local KMS master key."""
    return base64.b64encode(os.urandom(LOCAL_MASTER_KEY_BYTES)).decode("ascii")


def _fields_schema(fields: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    return {"fields": [{"path": path, "bsonType": bson_type} for path, bson_type in fields]}


class EncryptionManager:
    """Owns QE configuration: key material, schemas, and client construction.

    Constructing the manager validates the master key (and the ``crypt_shared``
    path, if set) eagerly, so a misconfiguration surfaces immediately at startup
    rather than on the first write.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Raises ValueError on a missing/malformed/wrong-length key (fail-closed).
        self._master_key = settings.decode_encryption_key()
        self._kms_providers: dict[str, Any] = {_LOCAL_KMS: {"key": self._master_key}}
        self._validate_crypt_shared()

    def _validate_crypt_shared(self) -> None:
        path = self._settings.crypt_shared_lib_path
        if path and not Path(path).is_file():
            raise ValueError(
                f"GATEWAY_CRYPT_SHARED_LIB_PATH points to a missing file: {path!r}. "
                "Automatic Queryable Encryption needs the MongoDB crypt_shared library."
            )

    @property
    def kms_providers(self) -> dict[str, Any]:
        return self._kms_providers

    @property
    def key_vault_namespace(self) -> str:
        return self._settings.encryption_key_vault_ns

    def encrypted_fields_for(self, collection_name: str) -> dict[str, Any]:
        """Return the ``encryptedFields`` schema for a managed collection."""
        if collection_name == self._settings.mongo_collection:
            return _fields_schema(_INTENT_ENCRYPTED_FIELDS)
        if collection_name == self._settings.cache_collection:
            return _fields_schema(_CACHE_ENCRYPTED_FIELDS)
        raise ValueError(f"No encryption schema for collection {collection_name!r}.")

    @property
    def intent_encrypted_paths(self) -> tuple[str, ...]:
        """Dotted paths of encrypted fields on the intents collection.

        Automatic QE refuses to encrypt a ``null`` value, so these paths must be
        dropped from a document when their value is ``None`` before insertion.
        """
        return tuple(path for path, _ in _INTENT_ENCRYPTED_FIELDS)

    def auto_encryption_opts(self) -> AutoEncryptionOpts:
        """Build the auto-encryption options for the data-plane client.

        ``crypt_shared_lib_required=True`` enforces the fail-closed posture: the
        driver will not fall back to spawning ``mongocryptd``.
        """
        return AutoEncryptionOpts(
            kms_providers=self._kms_providers,
            key_vault_namespace=self.key_vault_namespace,
            crypt_shared_lib_path=self._settings.crypt_shared_lib_path,
            crypt_shared_lib_required=True,
        )

    def build_encrypting_client(self) -> AsyncMongoClient[dict[str, Any]]:
        """Create a pooled client that transparently encrypts/decrypts QE fields."""
        return create_client(self._settings, auto_encryption_opts=self.auto_encryption_opts())

    async def ensure_encrypted_collections(
        self, key_vault_client: AsyncMongoClient[dict[str, Any]]
    ) -> None:
        """Bootstrap DEKs and the encrypted collections (idempotent).

        Uses a plaintext ``key_vault_client`` plus :class:`AsyncClientEncryption`
        to create each encrypted collection along with its per-field DEKs. Safe
        to call on every startup: already-created collections are left untouched.
        """
        codec_options: CodecOptions[dict[str, Any]] = CodecOptions(
            uuid_representation=UuidRepresentation.STANDARD
        )
        await self._ensure_key_vault_index(key_vault_client)

        client_encryption: AsyncClientEncryption[dict[str, Any]] = AsyncClientEncryption(
            kms_providers=self._kms_providers,
            key_vault_namespace=self.key_vault_namespace,
            key_vault_client=key_vault_client,
            codec_options=codec_options,
        )
        try:
            database = key_vault_client[self._settings.mongo_db]
            for collection_name in (
                self._settings.mongo_collection,
                self._settings.cache_collection,
            ):
                await self._ensure_one_collection(client_encryption, database, collection_name)
        finally:
            await client_encryption.close()

    async def _ensure_key_vault_index(
        self, key_vault_client: AsyncMongoClient[dict[str, Any]]
    ) -> None:
        db_name, _, coll_name = self.key_vault_namespace.partition(".")
        key_vault = key_vault_client[db_name][coll_name]
        # Recommended unique partial index guarding key alt names.
        await key_vault.create_index(
            "keyAltNames",
            name="keyAltNames_unique",
            unique=True,
            partialFilterExpression={"keyAltNames": {"$exists": True}},
        )

    async def _ensure_one_collection(
        self,
        client_encryption: AsyncClientEncryption[dict[str, Any]],
        database: Any,
        collection_name: str,
    ) -> None:
        existing = await database.list_collection_names(filter={"name": collection_name})
        if existing:
            _log.info("encrypted_collection_exists", collection=collection_name)
            return
        encrypted_fields = self.encrypted_fields_for(collection_name)
        try:
            await client_encryption.create_encrypted_collection(
                database,
                collection_name,
                encrypted_fields,
                kms_provider=_LOCAL_KMS,
            )
        except EncryptedCollectionError as exc:
            # A concurrent starter may have won the race; treat "already exists"
            # as success and re-raise anything else.
            cause = exc.__cause__
            if isinstance(cause, OperationFailure) and cause.code == _NAMESPACE_EXISTS:
                _log.info("encrypted_collection_exists", collection=collection_name)
                return
            raise
        _log.info("encrypted_collection_created", collection=collection_name)
