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

## Phase 2: deterministic failover and recovery

Use the same CLI and dataset with a fault scenario. This demo submits **100
requests per arm**, 500 total: two dataset cases repeated 50 times for one fixed
model and all four automatic policies. It exercises the actual FastAPI route,
ranked fallback executor, persistent health records, circuit opening, cooldown,
half-open probing and closing. No real providers or external services are used.

```powershell
New-Item -ItemType Directory -Force build | Out-Null
.venv\Scripts\python.exe -m app.evaluation.cli --dataset evaluations/smoke.json --mock-smoke --fault-scenario evaluations/failover.json --fixed-models mock/fast --repetitions 50 --concurrency 1 --max-attempts 2 --temperature 0 --max-tokens 32 --output build/failover-demo.json
```

Fault mode refuses to overwrite an existing output file. For another trial, choose
a new output filename, for example `build/failover-demo-002.json`. It never needs
to modify the Phase 1 `build/routing-smoke.json` report. To compare additional
baselines, use `--fixed-models mock/fast mock/balanced mock/quality`; each baseline
then receives the full 100-request schedule and its own comparisons.

`evaluations/failover.json` defines these inclusive, one-based request windows:

| Requests per arm | Affected model | Injected fault |
|---|---|---|
| 1–10 | None | Healthy initial period |
| 11–25 | `mock/fast` | Retryable rate limit, HTTP 429 |
| 31–45 | `mock/quality` | Retryable unavailable response, HTTP 503 |
| 51–65 | `mock/balanced` | Retryable provider timeout, HTTP 504 |
| 81–85 | `mock/fast` | Non-retryable authentication failure, HTTP 401 |

The explicit recovery windows are 26–30, 46–50, 66–80, and 86–100: 40 requests
per arm. There are 50 fault-window requests per arm. Fault windows describe
provider availability, independently of whether an arm chooses the affected
model. Healthy initial requests count toward overall success, not recovery rates.
This measures selection plus fallback and circuit behavior against fixed routing;
different policies can avoid a fault by selecting a different initial model.

Scenario JSON accepts a name, `request_interval_seconds`,
`circuit_failure_threshold`, `circuit_cooldown_seconds`, `health_ewma_alpha`,
`faults`, and `recovery_windows`. Each fault has `start`, `end`, `models`, `error`,
and optional `delay_ms` (0–1000). Error types are `rate_limit`, `timeout`,
`unavailable`, and `authentication`. Overlapping faults on the same model and
overlapping/non-healthy recovery windows are rejected. The number of requests
must cover every configured window. Only catalog mock IDs are accepted.

The schedule uses request indices, not provider attempt counts. Every attempt to
an affected model within a window receives the fault. Each arm starts with an
empty in-memory database and independent checkpoint/health state, retaining that
state throughout its trial. A simulated clock advances between requests (default
one second per request); the demo uses three failures to open and five virtual
seconds of cooldown. The endpoint uses its existing transition logic and persists
the resulting circuit state. There are no real cooldown sleeps.

Only the reliability simulation feeds fixed catalog latency estimates into health
scoring, so millisecond timing noise cannot change the routing schedule. The actual
provider and client wall-time measurements remain real. This does not change
normal server routing inputs. Fault mode requires serial in-process execution
(`--mock-smoke --concurrency 1`); concurrent or remote fault runs are rejected.
The schedule is fully explicit and uses no randomness, so no seed is needed.

An injected timeout is a classified provider exception, not a real socket timeout.
Its default delay is zero; `delay_ms` can add controlled wall-time delay. The demo
does not estimate real provider timeout durations, SDK retries, network behavior
or production outage recovery time. All outcomes, tokens and costs in this mode
are labeled simulated, including failed requests. Client latency is measured
local ASGI execution time with observation overhead; it excludes pre/post-request
health snapshots but includes the snapshot taken at each provider attempt.

### Reliability fields and denominators

The same report adds the scenario snapshot, `arms[].reliability`,
`reliability_comparisons`, and observations on each result:

- `fault_request_index`, virtual time, fault/recovery membership, exact gateway
  attempt counts (including zero), attempted/fallback models, injected status and
  retryability, and circuit state at each provider attempt.
- Persistent circuit snapshots before and after each request. Half-open state is
  also captured at provider entry, even when a successful probe immediately
  closes it. `circuits_opened` counts observed non-open-to-open snapshot changes;
  reopens within an already-open snapshot are visible in failed half-open attempts.
- `selected_model` is the final completed model, or the last attempted model on
  failure. It is null when routing rejects the request before any provider call.
  Model distributions include these observed final attempts, not just successes.

The metrics are:

| Metric | Definition |
|---|---|
| Overall success | Valid HTTP completions / all submitted requests |
| Fault-window success | Completions / requests within any fault window |
| Recovery success | Completions / requests within explicit fault-free recovery windows |
| Fallback success | Completions / requests making at least two provider attempts |
| Primary-failure recovery | Completions / requests whose first provider attempt failed |
| Mean attempts | All observed provider attempts / all submitted requests |
| Circuit-blocked requests | Failed requests with zero attempts, an open prior circuit and HTTP 422/503 |
| Circuit avoidance successes | Successful requests that skip a model remaining open |
| Improvement, percentage points | `(automatic rate - fixed rate) * 100` |

Empty denominators are `null`, including a fixed arm with no fallback attempts.
Failures and circuit blocks remain in overall latency and success denominators.
p50/p95 use the same Phase 1 interpolation and timing rules. A high fallback rate
can still coexist with non-retryable failures; inspect the separate primary-failure
and overall metrics. Healthy-window service success does not prove the original
model was probed or restored. Low-ranked half-open models can remain unprobed.

Attempt details come from the mock provider wrapper, not a new public API or error
response. Production/Phase 1 terminal errors still lack attempt information. The
existing unknown failed-call cost/usage rules remain in effect.

### Concurrent half-open safety finding

`test_concurrent_half_open_probe_safety_gap` demonstrates that two simultaneous
HTTP requests can both enter the same half-open model. It is a characterization
test of existing unsafe behavior, not a claim that probing is fixed. Serial fault
benchmarks avoid that race. No concurrency fix or schema migration is included.

The smallest robust fix is an atomic, expiring per-model probe lease in the health
store. Acquire it with a conditional database update immediately before attempting
a half-open candidate; only the lease holder may probe. Other callers should skip
the candidate or receive the existing unavailable response. Tie completion to the
lease token so stale results cannot close a newer circuit; let cancellation/crash
leases expire. A process-local lock alone does not protect multiple server workers.
That change would need a focused migration and concurrent lease/expiry tests.

Run all validation without writing pytest cache files:

```powershell
.venv\Scripts\python.exe -m pytest -o addopts= -q -p no:cacheprovider
.venv\Scripts\python.exe -m ruff format --check app tests migrations
.venv\Scripts\python.exe -m ruff check app tests migrations
```
