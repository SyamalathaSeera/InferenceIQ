# API Reference

Interactive OpenAPI documentation is served at `/docs`. When
`INFERENCEIQ_AUTH_REQUIRED=true`, data-plane routes use
`Authorization: Bearer ii_live_...`. Control-plane routes use
`X-InferenceIQ-Admin-Key` and remain protected even when project authentication
is disabled.

## Endpoints

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | Public | Process liveness |
| GET | `/ready` | Public | Database readiness |
| GET | `/metrics` | Infrastructure | Prometheus exposition |
| POST | `/v1/chat/completions` | Project | Completion or SSE stream |
| POST | `/v1/feedback` | Project | Rate/comment on an owned receipt |
| GET | `/v1/projects` | Admin | List projects |
| POST | `/v1/projects` | Admin | Create project |
| POST | `/v1/projects/{id}/api-keys` | Admin | Issue key; secret returned once |
| POST | `/v1/projects/{id}/api-keys/{key_id}/revoke` | Admin | Revoke key |
| GET | `/v1/projects/{id}/usage` | Admin | Monthly requests, tokens, spend, budget |
| GET | `/v1/projects/{id}/inferences` | Admin | Recent receipt summaries |
| GET | `/v1/projects/{id}/audit-events` | Admin | Signed audit events |
| GET | `/v1/projects/{id}/audit-integrity` | Admin | Verify full hash chain |
| GET | `/v1/provider-health` | Admin | Health metrics and circuit states |
| POST | `/v1/provider-health/reset` | Admin | Reset one model circuit |
| POST | `/v1/simulations` | Admin | Replay decisions without provider calls |
| GET | `/v1/policy-workflows` | Admin | List policy versions |
| POST | `/v1/policy-workflows` | Admin | Validate/simulate candidate |
| GET | `/v1/policy-workflows/{id}` | Admin | Read workflow state |
| POST | `/v1/policy-workflows/{id}/review` | Admin | Approve/reject candidate |
| GET | `/v1/policy-workflows/{id}/rollout-metrics` | Admin | Control/canary comparison |

## Automatic inference

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "Explain vector search."}],
  "temperature": 0.2,
  "max_tokens": 400,
  "inferenceiq": {
    "policy": "balanced",
    "max_attempts": 2,
    "require_zero_data_retention": false
  }
}
```

Use provider-prefixed explicit identifiers such as
`anthropic/claude-sonnet-4-6`. Non-streaming responses extend the OpenAI shape
with `provider`, `metadata`, and `inference_receipt`. Streaming uses
`text/event-stream` chunks followed by `[DONE]`.

## Project creation

```json
{
  "name": "portfolio-app",
  "rate_limit_rpm": 60,
  "monthly_budget_usd": 25,
  "pii_policy": "redact"
}
```

Privacy policy is `off`, `detect`, `redact`, or `block`.

## Policy proposal and review

```json
{
  "name": "quality-candidate",
  "policy": "quality",
  "sample_limit": 100,
  "canary_percent": 10,
  "guardrail_min_requests": 20,
  "max_error_rate": 0.1,
  "max_latency_increase_percent": 30,
  "max_cost_increase_percent": 30
}
```

```json
{"approved": true, "reviewed_by": "reviewer-name"}
```

## Common responses

`401` means an invalid project key; `403` means invalid admin access, a disabled
project, or cross-project access; `404` means the resource is unknown; `409`
means a workflow conflict; `429` means the project rate limit was reached; `503`
means an unavailable provider, open explicit-model circuit, or failed readiness.
FastAPI validation errors use `422`.
