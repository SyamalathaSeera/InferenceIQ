import asyncio
import inspect
import json
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from langsmith import Client
from langsmith.evaluation.evaluator import EvaluationResult
from pydantic import ValidationError

from app.evaluation import cli
from app.evaluation.evaluators import (
    aggregate_score,
    evaluate_output,
    langsmith_exact_match,
    langsmith_forbidden_substrings,
    langsmith_required_substrings,
)
from app.evaluation.langsmith_experiment import LangSmithExperimentRunner
from app.evaluation.models import EvaluationCase, PriceCatalog
from app.evaluation.quality import BudgetGuard, semantic_evaluate
from app.evaluation.runner import load_dataset, run_benchmark


def case(**overrides):
    return EvaluationCase.model_validate(
        {
            "id": "public-case",
            "messages": [{"role": "user", "content": "Question"}],
            "expected_exact": "answer",
            **overrides,
        }
    )


def prices(**overrides):
    return PriceCatalog.model_validate(
        {
            "version": "unit-price-v1",
            "source": "unit test",
            "simulated": True,
            "models": {
                "mock/fast": {
                    "input_cost_per_million": 1,
                    "output_cost_per_million": 2,
                },
                "mock/quality": {
                    "input_cost_per_million": 3,
                    "output_cost_per_million": 4,
                },
            },
            **overrides,
        }
    )


def payload(answer="answer", model="mock/fast", usage=True):
    return {
        "id": "unit",
        "created": 0,
        "model": model,
        "provider": "mock",
        "metadata": {"usage_available": usage, "simulated": True},
        "choices": [
            {"index": 0, "message": {"content": answer}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        "inference_receipt": {
            "selected_model": model,
            "selected_provider": "mock",
            "policy": "explicit",
            "reason": "unit",
            "candidates": [
                {
                    "model": model,
                    "provider": "mock",
                    "eligible": True,
                    "input_cost_per_million": 999,
                    "output_cost_per_million": 999,
                }
            ],
        },
    }


def run(handler, **options):
    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://unit"
        ) as client:
            return await run_benchmark(
                **{
                    "cases": [case()],
                    "fixed_models": ["mock/fast"],
                    "client": client,
                    "quality_mode": True,
                    "price_catalog": prices(),
                    **options,
                }
            )

    return asyncio.run(exercise())


def test_versioned_dataset_has_sixty_diverse_cases_with_correct_references():
    cases = load_dataset(Path("evaluations/quality.v1.json"))
    assert len(cases) == len({item.id for item in cases}) == 60
    assert len({item.category for item in cases}) == 10
    assert all(item.dataset_version == "general-quality-v1" for item in cases)
    assert sum(bool(item.semantic_rubric) for item in cases) == 6
    for item in cases:
        scores = evaluate_output(item, item.reference_answer)
        assert aggregate_score(scores) == (None if item.semantic_rubric else 1)


@pytest.mark.parametrize(
    "output,expected",
    [
        ("0", 1),
        ("0.0", 1),
        ("0e2", 1),
        ("answer: 0", 0),
        ("NaN", 0),
        ("true", 0),
        ("1", 0),
    ],
)
def test_numeric_evaluator_requires_a_complete_finite_number(output, expected):
    assert evaluate_output(case(expected_exact=None, expected_number=0), output) == {
        "number": expected
    }


def test_json_evaluator_checks_structure_and_distinguishes_boolean_and_number():
    item = case(expected_exact=None, expected_json={"a": 1, "b": [2]})
    assert evaluate_output(item, '{"b":[2],"a":1}') == {"json": 1}
    assert evaluate_output(item, '{"b":[2],"a":true}') == {"json": 0}
    assert evaluate_output(item, '{"b":[2],"a":1,"extra":0}') == {"json": 0}
    assert evaluate_output(item, "not json") == {"json": 0}


def test_absent_assertions_and_langsmith_checks_never_get_a_pass():
    with pytest.raises(ValidationError, match="assertion"):
        case(expected_exact=None)
    assert aggregate_score({}) is None
    for evaluator in (
        langsmith_exact_match,
        langsmith_required_substrings,
        langsmith_forbidden_substrings,
    ):
        assert evaluator({"output": "anything"}, {})["score"] is None
    assert EvaluationResult(key="not_applicable", score=None).score is None


def test_costs_use_versioned_prices_and_compare_every_chosen_baseline():
    def handler(request):
        body = json.loads(request.content)
        correct = body["model"] != "auto" or body["inferenceiq"]["policy"] == "quality"
        model = "mock/quality" if body["model"] == "mock/quality" else "mock/fast"
        return httpx.Response(
            200, json=payload("answer" if correct else "wrong", model)
        )

    report = run(handler, fixed_models=["mock/fast", "mock/quality"])
    assert len(report.quality_comparisons) == 10
    baseline = report.arms[0]
    assert baseline.cost_per_request_usd == 0.00005
    assert baseline.cost_per_quality_passing_response_usd == 0.00005
    assert report.arms[1].cost_per_request_usd == 0.00011
    assert all(row.pricing_version == "unit-price-v1" for row in report.results)
    assert all(row.pricing_matches_routing is False for row in report.results)
    failed_quality = next(arm for arm in report.arms if arm.arm == "auto:cost")
    assert failed_quality.quality_pass_rate == 0
    assert failed_quality.cost_per_quality_passing_response_usd is None
    comparison = next(
        row
        for row in report.quality_comparisons
        if row.baseline_arm == baseline.arm and row.comparison_arm == "auto:cost"
    )
    assert comparison.quality_pass_rate_difference_pp == -100


@pytest.mark.parametrize("missing", ["pricing", "usage", "quality"])
def test_missing_measurements_stay_unknown(missing):
    catalog = prices(models={}) if missing == "pricing" else prices()
    item = (
        case(
            expected_exact=None,
            reference_answer="Reference",
            semantic_rubric="Correct meaning",
        )
        if missing == "quality"
        else case()
    )
    report = run(
        lambda request: httpx.Response(200, json=payload(usage=missing != "usage")),
        cases=[item],
        price_catalog=catalog,
    )
    if missing == "quality":
        assert all(arm.quality_pass_rate is None for arm in report.arms)
        assert all(arm.quality_unknown_requests == 1 for arm in report.arms)
        assert all(
            row.quality_missing_reason == "semantic_disabled" for row in report.results
        )
    else:
        assert all(arm.cost_per_request_usd is None for arm in report.arms)
        assert all(row.request_cost_usd is None for row in report.results)
    assert all(arm.cost_per_quality_passing_response_usd is None for arm in report.arms)


def test_configurable_semantic_judge_uses_identical_parameters_and_separate_cost():
    observed = []
    active = peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        body = json.loads(request.content)
        observed.append(body)
        await asyncio.sleep(0)
        judged = body["model"] == "mock/quality"
        active -= 1
        return httpx.Response(
            200,
            json=payload(
                '{"score":0.8}' if judged else "PRIVATE_ANSWER",
                body["model"] if judged else "mock/fast",
            ),
        )

    item = case(
        expected_exact=None,
        reference_answer="PRIVATE_REFERENCE",
        semantic_rubric="Evaluate meaning",
    )
    report = run(
        handler,
        cases=[item],
        semantic_judge_model="mock/quality",
        quality_threshold=0.75,
    )
    assert peak == 1
    assert len(observed) == 10
    assert report.judge_requests == 5
    assert report.total_judge_cost_usd == pytest.approx(5 * 0.00011)
    assert all(arm.quality_pass_rate == 1 for arm in report.arms)
    assert all(row.quality_passed is True for row in report.results)
    assert all(
        body["temperature"] == 0 and body["max_tokens"] == 128
        for body in observed
        if body["model"] == "mock/quality"
    )
    assert "PRIVATE_ANSWER" not in report.model_dump_json()
    assert "PRIVATE_REFERENCE" not in report.model_dump_json()


@pytest.mark.parametrize("score", [True, "1", 2, -1])
def test_invalid_judge_scores_are_unknown(score):
    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json=payload(json.dumps({"score": score}))
                )
            ),
            base_url="http://unit",
        ) as client:
            return await semantic_evaluate(
                case(),
                "answer",
                client=client,
                model="mock/fast",
                catalog=prices(),
                budget=None,
            )

    score, cost, reason, requested = asyncio.run(exercise())
    assert score is None and reason == "judge_unavailable_or_invalid" and requested
    assert cost == 0.00005


def test_budget_halts_on_unknown_cost_without_fake_zero_latency():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload(usage=False))

    report = run(handler, budget=BudgetGuard(1, 0.01))
    assert len(calls) == 1
    assert report.budget_halted
    skipped = [row for row in report.results if not row.submitted]
    assert len(skipped) == 4
    assert all(row.latency_ms is row.request_cost_usd is None for row in skipped)
    assert all(row.error == "budget_halted" for row in skipped)
    assert all(
        comparison.cost_per_request_difference_usd is None
        for comparison in report.quality_comparisons
    )


def test_budget_ceiling_and_over_reservation_cost_stop_further_calls():
    guard = BudgetGuard(0.02, 0.01)
    assert guard.reserve() and guard.reserve() and not guard.reserve()
    assert guard.reserved == 0.02
    small = BudgetGuard(1e-14, 1e-14)
    assert small.reserve() and not small.reserve()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload())

    report = run(handler, budget=BudgetGuard(1, 0.00001))
    assert len(calls) == 1 and report.budget_halted


def test_dry_run_never_constructs_clients_or_runs_providers(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("Dry-run must not execute")

    monkeypatch.setattr(cli, "Client", forbidden)
    monkeypatch.setattr(cli, "benchmark_command", forbidden)
    monkeypatch.setattr(cli, "create_providers", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluation",
            "--dataset",
            "evaluations/quality.v1.json",
            "--benchmark",
            "--fixed-models",
            "mock/fast",
            "--dry-run",
            "--publish-langsmith",
            "--semantic-judge-model",
            "mock/quality",
            "--budget-usd",
            "5",
            "--request-budget-usd",
            "0.01",
        ],
    )
    asyncio.run(cli.main())
    plan = json.loads(capsys.readouterr().out)
    assert plan["inference_requests"] == 300
    assert plan["maximum_judge_requests"] == 30
    assert plan["maximum_reserved_usd"] == pytest.approx(3.3)
    assert plan["fits_budget"]


def test_live_cli_requires_approval_budget_and_versioned_prices(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluation",
            "--dataset",
            "evaluations/quality.v1.json",
            "--benchmark",
            "--fixed-models",
            "openai/example",
        ],
    )
    with pytest.raises(SystemExit):
        cli.parse_args()


class FakeSDK:
    def __init__(self):
        self.examples = []
        self.calls = []
        self.created = 0

    def has_dataset(self, **kwargs):
        return bool(self.created)

    def read_dataset(self, **kwargs):
        return SimpleNamespace(id="unit-dataset")

    def create_dataset(self, **kwargs):
        self.created += 1
        self.calls.append(kwargs)
        return self.read_dataset()

    def create_examples(self, **kwargs):
        self.examples = kwargs["examples"]
        self.calls.append(kwargs)

    def list_examples(self, **kwargs):
        return [SimpleNamespace(inputs=example["inputs"]) for example in self.examples]

    async def aevaluate(self, target, **kwargs):
        inspect.signature(Client.aevaluate).bind(self, target, **kwargs)
        outputs = [await target(example["inputs"]) for example in self.examples]
        self.calls.append(
            {**kwargs, "evaluators": "local function", "outputs": outputs}
        )
        return SimpleNamespace(experiment_name=kwargs["experiment_prefix"])


def test_langsmith_is_opt_in_reuses_dataset_and_sends_no_content():
    report = run(
        lambda request: httpx.Response(200, json=payload()),
        cases=[
            case(
                messages=[{"role": "user", "content": "PRIVATE_QUESTION"}],
                reference_answer="PRIVATE_REFERENCE",
            )
        ],
    )
    client = FakeSDK()
    runner = LangSmithExperimentRunner(client)
    assert asyncio.run(runner.publish_report(report)) == []
    assert client.calls == []
    first = asyncio.run(
        runner.publish_report(report, enabled=True, experiment_version="v1")
    )
    second = asyncio.run(
        runner.publish_report(report, enabled=True, experiment_version="v2")
    )
    assert len(first) == len(second) == 5 and client.created == 1
    assert first[0]["experiment_name"] != second[0]["experiment_name"]
    serialized = json.dumps(client.calls)
    assert "PRIVATE_QUESTION" not in serialized
    assert "PRIVATE_REFERENCE" not in serialized
    assert "mock/fast" not in serialized
    assert "messages" not in serialized


def test_preflight_rejects_an_underfunded_plan_before_http(tmp_path, monkeypatch):
    path = tmp_path / "prices.json"
    path.write_text(prices().model_dump_json(), encoding="utf-8")
    args = Namespace(
        mock_smoke=False,
        allow_live=True,
        price_catalog=path,
        quality=True,
        budget_usd=0.01,
        request_budget_usd=0.01,
        fixed_models=["mock/fast"],
        repetitions=1,
        concurrency=1,
        max_attempts=1,
        temperature=0,
        max_tokens=32,
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: pytest.fail("No HTTP allowed")
    )
    with pytest.raises(ValueError, match="does not fit"):
        asyncio.run(cli.benchmark_command(args, [case()]))


def test_live_preflight_rejects_unreviewed_data_before_http(tmp_path, monkeypatch):
    path = tmp_path / "prices.json"
    path.write_text(prices().model_dump_json(), encoding="utf-8")
    args = Namespace(
        mock_smoke=False,
        allow_live=True,
        price_catalog=path,
        quality=True,
        budget_usd=1,
        request_budget_usd=0.01,
        fixed_models=["mock/fast"],
        repetitions=1,
        concurrency=1,
        max_attempts=1,
        temperature=0,
        max_tokens=32,
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: pytest.fail("No HTTP allowed")
    )
    with pytest.raises(ValueError, match="bundled public datasets"):
        asyncio.run(cli.benchmark_command(args, [case()]))
