import json
import math
import re

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

    if case.expected_number is not None:
        numeric = re.fullmatch(
            r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", output.strip()
        )
        scores["number"] = float(
            bool(numeric)
            and math.isclose(
                float(output),
                case.expected_number,
                rel_tol=0,
                abs_tol=case.number_tolerance,
            )
        )

    if case.expected_json is not None:
        try:
            actual = json.loads(output)
            scores["json"] = float(
                json.dumps(actual, sort_keys=True)
                == json.dumps(case.expected_json, sort_keys=True)
            )
        except (ValueError, TypeError):
            scores["json"] = 0.0

    return scores


def aggregate_score(scores: dict[str, float]) -> float | None:
    return sum(scores.values()) / len(scores) if scores else None


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def langsmith_exact_match(
    outputs: dict,
    reference_outputs: dict,
) -> dict[str, float | str | None]:
    expected = reference_outputs.get("expected_exact")
    if expected is None:
        return {"key": "exact_match", "score": None}
    score = float(_normalize(outputs["output"]) == _normalize(expected))
    return {"key": "exact_match", "score": score}


def langsmith_required_substrings(
    outputs: dict,
    reference_outputs: dict,
) -> dict[str, float | str | None]:
    required = reference_outputs.get("required_substrings", [])
    if not required:
        return {"key": "required_substrings", "score": None}
    normalized_output = _normalize(outputs["output"])
    score = float(all(_normalize(item) in normalized_output for item in required))
    return {"key": "required_substrings", "score": score}


def langsmith_forbidden_substrings(
    outputs: dict,
    reference_outputs: dict,
) -> dict[str, float | str | None]:
    forbidden = reference_outputs.get("forbidden_substrings", [])
    if not forbidden:
        return {"key": "forbidden_substrings_absent", "score": None}
    normalized_output = _normalize(outputs["output"])
    score = float(all(_normalize(item) not in normalized_output for item in forbidden))
    return {"key": "forbidden_substrings_absent", "score": score}


def langsmith_recorded_quality(outputs: dict, reference_outputs: dict) -> dict:
    """Replay locally computed scores without uploading prompts or answers."""
    return {"key": "quality", "score": outputs.get("quality_score")}
