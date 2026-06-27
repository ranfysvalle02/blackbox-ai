# 2. Queryable Encryption fails closed at startup

- Status: Accepted
- Date: 2026-06-26

## Context

When Queryable Encryption (QE) is enabled, the crown-jewel fields - `raw_payload`
and the model's `content` / `chain_of_thought` - are encrypted client-side
before they reach MongoDB. The whole point is that the database never sees
plaintext. A misconfiguration (missing/invalid master key, absent `crypt_shared`
library) could silently disable encryption and write plaintext instead. That is
the one failure mode that must never happen quietly: it is worse than an outage,
because it is invisible.

## Decision

Encryption is **fail-closed**. The rest of the gateway is fail-open (it starts
even if MongoDB is unreachable), but QE is the deliberate exception:

- `crypt_shared_lib_required=True` is set on the auto-encryption options, so the
  driver refuses to operate without the encryption library.
- `EncryptionManager` validates the base64 96-byte local master key at
  construction and raises `ValueError` on any problem.
- The lifespan in [main.py](../../src/blackbox_ai/main.py) lets that error
  abort startup rather than catching it.

See [security/encryption.py](../../src/blackbox_ai/security/encryption.py) and
[bootstrap.py](../../src/blackbox_ai/bootstrap.py).

## Consequences

- If QE is enabled and misconfigured, the gateway refuses to start. This is
  intended: no plaintext is ever written under the belief it was encrypted.
- QE rejects `null` for encrypted fields (error 31041). The sink prunes
  null-valued encrypted paths before insert
  ([sink_mongo.py](../../src/blackbox_ai/telemetry/sink_mongo.py)); see also
  the cache store, which omits a null `request_payload`.
- Operators must provision `crypt_shared` (baked into the Docker image) and a
  real KMS in production instead of the local dev key.
