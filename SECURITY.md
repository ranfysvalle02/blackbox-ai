# Security Policy

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue for
anything exploitable. Use GitHub's
[private vulnerability reporting](https://github.com/ranfysvalle02/ghosts-in-the-code/security/advisories/new)
("Report a vulnerability" under the **Security** tab). Include a description,
affected version/commit, and reproduction steps or a proof of concept.

We aim to acknowledge reports within **72 hours** and to agree on a disclosure
timeline with you before any public write-up. Please give us a reasonable window
to ship a fix before disclosing publicly. We're happy to credit reporters.

## Scope

This gateway sits on the critical path between your apps and model providers and
handles sensitive prompt/response data, so we take its posture seriously. In
scope: the relay, telemetry pipeline, authentication, encryption wiring, and
secret handling. Out of scope: vulnerabilities in upstream providers, MongoDB
itself, or third-party dependencies (report those to their maintainers).

## Security posture

The project is built to fail safe by default:

- **Queryable Encryption** — crown-jewel fields (`raw_payload`, model `content`
  and `chain_of_thought`) are encrypted **client-side, by the driver**, before
  they ever reach MongoDB. The database only ever stores ciphertext for them.
- **Fail-closed encryption** — if encryption is enabled but the key or the
  `crypt_shared` library is missing, the gateway **refuses to start** rather than
  silently writing plaintext.
- **Fail-open data plane** — telemetry/DB failures never break a user's request;
  the recorder can never crash the relay.
- **Secrets are masked** — credentials are wrapped in Pydantic `SecretStr` so
  they don't leak into logs or tracebacks.
- **Constant-time auth** — client tokens are compared with `hmac.compare_digest`
  to avoid timing side channels.
- **Secure-by-default deployment** — `GATEWAY_ENV=production` enforces client
  auth and runs a startup self-check that refuses to boot an open relay.

Please **do not** include real secrets, customer data, or live API keys in any
report or attached payloads.
