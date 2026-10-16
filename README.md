# InferenceIQ

InferenceIQ is a production-oriented AI inference control plane. It chooses the
best model for each request, explains the decision, measures the outcome, and
safely improves routing policies over time.

Unlike a basic model gateway, the product is centered on **inference
intelligence**: policy-aware model selection, explainable inference receipts,
privacy enforcement, provider health, offline simulation, and guarded policy
rollouts.

## Highlights

- OpenAI-compatible chat completions with streaming
- OpenAI, Anthropic, Ollama, and deterministic mock providers
- Cost, latency, quality, and balanced routing policies with privacy constraints
- Bounded fallback with provider health and circuit breakers
- Explainable receipts with scores, constraints, attempts, and actual metrics
- Projects, hashed API keys, shared rate limits, and monthly budgets
- PII detect/redact/block modes and tamper-evident audit chains
- Policy simulation plus LangGraph approval, canary, and auto-rollback workflow
- Optional metadata-only LangSmith traces and offline evaluation
- React operations dashboard, Prometheus metrics, readiness checks, and JSON logs

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

The default mock provider needs no external credentials. Open:

- API documentation: <http://127.0.0.1:8000/docs>
- Dashboard: <http://127.0.0.1:8000/dashboard>
- Readiness: <http://127.0.0.1:8000/ready>
- Metrics: <http://127.0.0.1:8000/metrics>

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/v1/chat/completions `
  -ContentType "application/json" `
  -Body '{"model":"auto","messages":[{"role":"user","content":"Explain caching"}],"inferenceiq":{"policy":"balanced"}}'
```

For the dashboard development server:

```powershell
Set-Location dashboard
npm ci
npm run dev
```

## Documentation

- [Complete project guide](docs/PROJECT_DOCUMENTATION.md)
- [Architecture and request flows](docs/ARCHITECTURE.md)
- [API reference](docs/API_REFERENCE.md)
- [Deployment and operations](docs/DEPLOYMENT.md)
- [Security model](docs/SECURITY.md)
- [Learning and portfolio guide](docs/LEARNING_GUIDE.md)

## Verification

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

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and
[SECURITY.md](SECURITY.md) for vulnerability reporting. Licensed under MIT.
