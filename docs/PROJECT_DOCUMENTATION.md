# InferenceIQ: Complete Project Documentation

## 1. Product summary

InferenceIQ is an AI inference optimization and governance platform. An
application sends a familiar OpenAI-compatible request; InferenceIQ evaluates
the available models, applies project and privacy rules, selects a model,
executes with bounded fallback, and returns both the model response and an
explainable inference receipt.

The differentiator is not access to many providers. It is the closed feedback
loop around every inference:

1. Decide using explicit cost, quality, latency, privacy, and health
   evidence.
2. Execute with retry classification, fallback, and circuit protection.
3. Observe actual tokens, cost, latency, failures, and user feedback.
4. Simulate a proposed policy against stored decision evidence.
5. Promote it through human approval, deterministic canary traffic, guardrails,
   and automatic rollback.

This narrowed scope is deliberately deeper than a broad gateway clone. It is a
portfolio-ready demonstration of API design, distributed controls, model
operations, security, observability, workflow orchestration, testing, and
deployment.

## 2. Main features

### Intelligent routing

- `model: "auto"` activates the decision engine.
- Policies: `balanced`, `cost`, `latency`, and `quality`.
- Hard constraints remove models with open circuits or models that fail a
  requested zero-data-retention requirement.
- Remaining candidates receive normalized, policy-specific scores.
- Health evidence affects scoring; open circuits are excluded.
- Explicit model selection remains available when deterministic control matters.

### Resilient provider execution

- Provider adapters isolate upstream SDK behavior.
- OpenAI uses its native async client.
- Anthropic and Ollama use LangChain chat-model adapters.
- Mock models make development and CI deterministic and credential-free.
- Only retryable failures trigger fallback, and `max_attempts` bounds fan-out.
- Every attempt records provider, model, status, error class, and latency.

### Provider health and circuit breakers

- Success/failure and latency exponential moving averages are persisted.
- Repeated failures open a model circuit.
- The circuit becomes half-open after a configurable cooldown.
- A successful probe closes it; a failed probe reopens it.
- Operators can inspect health and manually reset a circuit.

### Explainable inference receipts

Every non-streaming result includes a receipt containing the requested and
selected model, provider, policy, selection reason, rejected constraints,
candidate scores, health evidence, fallback attempts, predicted metrics,
actual metrics, privacy action, and rollout assignment. Stored records contain
a SHA-256 request fingerprint rather than raw prompt text.

### Multi-tenant controls

- Admin-managed projects and API keys.
- API key secrets are shown once; only a prefix and SHA-256 hash are stored.
- PostgreSQL admission locking makes per-project request limits consistent across
  API replicas.
- Monthly usage includes requests, tokens, known spend, unknown-cost requests,
  and remaining budget.
- Projects can be disabled and keys revoked.

### Privacy and auditability

- Project modes: `off`, `detect`, `redact`, or `block`.
- Detection covers email, phone, US SSN, and Luhn-valid payment cards.
- Redaction occurs before any provider call.
- Matched values and raw prompts are excluded from receipts, persistence, logs,
  and audit details.
- Control-plane and privacy events form a per-project HMAC-SHA256 hash chain.
- An integrity endpoint recomputes the chain and identifies its first invalid
  sequence.

### Safe policy improvement

- The simulator replays stored automatic decisions without calling providers.
- LangGraph coordinates proposal validation, simulation, a durable human-review
  pause, and rollout state transitions.
- Approved versions receive a deterministic percentage of eligible traffic.
- Control and canary cost, latency, and error metrics are compared continuously.
- A violated guardrail moves the version to `rolled_back` and removes it from
  future traffic automatically.
- Only one active canary is allowed in this focused version.

### Evaluation and observability

- A deterministic offline evaluation CLI scores correctness, latency, and cost.
- LangSmith integration is optional and metadata-only; prompts and outputs are
  not traced by InferenceIQ.
- Prometheus request metrics, readiness checks, request IDs, structured JSON
  access logs, and defensive HTTP headers are built in.
- A React/TypeScript dashboard displays projects, usage, health, rollouts,
  receipts, and audit integrity.

## 3. Why LangChain and LangGraph are used

The frameworks are used only where they add clear value:

- **LangChain** standardizes the Anthropic and Ollama chat integrations and
  supports the evaluation ecosystem.
- **LangGraph** models the long-running policy lifecycle because validation,
  simulation, human approval, canary release, and rollback are stateful steps
  that must survive restarts.
- **LangSmith** is an optional observability/evaluation destination, never the
  source of truth and never a dependency of the serving path.

The latency-sensitive decision engine is ordinary typed Python. This keeps the
core algorithm understandable, testable, and independent of framework churn.

## 4. Technology stack

| Layer | Technology | Responsibility |
| --- | --- | --- |
| API | Python 3.11+, FastAPI, Pydantic | Async HTTP API and validation |
| Persistence | SQLAlchemy async, Alembic | SQLite development and PostgreSQL production |
| Providers | OpenAI SDK, LangChain Anthropic/Ollama adapters | Provider-independent execution |
| Workflow | LangGraph | Durable policy review and promotion |
| Evaluation | Custom evaluators, optional LangSmith | Offline tests and experiments |
| Dashboard | React, TypeScript, Vite | Internal operations UI |
| Operations | Prometheus client, JSON logging | Metrics, health, and diagnostics |
| Delivery | Docker, Docker Compose, GitHub Actions | Reproducible build and CI/security checks |
| Testing | Pytest, HTTPX, Ruff | Behavioral tests, formatting, and linting |

## 5. Repository map

```text
InferenceIQ/
├── app/
│   ├── api/             HTTP routes
│   ├── evaluation/      evaluators, CLI, LangSmith bridge
│   ├── inference/       catalog, decision engine, executor, simulator
│   ├── persistence/     SQLAlchemy records and repositories
│   ├── policy/          LangGraph workflow, checkpoints, rollout logic
│   ├── providers/       mock, OpenAI, Anthropic, Ollama adapters
│   ├── schemas/         public request/response contracts
│   ├── access.py        authentication, limits, budgets, project access
│   ├── audit.py         signed append-only audit chain
│   ├── health.py        EWMA health and circuit state machine
│   ├── privacy.py       PII detection and enforcement
│   └── operations.py    logs, metrics, readiness, security headers
├── dashboard/           React/TypeScript source
├── evaluations/         safe deterministic evaluation datasets
├── migrations/          Alembic schema history, currently through 0007
├── tests/               unit and API integration tests
├── docs/                project, architecture, API, deployment, security docs
├── .github/             CI, CodeQL, Gitleaks, and Dependabot configuration
├── compose.yaml
└── Dockerfile
```

## 6. Local development

Prerequisites are Python 3.11 or newer and Node.js 20 or newer. Docker is
optional for the local mock path.

```powershell
git clone YOUR_REPOSITORY_URL
Set-Location InferenceIQ
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

The default `PROVIDER=mock` configuration requires no keys. Build the embedded
dashboard once after cloning:

```powershell
Set-Location dashboard
npm ci
npm run build
Set-Location ..
```

Restart the API and visit <http://127.0.0.1:8000/dashboard>. The Vite development
server can instead be run with `npm run dev`; its origins are controlled by
`DASHBOARD_ORIGINS`.

## 7. Typical production demonstration

1. Start the stack with PostgreSQL and authentication enabled.
2. Create a project and one API key using the admin control plane.
3. Send several `model: "auto"` calls under different policies.
4. Show receipts explaining why different models were selected.
5. Trigger mock-provider failure to demonstrate fallback and an open circuit.
6. Inspect the dashboard, usage totals, and audit-chain verification.
7. Create a new policy proposal; explain the LangGraph pause.
8. Approve it, generate control/canary traffic, then show either healthy metrics
   or automatic rollback after a guardrail violation.
9. Run the offline evaluator and optionally publish safe metadata to LangSmith.

## 8. Configuration principles

- Copy an example env file; never rename it over or commit a real `.env`.
- Configure provider prices explicitly. Unknown prices remain `null` and are
  counted separately rather than being treated as free.
- Use PostgreSQL, migrations, authentication, strong unrelated secrets, and TLS
  in production.
- Keep `AUTO_CREATE_SCHEMA=false` in production.
- Keep the audit signing key stable. This version does not attach historical key
  identifiers, so transparent signing-key rotation is not yet supported.
- Treat the dashboard as an internal administrative application.

All variables are described in [Deployment](DEPLOYMENT.md).

## 9. Verification and quality gates

Run the complete local gate before committing:

```powershell
ruff format --check app tests migrations
ruff check app tests migrations
pytest
python -m compileall -q app migrations tests

Set-Location dashboard
npm ci
npm run build
npm audit --audit-level=high
```

CI repeats backend linting, tests, a clean migration, frontend compilation and
audit, and the Docker build. A separate workflow runs Gitleaks and CodeQL.
Dependabot tracks Python, npm, Docker, and GitHub Actions dependencies.

## 10. Intentional boundaries and next extensions

The project is production-oriented, but it does not claim to be a hosted
multi-region SaaS. Current boundaries are explicit:

- Text chat only; tools, images, embeddings, and audio need typed provider
  contracts before being advertised as supported.
- Streaming is supported, but streaming calls are excluded from canary outcome
  comparison and final spend accounting because normalized terminal usage is not
  yet available across every adapter.
- PII detection is deterministic pattern matching, not a replacement for a
  jurisdiction-specific data-loss-prevention service.
- Provider catalog entries and prices are configuration-backed rather than a
  live pricing feed.
- The internal admin key should be replaced or fronted by identity-aware SSO for
  a real hosted product.
- Audit signing-key rotation and admission-record pruning are future operational
  tasks.

The strongest next extension is normalized streaming usage persistence, followed
by OpenTelemetry traces and a real load-test profile. These deepen the same
product rather than expanding it into an unfocused gateway.

## 11. Resume-ready description

> Built InferenceIQ, a production-oriented AI inference control plane that
> routes requests across OpenAI, Anthropic, and local models using explainable
> cost/quality/latency policies and privacy constraints. Implemented circuit breakers, bounded
> fallback, multi-tenant budgets and rate limits, PII redaction, tamper-evident
> audits, offline policy simulation, and LangGraph-based human-approved canary
> rollouts with automatic rollback. Shipped an async FastAPI/PostgreSQL backend,
> React operations dashboard, Prometheus observability, migrations, automated
> tests, container deployment, and CI security scanning.
