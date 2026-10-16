import argparse
import asyncio
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
from langsmith import Client, tracing_context

from app.config import Settings, get_settings
from app.evaluation.faults import FaultContext, FaultInjectingMockProvider
from app.evaluation.langsmith_experiment import LangSmithExperimentRunner
from app.evaluation.models import FaultScenario
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
    parser.add_argument(
        "--langsmith",
        action="store_true",
        help="Run and publish the experiment through LangSmith",
    )
    args = parser.parse_args()
    if args.fault_scenario:
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
    return args


async def benchmark_command(args, cases):
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
    if args.benchmark:
        report = await benchmark_command(args, cases)
        rendered = report.model_dump_json(indent=2)
        if args.output:
            if args.fault_scenario:
                with args.output.open("x", encoding="utf-8") as output:
                    output.write(rendered + "\n")
            else:
                args.output.write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
        return
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
            response = await provider.complete(
                ChatCompletionRequest(
                    model=args.model,
                    messages=inputs["messages"],
                )
            )
            return {"output": response.choices[0].message.content or ""}

        await LangSmithExperimentRunner(Client(api_key=settings.langsmith_api_key)).run(
            cases=cases, model=args.model, target=target
        )
        return

    report = await run_evaluation(
        cases=cases,
        model=args.model,
        providers=providers,
    )
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
