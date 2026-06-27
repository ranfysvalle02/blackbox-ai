"""Application configuration via environment variables / ``.env``.

All settings are validated by pydantic-settings. Provider credentials use their
conventional environment variable names (``OPENAI_API_KEY`` etc.) so existing
secrets can be reused, while gateway-specific knobs use a ``GATEWAY_`` prefix to
avoid collisions.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property, lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "DeploymentEnv",
    "EmbeddingsProvider",
    "RuntimeSecurityReport",
    "Settings",
    "get_settings",
]

# A MongoDB local-KMS master key is exactly 96 bytes.
LOCAL_MASTER_KEY_BYTES = 96


class EmbeddingsProvider(StrEnum):
    """Selectable embedding backends for vector search."""

    NONE = "none"
    VOYAGE = "voyage"


class DeploymentEnv(StrEnum):
    """Runtime posture. ``production`` enforces secure-by-default policies."""

    DEV = "dev"
    PRODUCTION = "production"


@dataclass(frozen=True, slots=True)
class RuntimeSecurityReport:
    """Outcome of a startup security self-check.

    ``fatal`` entries must abort startup (fail-closed); ``warnings`` are logged
    loudly but allow the gateway to run.
    """

    fatal: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.fatal


class Settings(BaseSettings):
    """Strongly-typed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Allow construction by field name (e.g. in tests) in addition to the
        # environment-variable aliases used at runtime.
        populate_by_name=True,
    )

    # --- MongoDB ------------------------------------------------------------
    mongo_uri: str = Field(
        default="mongodb://localhost:27017/?directConnection=true",
        validation_alias="GATEWAY_MONGO_URI",
    )
    mongo_db: str = Field(default="blackbox_ai", validation_alias="GATEWAY_MONGO_DB")
    mongo_collection: str = Field(default="intents", validation_alias="GATEWAY_MONGO_COLLECTION")

    # Connection pool sizing. Rationale: writes to Mongo originate only from a
    # small, bounded pool of telemetry workers plus health pings, so concurrency
    # against the database is low. We keep a modest ceiling with headroom and a
    # couple of pre-warmed connections to avoid cold-start latency on the first
    # flush. Tune `mongo_max_pool_size` up if you raise `telemetry_workers`.
    mongo_max_pool_size: int = Field(default=20, validation_alias="GATEWAY_MONGO_MAX_POOL_SIZE")
    mongo_min_pool_size: int = Field(default=2, validation_alias="GATEWAY_MONGO_MIN_POOL_SIZE")

    # --- Deployment posture -------------------------------------------------
    # `production` makes the gateway secure-by-default: client auth is enforced
    # and startup fails fast on an insecure configuration (see
    # `runtime_security_report`). `dev` stays permissive for local work.
    deployment_env: DeploymentEnv = Field(default=DeploymentEnv.DEV, validation_alias="GATEWAY_ENV")

    # --- Server -------------------------------------------------------------
    host: str = Field(default="0.0.0.0", validation_alias="GATEWAY_HOST")
    port: int = Field(default=8000, validation_alias="GATEWAY_PORT")
    log_level: str = Field(default="INFO", validation_alias="GATEWAY_LOG_LEVEL")
    log_json: bool = Field(default=True, validation_alias="GATEWAY_LOG_JSON")

    # --- Client auth --------------------------------------------------------
    require_auth: bool = Field(default=False, validation_alias="GATEWAY_REQUIRE_AUTH")
    gateway_tokens_raw: SecretStr = Field(default=SecretStr(""), validation_alias="GATEWAY_TOKENS")

    # --- Telemetry plane ----------------------------------------------------
    telemetry_queue_maxsize: int = Field(
        default=10_000, validation_alias="GATEWAY_TELEMETRY_QUEUE_MAXSIZE"
    )
    telemetry_workers: int = Field(default=2, validation_alias="GATEWAY_TELEMETRY_WORKERS")
    telemetry_batch_size: int = Field(default=50, validation_alias="GATEWAY_TELEMETRY_BATCH_SIZE")
    telemetry_flush_interval_s: float = Field(
        default=1.0, validation_alias="GATEWAY_TELEMETRY_FLUSH_INTERVAL_S"
    )
    telemetry_max_capture_bytes: int = Field(
        default=8 * 1024 * 1024, validation_alias="GATEWAY_TELEMETRY_MAX_CAPTURE_BYTES"
    )

    # --- Upstream HTTP ------------------------------------------------------
    http_timeout_s: float = Field(default=300.0, validation_alias="GATEWAY_HTTP_TIMEOUT_S")
    http_connect_timeout_s: float = Field(
        default=10.0, validation_alias="GATEWAY_HTTP_CONNECT_TIMEOUT_S"
    )

    # --- Request limits -----------------------------------------------------
    # The relay must buffer each request body to forward and tee it; this bounds
    # data-plane memory by rejecting oversized bodies with 413 before reading
    # them in full. Defaults to 10 MiB - generous for LLM JSON payloads.
    max_request_bytes: int = Field(
        default=10 * 1024 * 1024, validation_alias="GATEWAY_MAX_REQUEST_BYTES"
    )
    # Hard ceiling on concurrently in-flight relayed requests. Each in-flight
    # request can hold up to max_request_bytes + telemetry_max_capture_bytes in
    # memory, so worst-case data-plane memory is roughly
    # max_concurrent_requests * (max_request_bytes + telemetry_max_capture_bytes).
    # Excess requests are rejected fast with 503 rather than queued.
    max_concurrent_requests: int = Field(
        default=100, validation_alias="GATEWAY_MAX_CONCURRENT_REQUESTS"
    )

    # --- Abuse prevention ---------------------------------------------------
    # Per-client sliding-window rate limit (keyed by gateway token when auth is
    # on, else by client IP). On by default; tune for your traffic shape.
    rate_limit_enabled: bool = Field(default=True, validation_alias="GATEWAY_RATE_LIMIT_ENABLED")
    rate_limit_requests: int = Field(default=120, validation_alias="GATEWAY_RATE_LIMIT_REQUESTS")
    rate_limit_window_s: float = Field(default=60.0, validation_alias="GATEWAY_RATE_LIMIT_WINDOW_S")

    # --- Provider credentials & endpoints -----------------------------------
    # Secrets are SecretStr so they are masked in logs / repr; unwrap with
    # .get_secret_value() only at the point of use (provider catalog, embedder).
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_base_url: str = Field(
        default="https://api.openai.com", validation_alias="OPENAI_BASE_URL"
    )

    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com", validation_alias="ANTHROPIC_BASE_URL"
    )

    gemini_api_key: SecretStr | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_base_url: str = Field(
        default="https://generativelanguage.googleapis.com",
        validation_alias="GEMINI_BASE_URL",
    )

    azure_openai_api_key: SecretStr | None = Field(
        default=None, validation_alias="AZURE_OPENAI_API_KEY"
    )
    azure_openai_endpoint: str | None = Field(
        default=None, validation_alias="AZURE_OPENAI_ENDPOINT"
    )

    ollama_base_url: str = Field(
        default="http://localhost:11434", validation_alias="OLLAMA_BASE_URL"
    )

    # --- Queryable Encryption (Phase 4) -------------------------------------
    # On by default (secure-by-default): the crown-jewel fields are encrypted
    # client-side via MongoDB Queryable Encryption before they reach the
    # database. Requires a key + crypt_shared + a QE-capable server (Atlas /
    # Enterprise); startup is fail-closed if those are missing. Set to false for
    # a plain-MongoDB dev box.
    encryption_enabled: bool = Field(default=True, validation_alias="GATEWAY_ENCRYPTION_ENABLED")
    # Base64-encoded 96-byte local KMS master key (see `blackbox-ai gen-key`).
    encryption_key: SecretStr | None = Field(
        default=None, validation_alias="GATEWAY_ENCRYPTION_KEY"
    )
    encryption_key_vault_ns: str = Field(
        default="blackbox_ai.__keyvault",
        validation_alias="GATEWAY_ENCRYPTION_KEY_VAULT_NS",
    )
    encryption_dek_name: str = Field(
        default="blackbox-ai-dek", validation_alias="GATEWAY_ENCRYPTION_DEK_NAME"
    )
    # Path to the MongoDB `crypt_shared` library required for automatic QE.
    # Baked into the Docker image; set explicitly for local (non-Docker) runs.
    crypt_shared_lib_path: str | None = Field(
        default=None, validation_alias="GATEWAY_CRYPT_SHARED_LIB_PATH"
    )

    # --- Embeddings / Vector Search (Phase 4) -------------------------------
    # Voyage AI by default (a MongoDB company); `voyage-code-3` is tuned for
    # code/agent intent. Embeddings only activate once VOYAGE_API_KEY is set -
    # without a key the gateway runs with vector search disabled (fail-open).
    embeddings_provider: EmbeddingsProvider = Field(
        default=EmbeddingsProvider.VOYAGE, validation_alias="GATEWAY_EMBEDDINGS_PROVIDER"
    )
    voyage_api_key: SecretStr | None = Field(default=None, validation_alias="VOYAGE_API_KEY")
    embedding_model: str = Field(
        default="voyage-code-3", validation_alias="GATEWAY_EMBEDDING_MODEL"
    )
    embedding_dims: int = Field(default=1024, validation_alias="GATEWAY_EMBEDDING_DIMS")
    # Circuit breaker around the embedding provider: after N consecutive batch
    # failures, stop calling it for a cooldown window (stays fail-open).
    embedding_breaker_threshold: int = Field(
        default=5, validation_alias="GATEWAY_EMBEDDING_BREAKER_THRESHOLD"
    )
    embedding_breaker_cooldown_s: float = Field(
        default=30.0, validation_alias="GATEWAY_EMBEDDING_BREAKER_COOLDOWN_S"
    )
    vector_index_name: str = Field(
        default="intent_vector_index", validation_alias="GATEWAY_VECTOR_INDEX_NAME"
    )
    # Atlas Search (full-text) index powering hybrid search via $rankFusion.
    search_index_name: str = Field(
        default="intent_text_index", validation_alias="GATEWAY_SEARCH_INDEX_NAME"
    )

    # --- Token cache (Phase 4) ----------------------------------------------
    cache_enabled: bool = Field(default=False, validation_alias="GATEWAY_CACHE_ENABLED")
    # When False (default) caching applies only to requests that opt in via the
    # `X-Intent-Cache` header; when True it applies to every cacheable request.
    cache_default_on: bool = Field(default=False, validation_alias="GATEWAY_CACHE_DEFAULT_ON")
    cache_ttl_s: int = Field(default=3600, validation_alias="GATEWAY_CACHE_TTL_S")
    cache_collection: str = Field(default="cache", validation_alias="GATEWAY_CACHE_COLLECTION")
    # Max time the request-path cache lookup may take before we give up and
    # forward upstream (fail-open). Keeps the data plane snappy.
    cache_lookup_timeout_s: float = Field(
        default=0.25, validation_alias="GATEWAY_CACHE_LOOKUP_TIMEOUT_S"
    )

    # --- Admin / search API -------------------------------------------------
    admin_token: SecretStr | None = Field(default=None, validation_alias="GATEWAY_ADMIN_TOKEN")
    # /metrics is open by default (scrape-friendly). Set true to require the
    # admin token; the route then fails closed if no admin token is configured.
    metrics_protected: bool = Field(default=False, validation_alias="GATEWAY_METRICS_PROTECTED")

    @cached_property
    def gateway_tokens(self) -> frozenset[str]:
        """Parse the comma-separated accepted-token list into a set."""
        raw = self.gateway_tokens_raw.get_secret_value()
        return frozenset(t.strip() for t in raw.split(",") if t.strip())

    @property
    def is_production(self) -> bool:
        return self.deployment_env is DeploymentEnv.PRODUCTION

    @property
    def effective_require_auth(self) -> bool:
        """Auth is always enforced in production, regardless of require_auth."""
        return self.require_auth or self.is_production

    def runtime_security_report(self) -> RuntimeSecurityReport:
        """Self-check the runtime posture; callers fail-closed on ``fatal``."""
        fatal: list[str] = []
        warnings: list[str] = []
        if self.is_production:
            if not self.gateway_tokens:
                fatal.append(
                    "GATEWAY_ENV=production requires GATEWAY_TOKENS to be set; "
                    "refusing to run an open relay."
                )
            if not self.encryption_enabled:
                warnings.append(
                    "Production without Queryable Encryption "
                    "(GATEWAY_ENCRYPTION_ENABLED=false): intent text is stored as plaintext."
                )
            if not self.rate_limit_enabled:
                warnings.append(
                    "Production with rate limiting disabled (GATEWAY_RATE_LIMIT_ENABLED=false)."
                )
        elif not self.effective_require_auth:
            warnings.append(
                "Auth is disabled: the relay is open to anyone who can reach it. Set "
                "GATEWAY_REQUIRE_AUTH=true (or GATEWAY_ENV=production) before exposing it."
            )
        return RuntimeSecurityReport(fatal=tuple(fatal), warnings=tuple(warnings))

    def decode_encryption_key(self) -> bytes:
        """Decode and validate the base64 local master key.

        Raises:
            ValueError: if the key is missing, malformed, or not 96 bytes.
        """
        if self.encryption_key is None or not self.encryption_key.get_secret_value():
            raise ValueError(
                "GATEWAY_ENCRYPTION_KEY is required when encryption is enabled. "
                "Generate one with `blackbox-ai gen-key`."
            )
        try:
            raw = base64.b64decode(self.encryption_key.get_secret_value(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("GATEWAY_ENCRYPTION_KEY is not valid base64.") from exc
        if len(raw) != LOCAL_MASTER_KEY_BYTES:
            raise ValueError(
                f"GATEWAY_ENCRYPTION_KEY must decode to {LOCAL_MASTER_KEY_BYTES} bytes, "
                f"got {len(raw)}."
            )
        return raw


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance (constructed once per process)."""
    return Settings()
