# Learning and Portfolio Guide

The project is most valuable when you can explain its decisions. Study it in
this order and make one small change in each area.

1. **Provider abstraction:** read `app/providers/base.py`, then mock and real
   adapters. Explain why SDK exceptions and message translation stay here.
2. **Decision engine:** read `app/inference/models.py`, `catalog.py`, and
   `engine.py`. Separate hard constraints from weighted preferences and trace
   their evidence into a receipt.
3. **Reliability:** read `executor.py` and `health.py`. Understand retryable
   errors, bounded attempts, EWMA smoothing, and circuit transitions.
4. **Persistence:** read models, repositories, and migrations in order. Compare
   SQLite development behavior with PostgreSQL admission locking.
5. **Security:** trace authentication, PII handling, provider egress,
   persistence, and audit signing. Verify why raw prompts are absent.
6. **LangChain/LangGraph/LangSmith:** LangChain adapts providers, LangGraph
   controls a durable human-reviewed process, and LangSmith is optional
   metadata-only observability rather than production state.
7. **Operations:** inspect the React dashboard and `app/operations.py`. Connect
   admin APIs, request IDs, logs, readiness, and Prometheus metrics.
8. **Delivery:** map tests to risks, follow the Docker stages, and explain why
   migrations are a separate release step.

## Suggested exercises

- Add a mock catalog model and prove explicit and automatic selection.
- Change one policy weight and predict the test result before running it.
- Test a failed half-open probe returning the circuit to open.
- Add a nullable project field through a new Alembic migration.
- Add a PII fixture and prove its value never reaches responses, storage, or logs.
- Approve a small canary, generate errors, and explain its automatic rollback.
- Add a rollout-group filter to the dashboard.

## Interview talking points

- It is an inference control plane, not just request forwarding.
- Hard constraints prevent a score from overriding privacy or circuit rules.
- Receipts make cost/reliability decisions debuggable and governable.
- LangGraph fits because human approval is a durable workflow interrupt.
- Plain typed Python keeps the latency-sensitive router deterministic and easy to
  test; LangChain is used at useful integration boundaries.
- Stateless API replicas coordinate project admission and durable state through
  PostgreSQL.
- The next focused extensions are normalized streaming usage, OpenTelemetry,
  managed administrator identity, load tests, and stronger DLP.

## Five-minute demo

1. Send an automatic request and inspect the receipt.
2. Switch cost to quality and compare candidate evidence.
3. Show PII redaction before provider execution.
4. Trigger provider failure, fallback, and a circuit transition.
5. Open the dashboard and verify the audit chain.
6. Simulate, approve, and canary a policy; explain rollback guardrails.
