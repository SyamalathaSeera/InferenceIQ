import argparse
import asyncio
from pathlib import Path

from langsmith import Client

from app.config import get_settings
from app.evaluation.langsmith_experiment import LangSmithExperimentRunner
from app.evaluation.runner import load_dataset, run_evaluation
from app.providers.factory import create_providers
from app.schemas.chat import ChatCompletionRequest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an InferenceIQ evaluation")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model", required=True, help="provider/model identifier")
    parser.add_argument(
        "--langsmith",
        action="store_true",
        help="Run and publish the experiment through LangSmith",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    settings = get_settings()
    cases = load_dataset(args.dataset)
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
