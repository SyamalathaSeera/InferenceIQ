import asyncio
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from time import perf_counter

import httpx
from langsmith import tracing_context
from pydantic import ValidationError

from app.evaluation.evaluators import aggregate_score, evaluate_output
from app.evaluation.faults import FaultContext
from app.evaluation.models import (
    BenchmarkArmSummary,
    BenchmarkCaseResult,
    BenchmarkReport,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationReport,
    FaultScenario,
    PriceCatalog,
    QualityComparison,
    ReliabilityArmSummary,
    ReliabilityComparison,
    VersionedDataset,
)
from app.evaluation.quality import (
    EVALUATION_VERSION,
    BudgetGuard,
    catalog_cost,
    fingerprint,
    observed_usage,
    semantic_evaluate,
)
from app.inference.metrics import calculate_cost
from app.inference.models import ModelProfile
from app.providers.base import ChatProvider, ProviderError
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse


def load_dataset(path: Path) -> list[EvaluationCase]:
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw_cases, dict):
        dataset = VersionedDataset.model_validate(raw_cases)
        return [
            case.model_copy(update={"dataset_version": dataset.version})
            for case in dataset.cases
        ]
    if not isinstance(raw_cases, list):
        raise ValueError("The evaluation dataset must be a JSON array")
    return [EvaluationCase.model_validate(case) for case in raw_cases]


async def run_evaluation(
    *,
    cases: list[EvaluationCase],
    model: str,
    providers: dict[str, ChatProvider],
    budget: BudgetGuard | None = None,
    price_catalog: PriceCatalog | None = None,
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
        if budget and not budget.reserve():
            raise ProviderError(
                "Budget halted before the next request", retryable=False
            )
        request = ChatCompletionRequest(model=model, messages=case.messages)
        started_at = perf_counter()
        with tracing_context(enabled=False):
            response = await provider.complete(request)
        input_tokens, output_tokens = observed_usage(response.model_dump())
        if budget:
            budget.observe(
                catalog_cost(price_catalog, model, input_tokens, output_tokens)
                if price_catalog
                else None
            )
        latency_ms = (perf_counter() - started_at) * 1000
        output = response.choices[0].message.content or ""
        evaluator_scores = evaluate_output(case, output)
        score = aggregate_score(evaluator_scores)
        results.append(
            EvaluationCaseResult(
                case_id=case.id,
                passed=score == 1.0 and not case.semantic_rubric,
                score=round(score, 4)
                if score is not None and not case.semantic_rubric
                else None,
                evaluator_scores=evaluator_scores,
                output=output,
                latency_ms=round(latency_ms, 2),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )

    count = len(results)
    return EvaluationReport(
        model=model,
        total_cases=count,
        passed_cases=sum(result.passed for result in results),
        pass_rate=(
            sum(result.passed for result in results) / len(scored)
            if (
                scored := [
                    result.score for result in results if result.score is not None
                ]
            )
            else None
        ),
        average_score=sum(scored) / len(scored) if scored else None,
        average_latency_ms=round(
            sum(result.latency_ms for result in results) / count if count else 0,
            2,
        ),
        total_input_tokens=sum(result.input_tokens for result in results)
        if all(result.input_tokens is not None for result in results)
        else None,
        total_output_tokens=sum(result.output_tokens for result in results)
        if all(result.output_tokens is not None for result in results)
        else None,
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
    arm: str,
    rows: list[BenchmarkCaseResult],
    *,
    strict_quality: bool = False,
) -> BenchmarkArmSummary:
    submitted = [row for row in rows if row.submitted]
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
    passed = sum(
        row.quality_passed if row.quality_passed is not None else row.quality_score == 1
        for row in rows
    )
    total_cost = (
        sum(row.request_cost_usd for row in rows) if known_costs == len(rows) else None
    )
    return BenchmarkArmSummary(
        arm=arm,
        requests=len(rows),
        successes=len(successful),
        success_rate=len(successful) / len(submitted) if submitted else None,
        p50_latency_ms=percentile(
            [row.latency_ms for row in rows if row.latency_ms is not None], 0.50
        ),
        p95_latency_ms=percentile(
            [row.latency_ms for row in rows if row.latency_ms is not None], 0.95
        ),
        successful_p50_latency_ms=percentile(
            [row.latency_ms for row in successful], 0.50
        ),
        successful_p95_latency_ms=percentile(
            [row.latency_ms for row in successful], 0.95
        ),
        average_quality_score=sum(scored) / len(scored) if scored else None,
        quality_scored_requests=len(scored),
        quality_pass_rate=(passed / len(scored) if scored else None)
        if strict_quality
        else passed / len(rows),
        quality_unknown_requests=len(rows) - len(scored),
        quality_passed_requests=passed,
        submitted_requests=len(submitted),
        cost_per_request_usd=total_cost / len(rows) if total_cost is not None else None,
        cost_per_quality_passing_response_usd=(
            total_cost / passed
            if total_cost is not None and passed > 0 and len(scored) == len(rows)
            else None
        ),
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
        reliability=(
            summarize_reliability(rows)
            if any(row.fault_request_index is not None for row in rows)
            else None
        ),
    )


def summarize_reliability(rows: list[BenchmarkCaseResult]) -> ReliabilityArmSummary:
    fault_rows = [row for row in rows if row.fault_window]
    recovery_rows = [row for row in rows if row.recovery_window]
    failed_primary = [
        row
        for row in rows
        if row.attempt_details and not row.attempt_details[0].success
    ]
    fallback_rows = [row for row in rows if (row.gateway_attempts or 0) > 1]

    def rate(selected):
        return (
            sum(row.success for row in selected) / len(selected) if selected else None
        )

    return ReliabilityArmSummary(
        fault_window_requests=len(fault_rows),
        fault_window_success_rate=rate(fault_rows),
        recovery_window_requests=len(recovery_rows),
        recovery_success_rate=rate(recovery_rows),
        primary_failure_requests=len(failed_primary),
        primary_failure_recovery_rate=rate(failed_primary),
        fallback_requests=len(fallback_rows),
        fallback_success_rate=rate(fallback_rows),
        total_gateway_attempts=sum(row.gateway_attempts or 0 for row in rows),
        mean_gateway_attempts=sum(row.gateway_attempts or 0 for row in rows)
        / len(rows),
        circuit_blocked_requests=sum(
            row.gateway_attempts == 0
            and not row.success
            and "open" in row.circuit_before.values()
            and row.http_status in {422, 503}
            for row in rows
        ),
        circuit_avoidance_successes=sum(
            row.success and bool(row.avoided_open_models) for row in rows
        ),
        half_open_probes=sum(
            attempt.circuit_state == "half_open"
            for row in rows
            for attempt in row.attempt_details
        ),
        circuits_opened=sum(
            state == "open" and row.circuit_before.get(model, "closed") != "open"
            for row in rows
            for model, state in row.circuit_after.items()
        ),
    )


def compare_reliability(arms: list[BenchmarkArmSummary]) -> list[ReliabilityComparison]:
    def difference(candidate, baseline):
        return (
            (candidate - baseline) * 100
            if (candidate is not None and baseline is not None)
            else None
        )

    return [
        ReliabilityComparison(
            baseline_arm=baseline.arm,
            automatic_arm=automatic.arm,
            success_improvement_pp=(automatic.success_rate - baseline.success_rate)
            * 100,
            fault_window_success_improvement_pp=difference(
                automatic.reliability.fault_window_success_rate,
                baseline.reliability.fault_window_success_rate,
            ),
            recovery_success_improvement_pp=difference(
                automatic.reliability.recovery_success_rate,
                baseline.reliability.recovery_success_rate,
            ),
        )
        for baseline in arms
        if baseline.arm.startswith("fixed:") and baseline.reliability
        for automatic in arms
        if automatic.arm.startswith("auto:") and automatic.reliability
    ]


def compare_quality(arms: list[BenchmarkArmSummary]) -> list[QualityComparison]:
    def difference(value, baseline, scale=1):
        return (
            (value - baseline) * scale
            if value is not None and baseline is not None
            else None
        )

    comparisons = []
    for baseline in arms:
        if not baseline.arm.startswith("fixed:"):
            continue
        for candidate in arms:
            if candidate.arm == baseline.arm:
                continue
            complete = (
                candidate.submitted_requests == candidate.requests
                and baseline.submitted_requests == baseline.requests
            )
            quality_complete = complete and not (
                candidate.quality_unknown_requests or baseline.quality_unknown_requests
            )
            comparisons.append(
                QualityComparison(
                    baseline_arm=baseline.arm,
                    comparison_arm=candidate.arm,
                    quality_pass_rate_difference_pp=difference(
                        candidate.quality_pass_rate, baseline.quality_pass_rate, 100
                    )
                    if quality_complete
                    else None,
                    cost_per_request_difference_usd=difference(
                        candidate.cost_per_request_usd, baseline.cost_per_request_usd
                    )
                    if complete
                    else None,
                    cost_per_quality_passing_response_difference_usd=difference(
                        candidate.cost_per_quality_passing_response_usd,
                        baseline.cost_per_quality_passing_response_usd,
                    )
                    if complete
                    else None,
                    p50_latency_difference_ms=difference(
                        candidate.p50_latency_ms, baseline.p50_latency_ms
                    )
                    if complete
                    else None,
                    p95_latency_difference_ms=difference(
                        candidate.p95_latency_ms, baseline.p95_latency_ms
                    )
                    if complete
                    else None,
                )
            )
    return comparisons


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
    fault_scenario: FaultScenario | None = None,
    fault_contexts: dict[str, FaultContext] | None = None,
    quality_mode: bool = False,
    quality_threshold: float = 1,
    price_catalog: PriceCatalog | None = None,
    semantic_judge_model: str | None = None,
    budget: BudgetGuard | None = None,
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
    if not 0 <= quality_threshold <= 1:
        raise ValueError("Quality threshold must be within [0, 1]")
    if fault_scenario and (semantic_judge_model or budget):
        raise ValueError(
            "Fault schedules cannot be combined with judges or live budgets"
        )
    if budget and concurrency != 1:
        raise ValueError("Guarded live runs require concurrency 1")
    if fault_scenario is not None:
        if concurrency != 1 or not clients_by_arm or not fault_contexts:
            raise ValueError("Fault runs require serial, isolated in-process arms")
        if any(
            arm not in fault_contexts or arm not in clients_by_arm for arm, _, _ in arms
        ):
            raise ValueError("Every arm requires a fault context and client")
        contexts = [fault_contexts[arm] for arm, _, _ in arms]
        if len({id(context.app) for context in contexts}) != len(arms):
            raise ValueError("Fault arms must not share application state")
        for context in contexts:
            if (
                context.scenario != fault_scenario
                or context.index
                or await context.snapshot()
            ):
                raise ValueError(
                    "Fault arms require matching scenarios and fresh health state"
                )
        windows = fault_scenario.faults + fault_scenario.recovery_windows
        if repetitions * len(cases) < max(window.end for window in windows):
            raise ValueError("Request count must cover every scenario window")
    elif fault_contexts:
        raise ValueError("Fault contexts require a scenario")
    semaphore = asyncio.Semaphore(concurrency)

    async def score_answer(row, case, answer, target):
        scores = evaluate_output(case, answer)
        if case.semantic_rubric:
            score, judge_cost, reason, requested = await semantic_evaluate(
                case,
                answer,
                client=target,
                model=semantic_judge_model,
                catalog=price_catalog,
                budget=budget,
            )
            row.judge_cost_usd = judge_cost
            row.judge_requested = requested
            if score is None:
                row.quality_missing_reason = reason
            else:
                scores["semantic"] = score
        row.evaluator_scores = scores
        row.quality_score = (
            aggregate_score(scores) if not row.quality_missing_reason else None
        )
        if row.quality_score is not None:
            row.quality_passed = row.quality_score >= quality_threshold
        elif row.quality_missing_reason is None:
            row.quality_missing_reason = "no_applicable_assertions"

    async def measure(case, repetition, arm, model, policy, request_index):
        async with semaphore:
            return await execute_request(
                case, repetition, arm, model, policy, request_index
            )

    async def execute_request(case, repetition, arm, model, policy, request_index):
        payload = ChatCompletionRequest(
            model=model,
            messages=case.messages,
            inferenceiq={"policy": policy, "max_attempts": max_attempts},
            **parameters,
        ).model_dump(exclude_none=True)
        if budget and not budget.reserve():
            return BenchmarkCaseResult(
                arm=arm,
                case_id=case.id,
                repetition=repetition,
                requested_model=model,
                latency_ms=None,
                submitted=False,
                error="budget_halted",
            )
        context = fault_contexts[arm] if fault_scenario is not None else None
        if context:
            await context.begin(request_index)
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
        if context:
            await context.observe(row)

        if data is None:
            if budget:
                budget.observe(None)
            return row
        row.response_model = data.model
        row.simulated = data.metadata.get("simulated", False)
        # Legacy providers can serialize default zero usage; explicit availability
        # metadata distinguishes missing usage from a legitimate observed zero.
        row.input_tokens, row.output_tokens = observed_usage(response.json())
        receipt = data.inference_receipt
        if receipt is None:
            row.selected_model = data.model
            if budget:
                budget.observe(None)
            await score_answer(row, case, data.choices[0].message.content or "", target)
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
        if price_catalog:
            row.pricing_version = price_catalog.version
            row.routing_input_cost_per_million = row.input_cost_per_million
            row.routing_output_cost_per_million = row.output_cost_per_million
            price = price_catalog.models.get(receipt.selected_model)
            row.input_cost_per_million = price.input_cost_per_million if price else None
            row.output_cost_per_million = (
                price.output_cost_per_million if price else None
            )
            if (
                price is not None
                and candidate is not None
                and all(
                    value is not None
                    for value in (
                        price.input_cost_per_million,
                        price.output_cost_per_million,
                        candidate.input_cost_per_million,
                        candidate.output_cost_per_million,
                    )
                )
            ):
                row.pricing_matches_routing = (
                    price.input_cost_per_million == candidate.input_cost_per_million
                    and price.output_cost_per_million
                    == candidate.output_cost_per_million
                )
            row.completed_call_cost_usd = catalog_cost(
                price_catalog,
                receipt.selected_model,
                row.input_tokens,
                row.output_tokens,
            )
            row.request_cost_usd = (
                row.completed_call_cost_usd if not receipt.fallbacks else None
            )
        if budget:
            budget.observe(row.request_cost_usd)
        await score_answer(row, case, data.choices[0].message.content or "", target)
        return row

    # Rotate arm order per case/repetition to avoid always measuring fixed first.
    tasks = []
    for repetition in range(1, repetitions + 1):
        for case_index, case in enumerate(cases):
            offset = (repetition - 1 + case_index) % len(arms)
            for arm, model, policy in arms[offset:] + arms[:offset]:
                tasks.append(
                    measure(
                        case,
                        repetition,
                        arm,
                        model,
                        policy,
                        (repetition - 1) * len(cases) + case_index + 1,
                    )
                )
    results = await asyncio.gather(*tasks)
    order = {arm: index for index, (arm, _, _) in enumerate(arms)}
    case_order = {case.id: index for index, case in enumerate(cases)}
    results.sort(
        key=lambda row: (order[row.arm], row.repetition, case_order[row.case_id])
    )
    canonical = json.dumps(
        [case.model_dump(mode="json") for case in cases], sort_keys=True
    )
    summaries = [
        summarize_benchmark(
            arm, [row for row in results if row.arm == arm], strict_quality=quality_mode
        )
        for arm, _, _ in arms
    ]
    report = BenchmarkReport(
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
            (
                "Quality-v1 pass rates use scored answers; "
                "inspect missing-score coverage."
                if quality_mode
                else "Legacy pass rates include all submitted requests and failures."
            ),
            (
                "Costs use the versioned price snapshot and reported usage. "
                if price_catalog
                else "Costs use receipt candidate prices and reported usage, "
            )
            + "not predicted tokens. "
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
        arms=summaries,
        results=results,
        fault_scenario=fault_scenario,
        reliability_comparisons=compare_reliability(summaries),
        dataset_version=cases[0].dataset_version,
        evaluation_version=EVALUATION_VERSION if quality_mode else None,
        quality_threshold=quality_threshold,
        price_catalog=price_catalog,
        price_catalog_sha256=fingerprint(price_catalog.model_dump())
        if price_catalog
        else None,
        quality_comparisons=compare_quality(summaries) if quality_mode else [],
        semantic_judge_model=semantic_judge_model,
        budget_usd=budget.ceiling if budget else None,
        reserved_budget_usd=budget.reserved if budget else None,
        budget_halted=budget.halted if budget else False,
        judge_requests=sum(row.judge_requested for row in results),
        total_judge_cost_usd=(
            sum(row.judge_cost_usd for row in results if row.judge_requested)
            if any(row.judge_requested for row in results)
            and all(
                row.judge_cost_usd is not None for row in results if row.judge_requested
            )
            else None
        ),
    )
    if quality_mode:
        report.methodology.extend(
            [
                "Quality-v1 pass rate uses scored answers only; "
                "missing scores stay unknown. "
                "Coverage and unknown counts must accompany every rate. "
                "Quality differences require complete coverage on both arms.",
                "Cost per request requires complete pricing/usage; "
                "cost per passing answer "
                "also requires complete quality coverage and at least one pass. "
                "Judge charges are separate from inference cost.",
                "Versioned price snapshots override receipt prices for quality runs. "
                "Reservations are operator inputs, not guaranteed invoices; "
                "unknown/excessive charges halt requests. Use provider account caps.",
            ]
        )
    if fault_scenario:
        report.methodology.extend(
            [
                "Injected-fault simulation, not live-provider reliability. "
                "Fault windows use per-arm request indices, "
                "never provider-attempt counts.",
                "Circuit time advances by the scenario interval between requests; "
                "no cooldown sleeps. Routing latency uses catalog estimates "
                "for determinism; reported client/provider wall times remain measured.",
                "Recovery success means completion within explicit fault-free recovery "
                "windows. Fault-window success is separate. Empty rates are null.",
                "Fallback success is conditional on >=2 provider attempts. "
                "Primary-failure recovery includes non-retryable failures. "
                "Circuit blocks have zero attempts.",
                "Percentage-point differences are automatic minus fixed rates * 100. "
                "Health snapshots and injection add local measurement overhead.",
            ]
        )
    return report
