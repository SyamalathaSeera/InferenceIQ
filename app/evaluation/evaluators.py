from app.evaluation.models import EvaluationCase


def evaluate_output(case: EvaluationCase, output: str) -> dict[str, float]:
    scores: dict[str, float] = {}
    normalized_output = _normalize(output)

    if case.expected_exact is not None:
        scores["exact_match"] = float(
            normalized_output == _normalize(case.expected_exact)
        )

    if case.required_substrings:
        scores["required_substrings"] = float(
            all(
                _normalize(required) in normalized_output
                for required in case.required_substrings
            )
        )

    if case.forbidden_substrings:
        scores["forbidden_substrings_absent"] = float(
            all(
                _normalize(forbidden) not in normalized_output
                for forbidden in case.forbidden_substrings
            )
        )

    return scores


def aggregate_score(scores: dict[str, float]) -> float:
    return sum(scores.values()) / len(scores) if scores else 0.0


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def langsmith_exact_match(
    outputs: dict,
    reference_outputs: dict,
) -> dict[str, float | str]:
    expected = reference_outputs.get("expected_exact")
    if expected is None:
        return {"key": "exact_match", "score": 1.0}
    score = float(_normalize(outputs["output"]) == _normalize(expected))
    return {"key": "exact_match", "score": score}


def langsmith_required_substrings(
    outputs: dict,
    reference_outputs: dict,
) -> dict[str, float | str]:
    required = reference_outputs.get("required_substrings", [])
    normalized_output = _normalize(outputs["output"])
    score = float(all(_normalize(item) in normalized_output for item in required))
    return {"key": "required_substrings", "score": score}


def langsmith_forbidden_substrings(
    outputs: dict,
    reference_outputs: dict,
) -> dict[str, float | str]:
    forbidden = reference_outputs.get("forbidden_substrings", [])
    normalized_output = _normalize(outputs["output"])
    score = float(all(_normalize(item) not in normalized_output for item in forbidden))
    return {"key": "forbidden_substrings_absent", "score": score}
