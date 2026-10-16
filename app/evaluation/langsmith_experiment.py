import re
from datetime import UTC, datetime
from typing import Any

from langsmith import Client, tracing_context

from app.evaluation.evaluators import (
    aggregate_score,
    evaluate_output,
    langsmith_recorded_quality,
)
from app.evaluation.models import BenchmarkReport, EvaluationCase
from app.evaluation.quality import fingerprint


def _metric_target(lookup):
    async def target(inputs):
        row = lookup[(inputs["case_hash"], inputs["repetition"])]
        return {
            "quality_score": row.quality_score,
            "quality_passed": row.quality_passed,
            "success": row.success,
            "latency_ms": row.latency_ms,
            "cost_usd": row.request_cost_usd,
            "simulated": row.simulated,
        }

    return target


class LangSmithExperimentRunner:
    def __init__(self, client: Client) -> None:
        self.client = client

    async def publish_report(
        self,
        report: BenchmarkReport,
        *,
        enabled: bool = False,
        experiment_version: str = "v1",
    ) -> list[dict[str, str | None]]:
        """Opt-in replay: only hashed case IDs and scalar metrics leave the machine."""
        if not enabled:
            return []
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", experiment_version):
            raise ValueError("Experiment version must be a short public version label")
        identities = sorted(
            {(fingerprint(row.case_id), row.repetition) for row in report.results}
        )
        manifest = fingerprint([report.dataset_sha256, identities])
        name = f"InferenceIQ-metrics-{manifest}"
        if self.client.has_dataset(dataset_name=name):
            dataset = self.client.read_dataset(dataset_name=name)
            actual = sorted(
                (example.inputs.get("case_hash"), example.inputs.get("repetition"))
                for example in self.client.list_examples(dataset_id=dataset.id)
            )
            if actual != identities:
                raise ValueError("Existing LangSmith dataset manifest does not match")
        else:
            dataset = self.client.create_dataset(
                dataset_name=name,
                description="Hashed case IDs and metric-only benchmark replay.",
            )
            self.client.create_examples(
                dataset_id=dataset.id,
                examples=[
                    {
                        "inputs": {"case_hash": key, "repetition": repetition},
                        "outputs": {},
                    }
                    for key, repetition in identities
                ],
            )
        experiments = []
        for index, arm in enumerate(report.arms):
            lookup = {
                (fingerprint(row.case_id), row.repetition): row
                for row in report.results
                if row.arm == arm.arm
            }

            result = await self.client.aevaluate(
                _metric_target(lookup),
                data=name,
                evaluators=[langsmith_recorded_quality],
                experiment_prefix=f"InferenceIQ-{experiment_version}-arm-{index}",
                metadata={
                    "experiment_version": experiment_version,
                    "dataset_sha256": report.dataset_sha256,
                    "evaluation_version": report.evaluation_version,
                    "price_catalog_sha256": report.price_catalog_sha256,
                    "quality_threshold": report.quality_threshold,
                    "generation_parameters": report.generation_parameters,
                    "max_attempts": report.max_attempts,
                    "semantic_judge_hash": fingerprint(report.semantic_judge_model)
                    if report.semantic_judge_model
                    else None,
                    "arm_hash": fingerprint(arm.arm),
                    "content_recorded": False,
                },
                max_concurrency=2,
                blocking=True,
            )
            experiments.append(
                {
                    "arm": arm.arm,
                    "experiment_name": getattr(result, "experiment_name", None),
                }
            )
        return experiments

    async def run(
        self,
        *,
        cases: list[EvaluationCase],
        model: str,
        target,
    ) -> Any:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        local_scores = {}
        with tracing_context(enabled=False):
            for case in cases:
                result = await target(
                    {
                        "messages": [
                            message.model_dump(exclude_none=True)
                            for message in case.messages
                        ]
                    }
                )
                local_scores[fingerprint(case.id)] = (
                    aggregate_score(evaluate_output(case, result.get("output", "")))
                    if not case.semantic_rubric
                    else None
                )
        dataset_name = f"InferenceIQ-legacy-{fingerprint(model)[:16]}-{timestamp}"
        dataset = self.client.create_dataset(
            dataset_name=dataset_name,
            description="Metric-only legacy evaluation replay; content remains local.",
        )
        self.client.create_examples(
            dataset_id=dataset.id,
            examples=[
                {
                    "inputs": {"case_hash": fingerprint(case.id)},
                    "outputs": {},
                }
                for case in cases
            ],
        )

        async def replay(inputs):
            return {"quality_score": local_scores[inputs["case_hash"]]}

        return await self.client.aevaluate(
            replay,
            data=dataset_name,
            evaluators=[langsmith_recorded_quality],
            experiment_prefix=f"InferenceIQ-legacy-{fingerprint(model)[:16]}",
            metadata={
                "model_hash": fingerprint(model),
                "source": "inferenceiq-cli",
                "content_recorded": False,
            },
            max_concurrency=2,
        )
