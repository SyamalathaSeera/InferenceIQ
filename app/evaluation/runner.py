import json
from pathlib import Path
from time import perf_counter

from app.evaluation.evaluators import aggregate_score, evaluate_output
from app.evaluation.models import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationReport,
)
from app.providers.base import ChatProvider, ProviderError
from app.schemas.chat import ChatCompletionRequest


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
