# Security Model

InferenceIQ accepts potentially sensitive prompts and calls external model
providers. Its security goals are to protect credentials, isolate projects,
enforce privacy before egress, bound upstream work, and make administrative
changes tamper-evident.

## Controls

- Project keys protect inference and feedback when authentication is enabled.
- Generated key material is returned once; only a display prefix and SHA-256
  digest are stored. SHA-256 is suitable for high-entropy generated keys, unlike
  human passwords, which need a slow password hash.
- Feedback verifies receipt ownership to prevent cross-project access.
- An independent admin key protects projects, health, simulations, audits, and
  policy workflows.
- PII detection/redaction/blocking occurs before provider execution.
- Raw prompts, completions, and matched PII values are excluded from persistence,
  access logs, receipts, and audit details.
- Bounded fallback prevents retry amplification; project limits and budgets bound
  consumption; circuits suppress unhealthy upstreams.

## Audit integrity

Audit events are sequenced per project. Each HMAC includes the prior hash, stable
event fields, and sanitized details. Verification detects database-only
tampering while the signing key remains secret. This is tamper-evident rather
than immutable storage; stronger deployments can export chain heads to external
write-once storage.

Keep the signing key stable. This version does not yet store historical signing
key identifiers for transparent rotation.

## Secrets and browser security

Runtime credentials belong in environment injection or a secret manager.
Secret env files, credentials, private keys, keystores, databases, logs, caches,
Terraform state, and build artifacts are ignored. Example env files contain
placeholders only. LangSmith is disabled by default and receives metadata only.

The admin dashboard stores its token in browser `sessionStorage`. It still
requires HTTPS, careful XSS prevention, a trusted origin list, and preferably an
identity-aware reverse proxy or private network.

## Production checklist

- Enable authentication and replace every default secret.
- Protect the admin surface, `/metrics`, and optionally `/docs` from public access.
- Use PostgreSQL TLS, least-privilege roles, encrypted backups, and restore tests.
- Configure proxy HSTS, trusted hosts, body limits, connection limits, and
  timeouts.
- Verify provider zero-data-retention claims contractually.
- Define retention/deletion policies and use specialized DLP for regulated data.
- Alert on authorization failures, open circuits, audit-integrity failures,
  budget exhaustion, and cost anomalies.
- Run Gitleaks, CodeQL, dependency audits, and an external review before handling
  regulated or high-impact workloads.

Report vulnerabilities privately using the root [security policy](../SECURITY.md).
