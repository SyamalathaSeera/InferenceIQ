import argparse
import asyncio
import json
import math
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
from langsmith import Client, tracing_context

from app.config import Settings, get_settings
from app.evaluation.faults import FaultContext, FaultInjectingMockProvider
from app.evaluation.langsmith_experiment import LangSmithExperimentRunner
from app.evaluation.models import FaultScenario, PriceCatalog
from app.evaluation.quality import (
    BudgetGuard,
    benchmark_plan,
    catalog_cost,
    fingerprint,
    observed_usage,
)
from app.evaluation.runner import (
    benchmark_arms,
    load_dataset,
    run_benchmark,
    run_evaluation,
)
from app.inference.catalog import create_model_catalog
from app.providers.factory import create_providers
from app.schemas.chat import ChatCompletionRequest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an InferenceIQ evaluation")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model", help="provider/model identifier (legacy evaluation)")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--mock-smoke", action="store_true")
    parser.add_argument("--fault-scenario", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--fixed-models", nargs="+")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--max-attempts", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quality", action="store_true")
    parser.add_argument("--price-catalog", type=Path)
    parser.add_argument("--quality-threshold", type=float, default=1)
    parser.add_argument("--semantic-judge-model")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--budget-usd", type=float)
    parser.add_argument("--request-budget-usd", type=float)
    parser.add_argument("--publish-langsmith", action="store_true")
    parser.add_argument("--experiment-version", default="v1")
    parser.add_argument(
        "--langsmith",
        action="store_true",
        help="Run and publish the experiment through LangSmith",
    )
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("Output already exists; select a new path to preserve reports")
    for value in (args.budget_usd, args.request_budget_usd):
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error("Budget values must be finite and positive")
    if args.fault_scenario:
        if args.semantic_judge_model or args.publish_langsmith:
            parser.error("Fault scenarios cannot use judges or experiment uploads")
        if not args.mock_smoke or args.concurrency != 1:
            parser.error("--fault-scenario requires --mock-smoke and --concurrency 1")
        if args.langsmith:
            parser.error("Fault runs cannot upload LangSmith data")
        if args.output and args.output.exists():
            parser.error("Fault output already exists; select a new path")
    if args.mock_smoke:
        args.benchmark = True
    if args.benchmark:
        if args.langsmith:
            parser.error("Benchmarks do not support external LangSmith uploads")
        if not args.fixed_models:
            args.fixed_models = [args.model or "mock/fast"] if args.mock_smoke else None
        if not args.fixed_models:
            parser.error("--benchmark requires --fixed-models (or --mock-smoke)")
        if min(args.repetitions, args.concurrency, args.timeout) <= 0:
            parser.error("repetitions, concurrency and timeout must be positive")
        if args.mock_smoke and any(
            model not in {"mock/fast", "mock/balanced", "mock/quality"}
            for model in args.fixed_models
        ):
            parser.error("--mock-smoke requires catalog mock model IDs")
    elif not args.model:
        parser.error("--model is required for legacy evaluation")
    if not 0 <= args.quality_threshold <= 1:
        parser.error("--quality-threshold must be within [0, 1]")
    if args.semantic_judge_model and (
        args.semantic_judge_model == "auto" or "/" not in args.semantic_judge_model
    ):
        parser.error("The semantic judge must be a fixed provider/model ID")
    live = not args.mock_smoke and (
        args.benchmark or not args.model.startswith("mock/")
    )
    if live and not args.dry_run:
        if not args.allow_live or not args.price_catalog:
            parser.error(
                "Live evaluation requires --allow-live and --price-catalog; "
                "use --dry-run first"
            )
        if args.budget_usd is None or args.request_budget_usd is None:
            parser.error(
                "Live evaluation requires --budget-usd and --request-budget-usd"
            )
        if args.concurrency != 1 or args.max_attempts != 1:
            parser.error(
                "Guarded live evaluation requires concurrency=1 and max-attempts=1"
            )
    if args.publish_langsmith and not args.benchmark:
        parser.error("--publish-langsmith requires benchmark mode")
    return args


def evaluation_catalog(args, cases):
    quality_mode = (
        getattr(args, "quality", False)
        or getattr(args, "price_catalog", None)
        or cases[0].dataset_version != "legacy-v1"
    )
    path = getattr(args, "price_catalog", None)
    if path is None and quality_mode and args.mock_smoke:
        path = Path(__file__).parents[2] / "evaluations" / "prices.mock.v1.json"
    catalog = (
        PriceCatalog.model_validate_json(path.read_text(encoding="utf-8"))
        if path
        else None
    )
    return bool(quality_mode), catalog


def require_public_cases(cases):
    actual = fingerprint([case.model_dump() for case in cases])
    for filename in ("quality.v1.json", "smoke.json"):
        approved = load_dataset(Path(__file__).parents[2] / "evaluations" / filename)
        if actual == fingerprint([case.model_dump() for case in approved]):
            return
    raise ValueError("Live CLI evaluation is restricted to bundled public datasets")


async def benchmark_command(args, cases):
    quality_mode, catalog = evaluation_catalog(args, cases)
    judge = getattr(args, "semantic_judge_model", None)
    if judge:
        public = load_dataset(
            Path(__file__).parents[2] / "evaluations" / "quality.v1.json"
        )
        if fingerprint([case.model_dump() for case in cases]) != fingerprint(
            [case.model_dump() for case in public]
        ):
            raise ValueError(
                "Semantic judging is restricted to the bundled public dataset"
            )
    if args.mock_smoke and judge and not judge.startswith("mock/"):
        raise ValueError("Mock smoke cannot use a live semantic judge")
    budget = None
    if not args.mock_smoke:
        if not getattr(args, "allow_live", False) or catalog is None:
            raise ValueError(
                "Live execution requires explicit approval and versioned pricing"
            )
        budget = BudgetGuard(args.budget_usd, args.request_budget_usd)
        plan = benchmark_plan(
            cases,
            args.fixed_models,
            args.repetitions,
            judge,
            budget.ceiling,
            budget.per_call,
        )
        if not plan["fits_budget"]:
            raise ValueError("Full comparison does not fit the reserved budget")
        required = set(args.fixed_models) | ({judge} if judge else set())
        if not required.issubset(catalog.models):
            raise ValueError("Price catalog must include every fixed model and judge")
        if any(
            value is None
            for model in required
            for value in catalog.models[model].model_dump().values()
        ):
            raise ValueError("Live fixed models and judges require known prices")
        if catalog.simulated and any(
            not model.startswith("mock/") for model in catalog.models
        ):
            raise ValueError(
                "Simulated prices cannot authorize real-provider execution"
            )
        require_public_cases(cases)
    scenario_path = getattr(args, "fault_scenario", None)
    scenario = (
        FaultScenario.model_validate_json(scenario_path.read_text(encoding="utf-8"))
        if scenario_path
        else None
    )
    if scenario and (not args.mock_smoke or args.concurrency != 1):
        raise ValueError("Fault runs require isolated serial mock arms")
    options = {
        "cases": cases,
        "fixed_models": args.fixed_models,
        "repetitions": args.repetitions,
        "concurrency": args.concurrency,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "max_attempts": args.max_attempts,
        "quality_mode": quality_mode,
        "quality_threshold": getattr(args, "quality_threshold", 1),
        "price_catalog": catalog,
        "semantic_judge_model": judge,
        "budget": budget,
    }
    if not args.mock_smoke:
        url = httpx.URL(args.base_url)
        if (
            url.scheme not in {"http", "https"}
            or url.userinfo
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "Use an HTTP base URL without credentials, query or fragment"
            )
        async with httpx.AsyncClient(
            base_url=url, timeout=args.timeout, trust_env=False
        ) as client:
            return await run_benchmark(client=client, **options)

    from app.main import create_app

    # Explicit values override environment configuration; no real providers or
    # observer clients are constructed. State is isolated for every benchmark arm.
    settings = Settings(
        _env_file=None,
        provider="mock",
        openai_api_key=None,
        anthropic_api_key=None,
        langsmith_api_key=None,
        ollama_enabled=False,
        database_url="sqlite+aiosqlite:///:memory:",
        langgraph_checkpoint_url=":memory:",
        auto_create_schema=True,
        inferenceiq_langsmith_enabled=False,
        inferenceiq_auth_required=False,
        circuit_failure_threshold=scenario.circuit_failure_threshold if scenario else 3,
        circuit_cooldown_seconds=scenario.circuit_cooldown_seconds if scenario else 30,
        health_ewma_alpha=scenario.health_ewma_alpha if scenario else 0.2,
    )
    async with AsyncExitStack() as stack:
        stack.enter_context(tracing_context(enabled=False))
        clients = {}
        contexts = {}
        catalog_latency = {
            profile.model_id: profile.estimated_latency_ms
            for profile in create_model_catalog(settings)
        }
        for arm, _, _ in benchmark_arms(args.fixed_models):
            context = FaultContext(scenario) if scenario else None
            app = create_app(
                settings, **({"health_clock": context.now} if context else {})
            )
            await stack.enter_async_context(app.router.lifespan_context(app))
            if context:
                context.app = app
                contexts[arm] = context
                app.state.providers = {"mock": FaultInjectingMockProvider(context)}
                app.state.health_latency_ms = lambda model, elapsed: catalog_latency[
                    model
                ]
            clients[arm] = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                    base_url="http://mock-benchmark",
                    timeout=args.timeout,
                    trust_env=False,
                )
            )
        return await run_benchmark(
            client=next(iter(clients.values())),
            clients_by_arm=clients,
            fault_scenario=scenario,
            fault_contexts=contexts or None,
            **options,
        )


async def main() -> None:
    args = parse_args()
    cases = load_dataset(args.dataset)
    if args.dry_run:
        _, catalog = evaluation_catalog(args, cases)
        plan = benchmark_plan(
            cases,
            args.fixed_models if args.benchmark else [args.model],
            args.repetitions if args.benchmark else 1,
            args.semantic_judge_model,
            args.budget_usd,
            args.request_budget_usd,
            automatic=args.benchmark,
        )
        plan["pricing_version"] = catalog.version if catalog else None
        plan["dataset_version"] = cases[0].dataset_version
        plan["would_publish_langsmith"] = args.publish_langsmith or args.langsmith
        print(json.dumps(plan, indent=2))
        return
    if args.benchmark:
        report = await benchmark_command(args, cases)
        if args.publish_langsmith:
            report.langsmith_experiments = await LangSmithExperimentRunner(
                Client()
            ).publish_report(
                report,
                enabled=True,
                experiment_version=args.experiment_version,
            )
        rendered = report.model_dump_json(indent=2)
        if args.output:
            with args.output.open("x", encoding="utf-8") as output:
                output.write(rendered + "\n")
        else:
            print(rendered)
        return
    legacy_budget = None
    _, legacy_catalog = evaluation_catalog(args, cases)
    if not args.model.startswith("mock/"):
        legacy_budget = BudgetGuard(args.budget_usd, args.request_budget_usd)
        plan = benchmark_plan(
            cases,
            [args.model],
            1,
            None,
            legacy_budget.ceiling,
            legacy_budget.per_call,
            automatic=False,
        )
        if not plan["fits_budget"] or legacy_catalog is None:
            raise ValueError(
                "Legacy live evaluation requires a fully reserved plan and prices"
            )
        price = legacy_catalog.models.get(args.model)
        if (
            price is None
            or legacy_catalog.simulated
            or any(value is None for value in price.model_dump().values())
        ):
            raise ValueError(
                "Legacy live evaluation requires non-simulated known pricing"
            )
        require_public_cases(cases)
    settings = get_settings()
    providers = create_providers(settings)

    if args.langsmith:
        if not settings.langsmith_api_key:
            raise RuntimeError("LANGSMITH_API_KEY is required with --langsmith")
        provider_name = args.model.partition("/")[0]
        provider = providers.get(provider_name)
        if provider is None:
            raise RuntimeError(f"Provider '{provider_name}' is not configured")

        async def target(inputs: dict) -> dict:
            if legacy_budget and not legacy_budget.reserve():
                raise RuntimeError("Budget halted before the next request")
            response = await provider.complete(
                ChatCompletionRequest(
                    model=args.model,
                    messages=inputs["messages"],
                )
            )
            if legacy_budget:
                usage = observed_usage(response.model_dump())
                legacy_budget.observe(catalog_cost(legacy_catalog, args.model, *usage))
            return {"output": response.choices[0].message.content or ""}

        await LangSmithExperimentRunner(Client(api_key=settings.langsmith_api_key)).run(
            cases=cases, model=args.model, target=target
        )
        return

    report = await run_evaluation(
        cases=cases,
        model=args.model,
        providers=providers,
        budget=legacy_budget,
        price_catalog=legacy_catalog,
    )
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
