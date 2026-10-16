# Deployment and Operations

## Configuration

Start from `.env.production.example` and inject real values through a secret
manager. Never commit the resulting file.

| Variable group | Production guidance |
| --- | --- |
| `OPENAI_*`, `ANTHROPIC_*` | Keys, model names, and current per-million token prices |
| `OLLAMA_*` | Enable only when the Ollama API is reachable on a private network |
| `DATABASE_URL` | Async `postgresql+asyncpg://...` URL |
| `AUTO_CREATE_SCHEMA` | `false`; use Alembic release migrations |
| `INFERENCEIQ_AUTH_REQUIRED` | `true` |
| `INFERENCEIQ_ADMIN_TOKEN` | Strong random control-plane secret |
| `INFERENCEIQ_AUDIT_SIGNING_KEY` | Strong, unrelated, stable HMAC key |
| `LANGGRAPH_CHECKPOINT_URL` | Synchronous PostgreSQL checkpoint URL |
| `LANGSMITH_*` | Optional metadata-only tracing settings |
| `CIRCUIT_*`, `HEALTH_EWMA_ALPHA` | Failure threshold, cooldown, smoothing |
| `DASHBOARD_ORIGINS` | Comma-separated trusted UI origins |

If a database password is embedded in a URL, percent-encode reserved
characters. Provider prices are configuration because prices change; unknown
prices remain unknown and are reported separately rather than treated as free.

## Docker Compose

The stack runs PostgreSQL, a one-shot migration job, and the non-root API image.
The image builds the React app in a Node stage and copies only static output.

```powershell
Copy-Item .env.production.example .env.production
# Fill .env.production from a secure source.
docker compose --env-file .env.production up --build -d
docker compose ps
docker compose logs -f api
```

Stop with `docker compose --env-file .env.production down`. Add `-v` only when
you intentionally want to delete the PostgreSQL data volume.

## Manual release order

1. Provision PostgreSQL, encryption, backups, and TLS connectivity.
2. Inject application and provider secrets.
3. Run `alembic upgrade head` as a release job.
4. Deploy API replicas with `AUTO_CREATE_SCHEMA=false`.
5. Route traffic only after `/ready` succeeds.
6. Scrape `/metrics`, centralize JSON stdout logs, and configure alerts.
7. Restrict admin routes, metrics, and optionally `/docs` at the proxy layer.

Migration history currently ends at `0007_provider_health`. Back up both the
application and LangGraph checkpoint tables and test restoration.

## Operations

- `/health` proves that the process is alive.
- `/ready` checks database connectivity and should gate traffic.
- `/metrics` exposes Prometheus-format request totals, duration, and in-flight
  request count.
- `X-Request-ID` is accepted or generated and returned for log correlation.
- Access logs exclude query strings and request/response bodies.

Production should add proxy body limits, upstream timeouts, TLS/HSTS, centralized
logs, alert rules, managed backups, and identity-aware protection for the
dashboard. The dashboard is an internal control plane, not a public end-user UI.

## CI/CD

The CI workflow checks Ruff formatting/lint, Pytest, Python compilation, a clean
migration, frontend build/audit, and Docker build. The security workflow runs
Gitleaks and CodeQL. Dependabot tracks Python, npm, Docker, and Actions updates.
Protect the default branch with required checks and review.

## GitHub first push

```powershell
Set-Location C:\Users\Syamalatha\Desktop\InferenceIQ
git init
git add .
git status
git commit -m "Build production-ready InferenceIQ control plane"
git branch -M main
git remote add origin YOUR_GITHUB_REPOSITORY_URL
git push -u origin main
```

Review `git status` before committing. It must not contain `.env`, databases,
private keys, caches, `node_modules`, or generated dashboard assets.

## Release checklist

- Rotate any credential ever pasted into a terminal, issue, or commit.
- Confirm ignored secret and key files do not appear in staging.
- Use non-default, unrelated admin and audit secrets.
- Verify current provider prices and budget behavior.
- Run backend, frontend, migration, audit, and container quality gates.
- Test PII, revocation, rate limit, circuit, and rollback behavior in staging.
- Back up the database and verify restoration.
