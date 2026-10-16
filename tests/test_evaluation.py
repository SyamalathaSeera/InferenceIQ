import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.evaluation.langsmith_experiment import LangSmithExperimentRunner
from app.evaluation.models import EvaluationCase
from app.evaluation.runner import load_dataset, run_evaluation
from app.providers.mock import MockProvider


def cases() -> list[EvaluationCase]:
    return [
        EvaluationCase(
            id="exact",
            messages=[{"role": "user", "content": "Hello"}],
            expected_exact="mock response: hello",
        ),
        EvaluationCase(
            id="contains",
            messages=[{"role": "user", "content": "Inference routing"}],
            required_substrings=["inference", "routing"],
            forbidden_substrings=["failure"],
        ),
    ]


def test_local_evaluation_produces_aggregate_report() -> None:
    report = asyncio.run(
        run_evaluation(
            cases=cases(),
            model="mock/fast",
            providers={"mock": MockProvider()},
        )
    )

    assert report.total_cases == 2
    assert report.passed_cases == 2
    assert report.pass_rate == 1
    assert report.average_score == 1
    assert report.total_input_tokens > 0


def test_sample_dataset_is_valid() -> None:
    dataset = load_dataset(Path("evaluations/smoke.json"))

    assert len(dataset) == 2


class FakeLangSmithClient:
    def __init__(self) -> None:
        self.examples = None
        self.evaluation = None

    def create_dataset(self, **kwargs):
        return SimpleNamespace(id="dataset-id", name=kwargs["dataset_name"])

    def create_examples(self, **kwargs):
        self.examples = kwargs["examples"]

    async def aevaluate(self, target, **kwargs):
        self.evaluation = kwargs
        output = await target({"messages": [{"role": "user", "content": "Hello"}]})
        assert "output" in output
        return {"experiment": "created"}


def test_langsmith_runner_creates_dataset_and_experiment() -> None:
    client = FakeLangSmithClient()

    async def target(inputs: dict) -> dict:
        return {"output": inputs["messages"][0]["content"]}

    result = asyncio.run(
        LangSmithExperimentRunner(client).run(
            cases=cases(),
            model="mock/fast",
            target=target,
        )
    )

    assert result == {"experiment": "created"}
    assert len(client.examples) == 2
    assert client.evaluation["metadata"]["models"] == ["mock/fast"]
    assert len(client.evaluation["evaluators"]) == 3
