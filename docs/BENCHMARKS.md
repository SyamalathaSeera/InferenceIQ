# Routing benchmarks (Phase 1)

The existing evaluation CLI now benchmarks the actual non-streaming
`POST /v1/chat/completions` route. Every fixed model and the automatic cost,
latency, quality, and balanced policies receive the same dataset, repetitions,
generation parameters, and attempt limit. Legacy single-provider evaluation
commands continue to work.

## Safe mock smoke run

From the repository root, with the development dependencies installed:

```powershell
.venv\Scripts\python.exe -m app.evaluation.cli --dataset evaluations/smoke.json --mock-smoke --fixed-models mock/fast --repetitions 3 --concurrency 1 --temperature 0 --max-tokens 32
```

No server process is required. This uses HTTPX's in-process ASGI transport against
the FastAPI application, including authentication/admission, routing, provider
execution, health recording and persistence. Each arm has its own in-memory
database and checkpoint store. Real providers and the observer are disabled by
explicit settings, and LangSmith tracing is disabled around execution. No dataset
or trace is uploaded. Mock responses, word-count token usage, and catalog-derived
costs are **simulated**; measured client wall time is local software execution time,
not model-serving latency. The mock ignores generation parameters, returns the
same response across models, and does not simulate catalog model latencies.

The supplied two-case dataset checks mock behavior, not real model capabilities.
Selection, counts, and assertion outcomes are deterministic under the default
serial smoke configuration. Timing varies between runs; higher concurrency also
changes scheduling and health observations.

To retain a report without adding a generated artifact to Git, use an existing
ignored directory:

```powershell
New-Item -ItemType Directory -Force build | Out-Null
.venv\Scripts\python.exe -m app.evaluation.cli --dataset evaluations/smoke.json --mock-smoke --repetitions 3 --concurrency 1 --temperature 0 --max-tokens 32 --output build/routing-smoke.json
```

## Benchmark an existing endpoint

The HTTP benchmark path does not construct provider clients or load CLI provider
settings. Configure providers and prices on an isolated server before running.
This command is a template: replace the fixed ID with a catalog model. Requests
to a real-provider server may incur charges; Phase 1 validation uses mocks only.

```powershell
.venv\Scripts\python.exe -m app.evaluation.cli --benchmark --base-url http://127.0.0.1:8000 --dataset evaluations/smoke.json --fixed-models mock/fast mock/quality --repetitions 3 --concurrency 2 --temperature 0 --max-tokens 32 --max-attempts 1 --timeout 60
```

Use a representative sanitized dataset for real models. The existing case schema
supports a unique `id`, `messages`, and at least one of `expected_exact`,
`required_substrings`, or `forbidden_substrings`. Exact comparison normalizes case
and whitespace. Substring checks are binary per applicable check category.

Fixed IDs must use `provider/model` form. Use catalog IDs with configured input
and output prices. Unknown model prices stay unavailable. The default one-attempt
limit isolates selection; a separate run with `--max-attempts 2` also exercises
automatic fallback. Explicit fixed routing remains one candidate.

Remote arms share server health, budgets and rate limits. Use a fresh dedicated
deployment, sufficient quotas, and controlled load. Explicit automatic policy
options bypass canary assignment. The CLI currently supports unauthenticated
benchmark deployments; it does not accept credentials in URLs or export request
headers. It does not upload LangSmith experiments (`--langsmith` is rejected with
benchmark mode). Server-side observability must be disabled separately for remote
runs. HTTPX environment proxies and `.netrc` authentication are disabled.

## Report interpretation

- The report includes a SHA-256 fingerprint of the normalized dataset, fixed model
  IDs, generation parameters, repetitions, concurrency, attempt limit, and a stable
  per-arm/per-repetition/per-case ordering. Arm submission order rotates by case
  and repetition. It does not promise identical model outputs or timings.
- Every submitted request has a result, including HTTP/transport failures and
  malformed responses. Errors are bounded categories, never copied response
  bodies or exception strings. Prompt and response text are not exported.
- `success` means a successful HTTP status with a valid completion and a choice.
  `http_status` preserves the actual status; transport failures have no status.
- p50/p95 use linear interpolation at `(n - 1) * percentile`. Overall latency
  includes failures; separate percentiles include successful responses only.
  Timing starts after the local concurrency semaphore and includes endpoint work,
  full response receipt, and response validation. In-process timing excludes TCP.
- Quality averages score only successful responses using applicable checks.
  `quality_pass_rate` divides fully passing responses by **all submitted requests**,
  so failures cannot inflate the passing rate. These checks are not a general
  semantic quality judge. Inspect per-case and per-task results before conclusions.
- Token counts represent the completed provider call. Missing provider usage is
  `null`, including when an adapter previously serialized placeholder zero counts.
  Observed zero usage and configured zero prices remain valid zeros.
- Completed-call cost is `(input_tokens * input_price + output_tokens * output_price)
  / 1_000_000`, using prices copied from the selected candidate into each result.
  It is a configured-price calculation, not a provider invoice. Cache/reasoning
  pricing, local hardware costs and failed-call charges are not modeled.
- `request_cost_usd` is unknown after fallback: the API does not expose failed-call
  usage. `total_cost_usd` is populated only when every request cost is known.
  `known_completed_call_cost_usd` is explicitly a partial sum, accompanied by
  coverage counts. Unknown cost or usage is never silently summed as zero.
- Observable `gateway_attempts` includes failed candidates plus the final success.
  Terminal failures expose no attempt count. SDK-internal retries are unavailable.
- Simulated result counts are explicit per arm. Do not interpret mock cost
  differences or passing checks as measured production improvements.

For a fair comparison, hold model versions, prices, dataset, generation settings,
server resources and load fixed. Keep warm-up separate, repeat trials, and inspect
tail latency alongside quality and failure counts. This implementation does not
yet provide confidence intervals, automatic warm-up, fault-injection schedules,
streaming metrics, semantic judging or automatic server-state reset for remote arms.

## Legacy evaluation and validation

```powershell
.venv\Scripts\python.exe -m app.evaluation.cli --dataset evaluations/smoke.json --model mock/fast
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m ruff format --check app tests migrations
.venv\Scripts\python.exe -m ruff check app tests migrations
```

The legacy `--langsmith` mode still explicitly publishes its dataset; it is separate
from benchmark mode and is not used for smoke validation.
