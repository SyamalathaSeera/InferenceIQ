# Architecture

## System view

```mermaid
flowchart LR
    Client[Application] --> API[FastAPI API]
    Admin[Operations dashboard] --> API
    API --> Auth[Project auth, rate limit, budget]
    Auth --> Privacy[PII firewall]
    Privacy --> Router[Inference decision engine]
    Router --> Health[Health and circuit state]
    Router --> Exec[Bounded executor]
    Exec --> OpenAI[OpenAI]
    Exec --> Anthropic[Anthropic via LangChain]
    Exec --> Ollama[Ollama via LangChain]
    Exec --> Mock[Mock provider]
    Exec --> DB[(PostgreSQL / SQLite)]
    DB --> Sim[Policy simulator]
    Sim --> Graph[LangGraph policy workflow]
    Graph --> Rollout[Canary and rollback]
    API --> Metrics[Prometheus and JSON logs]
    API -. metadata only .-> LangSmith[LangSmith]
```

## Non-streaming request sequence

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant G as Access controls
    participant P as Privacy firewall
    participant D as Decision engine
    participant E as Executor
    participant U as Provider
    participant S as Database
    C->>A: POST /v1/chat/completions
    A->>G: authenticate, admit, check budget
    G-->>A: project context
    A->>P: detect/redact/block messages
    P-->>A: safe request + privacy evidence
    A->>D: request + catalog + health
    D-->>A: ranked candidates + receipt
    A->>E: execute candidates, bounded attempts
    E->>U: provider call
    U-->>E: completion or classified error
    E-->>A: response + attempt evidence
    A->>S: metrics, fingerprint, receipt, health
    A-->>C: completion + inference receipt
```

## Decision design

The engine applies hard constraints before weighted scoring. A model with an
open circuit, or without a requested zero-data-retention property, is removed
before scores are compared. Policy-specific weights then rank eligible models by
quality, configured cost, and predicted or observed latency. Degraded health
lowers a score.

Explicit model requests remain deterministic. If the requested model has an
open circuit, the API returns service unavailable instead of silently selecting
a different model.

## Provider boundary

All adapters implement one async completion and streaming contract. Adapters own
SDK translation; the executor owns retry classification and bounded fallback.
This prevents provider-specific messages and exceptions from entering policy
code.

## Persistence

Alembic-managed records cover inference outcomes, feedback, policy versions and
rollouts, projects and hashed API keys, rate admissions and monthly usage,
signed audit events, and provider health. Raw prompts and completions are not
stored. A SHA-256 request fingerprint supports deterministic canary assignment.

## Policy lifecycle

```mermaid
stateDiagram-v2
    [*] --> draft
    draft --> invalid: validation fails
    draft --> pending_approval: simulation succeeds
    pending_approval --> rejected: reviewer rejects
    pending_approval --> canary: reviewer approves
    canary --> rolled_back: guardrail violation
```

LangGraph is used because human review is a durable interrupt in a multi-step
state machine. Policy and checkpoint state survive API restarts. The router
reads the active canary and assigns eligible traffic from the request
fingerprint.

## Reliability and scaling

- Only retryable errors fall back, and `max_attempts` caps upstream work.
- Provider attempts update durable health even when fallback succeeds.
- Circuits transition `closed -> open -> half_open -> closed/open`.
- `/ready` verifies database connectivity; `/health` is process liveness.
- PostgreSQL serializes rate admission per project across API replicas.
- LangSmith failure never fails inference or feedback.

The API is otherwise stateless and can be replicated. PostgreSQL coordinates
projects, admission, usage, health, audits, and policy state. Production should
also use PostgreSQL checkpoints plus TLS, backups, centralized logs, alerting,
and an identity-aware proxy for the admin surface.
