# Contributing

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
Set-Location dashboard
npm ci
Set-Location ..
```

Use the mock provider for tests. Never put real keys in source, fixtures,
screenshots, issues, or commits.

## Quality gate

```powershell
ruff format --check app tests migrations
ruff check app tests migrations
pytest
python -m compileall -q app migrations tests
Set-Location dashboard
npm run build
npm audit --audit-level=high
```

Add tests for behavior changes. Create a new Alembic revision for schema changes;
never rewrite an applied migration. Keep provider types inside adapters and raw
prompts/completions out of storage and logs. Pull requests should describe user
behavior, design tradeoffs, verification, migration/configuration impact, and
security/privacy impact.
