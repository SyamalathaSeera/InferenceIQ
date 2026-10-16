from datetime import UTC, datetime
from typing import Any

from langsmith import Client

from app.evaluation.evaluators import (
    langsmith_exact_match,
    langsmith_forbidden_substrings,
    langsmith_required_substrings,
)
from app.evaluation.models import EvaluationCase


class LangSmithExperimentRunner:
    def __init__(self, client: Client) -> None:
        self.client = client

    async def run(
        self,
        *,
        cases: list[EvaluationCase],
        model: str,
        target,
    ) -> Any:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        dataset_name = f"InferenceIQ-{model.replace('/', '-')}-{timestamp}"
        dataset = self.client.create_dataset(
            dataset_name=dataset_name,
            description="Curated InferenceIQ offline evaluation dataset.",
        )
        self.client.create_examples(
            dataset_id=dataset.id,
            examples=[
                {
                    "inputs": {
                        "messages": [
                            message.model_dump(exclude_none=True)
                            for message in case.messages
                        ]
                    },
                    "outputs": {
                        "expected_exact": case.expected_exact,
                        "required_substrings": case.required_substrings,
                        "forbidden_substrings": case.forbidden_substrings,
                    },
                    "metadata": {"case_id": case.id},
                }
                for case in cases
            ],
        )
        return await self.client.aevaluate(
            target,
            data=dataset_name,
            evaluators=[
                langsmith_exact_match,
                langsmith_required_substrings,
                langsmith_forbidden_substrings,
            ],
            experiment_prefix=f"InferenceIQ-{model.replace('/', '-')}",
            metadata={"models": [model], "source": "inferenceiq-cli"},
            max_concurrency=2,
        )
