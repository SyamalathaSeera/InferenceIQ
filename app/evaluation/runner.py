import asyncio
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from time import perf_counter

import httpx
from pydantic import ValidationError

from app.evaluation.evaluators import aggregate_score, evaluate_output
from app.evaluation.models import (
    BenchmarkArmSummary,
    BenchmarkCaseResult,
    BenchmarkReport,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationReport,
)
from app.inference.metrics import calculate_cost
from app.inference.models import ModelProfile
from app.providers.base import ChatProvider, ProviderError
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse


def load_dataset(path: Path) -> list[EvaluationCase]:
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_cases, list):
        raise ValueError("The evaluation dataset must be a JSON array")
    return [EvaluationCase.model_validate(case) for case in raw_cases]


async def run_evaluation(
    *,
    cases: list[EvaluationCase],
    model: str,
    providers: dict[str, ChatProvider],
) -> EvaluationReport:
    provider_name = _provider_name(model)
    provider = providers.get(provider_name)
    if provider is None:
        raise ProviderError(
            f"Provider '{provider_name}' is not configured.",
            status_code=400,
            category="configuration",
            retryable=False,
        )

    results: list[EvaluationCaseResult] = []
    for case in cases:
        request = ChatCompletionRequest(model=model, messages=case.messages)
        started_at = perf_counter()
        response = await provider.complete(request)
        latency_ms = (perf_counter() - started_at) * 1000
        output = response.choices[0].message.content or ""
        evaluator_scores = evaluate_output(case, output)
        score = aggregate_score(evaluator_scores)
        results.append(
            EvaluationCaseResult(
                case_id=case.id,
                passed=score == 1.0,
                score=round(score, 4),
                evaluator_scores=evaluator_scores,
                output=output,
                latency_ms=round(latency_ms, 2),
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            )
        )

    count = len(results)
    return EvaluationReport(
        model=model,
        total_cases=count,
        passed_cases=sum(result.passed for result in results),
        pass_rate=round(
            sum(result.passed for result in results) / count if count else 0,
            4,
        ),
        average_score=round(
            sum(result.score for result in results) / count if count else 0,
            4,
        ),
        average_latency_ms=round(
            sum(result.latency_ms for result in results) / count if count else 0,
            2,
        ),
        total_input_tokens=sum(result.input_tokens for result in results),
        total_output_tokens=sum(result.output_tokens for result in results),
        results=results,
    )


def _provider_name(model: str) -> str:
    provider, separator, _ = model.partition("/")
    if not separator:
        raise ValueError("Evaluation model must use a provider/model identifier")
    return provider


def benchmark_arms(fixed_models: list[str]) -> list[tuple[str, str, str]]:
    return [(f"fixed:{model}", model, "balanced") for model in fixed_models] + [
        (f"auto:{policy}", "auto", policy)
        for policy in ("cost", "latency", "quality", "balanced")
    ]


def percentile(values: list[float], fraction: float) -> float | None:
    """Linear interpolation at (n - 1) * fraction, including single samples."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize_benchmark(
    arm: str, rows: list[BenchmarkCaseResult]
) -> BenchmarkArmSummary:
    successful = [row for row in rows if row.success]
    scored = [row.quality_score for row in rows if row.quality_score is not None]
    costs = [
        row.completed_call_cost_usd
        for row in rows
        if row.completed_call_cost_usd is not None
    ]
    known_costs = sum(row.request_cost_usd is not None for row in rows)
    known_usage = sum(
        row.input_tokens is not None and row.output_tokens is not None for row in rows
    )
    return BenchmarkArmSummary(
        arm=arm,
        requests=len(rows),
        successes=len(successful),
        success_rate=len(successful) / len(rows),
        p50_latency_ms=percentile([row.latency_ms for row in rows], 0.50),
        p95_latency_ms=percentile([row.latency_ms for row in rows], 0.95),
        successful_p50_latency_ms=percentile(
            [row.latency_ms for row in successful], 0.50
        ),
        successful_p95_latency_ms=percentile(
            [row.latency_ms for row in successful], 0.95
        ),
        average_quality_score=sum(scored) / len(scored) if scored else None,
        quality_scored_requests=len(scored),
        quality_pass_rate=sum(score == 1 for score in scored) / len(rows),
        total_input_tokens=(
            sum(row.input_tokens for row in rows) if known_usage == len(rows) else None
        ),
        total_output_tokens=(
            sum(row.output_tokens for row in rows) if known_usage == len(rows) else None
        ),
        usage_known_requests=known_usage,
        total_cost_usd=(
            sum(row.request_cost_usd for row in rows)
            if known_costs == len(rows)
            else None
        ),
        known_completed_call_cost_usd=sum(costs) if costs else None,
        cost_known_requests=known_costs,
        simulated_requests=sum(row.simulated is True for row in rows),
        model_distribution=dict(
            Counter(
                row.selected_model for row in rows if row.selected_model is not None
            )
        ),
    )


async def run_benchmark(
    *,
    cases: list[EvaluationCase],
    fixed_models: list[str],
    client: httpx.AsyncClient,
    repetitions: int = 1,
    concurrency: int = 1,
    temperature: float | None = None,
    max_tokens: int | None = None,
    max_attempts: int = 1,
    clients_by_arm: dict[str, httpx.AsyncClient] | None = None,
) -> BenchmarkReport:
    """Benchmark the real non-streaming HTTP route, retaining failed requests."""
    if not cases or repetitions < 1 or concurrency < 1 or not fixed_models:
        raise ValueError(
            "Nonempty cases/models and positive repetitions/concurrency required"
        )
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Dataset case IDs must be unique")
    if len(set(fixed_models)) != len(fixed_models):
        raise ValueError("Fixed model IDs must be unique")
    if any(model == "auto" or "/" not in model for model in fixed_models):
        raise ValueError("Fixed models must use provider/model identifiers")
    parameters = {
        key: value
        for key, value in {"temperature": temperature, "max_tokens": max_tokens}.items()
        if value is not None
    }
    # Validate request options before any HTTP calls.
    ChatCompletionRequest(
        messages=cases[0].messages,
        inferenceiq={"max_attempts": max_attempts},
        **parameters,
    )
    arms = benchmark_arms(fixed_models)
    semaphore = asyncio.Semaphore(concurrency)

    async def measure(case, repetition, arm, model, policy):
        payload = ChatCompletionRequest(
            model=model,
            messages=case.messages,
            inferenceiq={"policy": policy, "max_attempts": max_attempts},
            **parameters,
        ).model_dump(exclude_none=True)
        async with semaphore:
            started_at = perf_counter()
            status = None
            data = None
            error = None
            try:
                target = clients_by_arm[arm] if clients_by_arm else client
                response = await target.post("/v1/chat/completions", json=payload)
                status = response.status_code
                if response.is_success:
                    data = ChatCompletionResponse.model_validate(response.json())
                    if not data.choices:
                        error = "invalid_response"
                        data = None
                else:
                    # Never copy server bodies, prompts, headers or exception strings.
                    error = f"http_{status}"
            except httpx.TimeoutException:
                error = "client_timeout"
            except httpx.RequestError:
                error = "transport_error"
            except (ValidationError, ValueError, TypeError):
                error = "invalid_response"
            elapsed = (perf_counter() - started_at) * 1000

        row = BenchmarkCaseResult(
            arm=arm,
            case_id=case.id,
            repetition=repetition,
            requested_model=model,
            http_status=status,
            success=data is not None,
            error=error,
            latency_ms=elapsed,
        )
        if data is None:
            return row
        row.response_model = data.model
        row.simulated = data.metadata.get("simulated", False)
        scores = evaluate_output(case, data.choices[0].message.content or "")
        row.evaluator_scores = scores
        row.quality_score = aggregate_score(scores) if scores else None
        # Legacy providers can serialize default zero usage; explicit availability
        # metadata distinguishes missing usage from a legitimate observed zero.
        raw_usage = response.json().get("usage") or {}
        if data.metadata.get("usage_available", True) and all(
            type(raw_usage.get(key)) is int and raw_usage[key] >= 0
            for key in ("prompt_tokens", "completion_tokens")
        ):
            row.input_tokens = data.usage.prompt_tokens
            row.output_tokens = data.usage.completion_tokens
        receipt = data.inference_receipt
        if receipt is None:
            row.selected_model = data.model
            return row
        row.selected_model = receipt.selected_model
        row.gateway_attempts = len(receipt.fallbacks) + 1
        candidate = next(
            (
                item
                for item in receipt.candidates
                if item.model == receipt.selected_model
            ),
            None,
        )
        if candidate:
            row.input_cost_per_million = candidate.input_cost_per_million
            row.output_cost_per_million = candidate.output_cost_per_million
        if (
            candidate
            and row.input_tokens is not None
            and row.output_tokens is not None
            and all(
                price is not None and math.isfinite(price) and price >= 0
                for price in (
                    candidate.input_cost_per_million,
                    candidate.output_cost_per_million,
                )
            )
        ):
            row.completed_call_cost_usd = calculate_cost(
                ModelProfile(
                    model_id=candidate.model,
                    provider=candidate.provider,
                    quality_score=0,
                    estimated_latency_ms=1,
                    input_cost_per_million=candidate.input_cost_per_million,
                    output_cost_per_million=candidate.output_cost_per_million,
                ),
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
            )
            if not receipt.fallbacks:
                row.request_cost_usd = row.completed_call_cost_usd
        return row

    # Rotate arm order per case/repetition to avoid always measuring fixed first.
    tasks = []
    for repetition in range(1, repetitions + 1):
        for case_index, case in enumerate(cases):
            offset = (repetition - 1 + case_index) % len(arms)
            for arm, model, policy in arms[offset:] + arms[:offset]:
                tasks.append(measure(case, repetition, arm, model, policy))
    results = await asyncio.gather(*tasks)
    order = {arm: index for index, (arm, _, _) in enumerate(arms)}
    case_order = {case.id: index for index, case in enumerate(cases)}
    results.sort(
        key=lambda row: (order[row.arm], row.repetition, case_order[row.case_id])
    )
    canonical = json.dumps(
        [case.model_dump(mode="json") for case in cases], sort_keys=True
    )
    return BenchmarkReport(
        dataset_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
        repetitions=repetitions,
        concurrency=concurrency,
        fixed_models=fixed_models,
        generation_parameters=parameters,
        max_attempts=max_attempts,
        client_timeout_seconds=client.timeout.as_dict(),
        methodology=[
            "Non-streaming client wall time includes HTTP completion and validation; "
            "excludes local semaphore queue time. "
            "Percentiles use linear interpolation.",
            "Quality averages use applicable assertions on successful responses; "
            "quality pass rate uses all submitted requests, including failures.",
            "Costs use receipt candidate prices and reported usage, "
            "not predicted tokens. "
            "Unknown totals remain null; known completed-call costs are partial sums.",
            "Fallback request costs are unknown: failed-call usage is unavailable. "
            "Gateway attempts exclude SDK retries; failures may hide attempts. "
            "Token totals cover completed calls only.",
            "Mock tokens, costs and response quality are simulated; "
            "wall time is measured. "
            "Mock latency does not simulate catalog latency or real model performance.",
            (
                "Each arm uses isolated in-memory application state."
                if clients_by_arm
                else "Remote arms share server state; use an isolated deployment."
            ),
            "Fixed and automatic arms receive identical generation parameters; "
            "explicit policies bypass canary assignment. "
            "No prompt/output text is exported.",
        ],
        arms=[
            summarize_benchmark(arm, [row for row in results if row.arm == arm])
            for arm, _, _ in arms
        ],
        results=results,
    )
