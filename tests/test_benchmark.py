import asyncio
import json
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage

from app.config import Settings
from app.evaluation.cli import benchmark_command, parse_args
from app.evaluation.models import BenchmarkCaseResult, EvaluationCase
from app.evaluation.runner import (
    load_dataset,
    run_benchmark,
    summarize_benchmark,
)
from app.main import create_app
from app.providers.base import ProviderError
from app.providers.langchain_anthropic import LangChainAnthropicProvider
from app.providers.langchain_ollama import LangChainOllamaProvider
from app.providers.mock import MockProvider
from app.providers.openai import OpenAIProvider
from app.schemas.chat import ChatCompletionRequest


def dataset():
    return load_dataset(Path("evaluations/smoke.json"))


def smoke_args(**overrides):
    values = dict(
        mock_smoke=True,
        fixed_models=["mock/fast"],
        repetitions=2,
        concurrency=1,
        temperature=0,
        max_tokens=32,
        max_attempts=1,
        timeout=10,
    )
    return Namespace(**(values | overrides))


@pytest.mark.parametrize("concurrency", [1, 2])
def test_smoke_exercises_all_policies_and_repeats(concurrency):
    report = asyncio.run(
        benchmark_command(smoke_args(concurrency=concurrency), dataset())
    )
    assert len(report.results) == 20
    assert [arm.arm for arm in report.arms] == [
        "fixed:mock/fast",
        "auto:cost",
        "auto:latency",
        "auto:quality",
        "auto:balanced",
    ]
    assert report.generation_parameters == {"temperature": 0, "max_tokens": 32}
    for arm in report.arms:
        assert arm.requests == arm.successes == arm.simulated_requests == 4
        assert arm.success_rate == arm.quality_pass_rate == 1
        assert arm.total_cost_usd is not None
        assert arm.p95_latency_ms >= arm.p50_latency_ms > 0
    selected = {row.arm: row.selected_model for row in report.results}
    assert selected["auto:cost"] == "mock/fast"
    assert selected["auto:quality"] == "mock/quality"
    assert selected["auto:latency"] == "mock/fast"
    assert selected["auto:balanced"] == "mock/balanced"
    assert all(row.gateway_attempts == 1 for row in report.results)


def test_benchmark_aggregation_includes_failures_without_fabricated_totals():
    rows = [
        BenchmarkCaseResult(
            arm="fixed:mock/fast",
            case_id="a",
            repetition=1,
            requested_model="mock/fast",
            success=True,
            latency_ms=10,
            input_tokens=2,
            output_tokens=3,
            quality_score=1,
            completed_call_cost_usd=0.01,
            request_cost_usd=0.01,
        ),
        BenchmarkCaseResult(
            arm="fixed:mock/fast",
            case_id="b",
            repetition=1,
            requested_model="mock/fast",
            latency_ms=30,
            error="http_503",
        ),
    ]
    summary = summarize_benchmark("fixed:mock/fast", rows)
    assert summary.success_rate == summary.quality_pass_rate == 0.5
    assert summary.average_quality_score == 1
    assert summary.p50_latency_ms == 20
    assert summary.p95_latency_ms == 29
    assert summary.successful_p95_latency_ms == 10
    assert summary.total_cost_usd is summary.total_input_tokens is None
    assert summary.known_completed_call_cost_usd == 0.01
    assert summary.cost_known_requests == summary.usage_known_requests == 1


class UnavailableProvider(MockProvider):
    async def complete(self, request):
        if request.model == "mock/quality":
            raise ProviderError("unavailable", category="unavailable", status_code=503)
        return await super().complete(request)


def test_endpoint_failure_does_not_abort_benchmark_and_fallback_is_observable():
    async def exercise():
        app = create_app(
            Settings(
                _env_file=None,
                database_url="sqlite+aiosqlite:///:memory:",
                langgraph_checkpoint_url=":memory:",
            )
        )
        async with app.router.lifespan_context(app):
            app.state.providers = {"mock": UnavailableProvider()}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                report = await run_benchmark(
                    cases=dataset(),
                    fixed_models=["mock/quality"],
                    client=client,
                    max_attempts=2,
                )
        return report

    report = asyncio.run(exercise())
    fixed = [row for row in report.results if row.arm == "fixed:mock/quality"]
    assert all(row.http_status == 503 and not row.success for row in fixed)
    assert all(
        row.error == "http_503" and row.gateway_attempts is None for row in fixed
    )
    assert any(row.success for row in report.results)
    recovered = next(row for row in report.results if row.gateway_attempts == 2)
    assert recovered.completed_call_cost_usd is not None
    assert recovered.request_cost_usd is None


def response_payload(*, usage_available=True, pricing=True):
    return {
        "id": "test",
        "created": 0,
        "model": "mock/fast",
        "provider": "mock",
        "choices": [
            {"index": 0, "message": {"content": "Hello"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        "metadata": {"usage_available": usage_available, "simulated": True},
        "inference_receipt": {
            "selected_model": "mock/fast",
            "selected_provider": "mock",
            "policy": "explicit",
            "reason": "test",
            "candidates": [
                {
                    "model": "mock/fast",
                    "provider": "mock",
                    "eligible": True,
                    "input_cost_per_million": 1 if pricing else None,
                    "output_cost_per_million": 2 if pricing else None,
                }
            ],
        },
    }


@pytest.mark.parametrize("missing", ["pricing", "usage", "usage_flag", "usage_fields"])
def test_missing_cost_or_usage_is_never_zero(missing):
    payload = response_payload(
        pricing=missing != "pricing", usage_available=missing != "usage_flag"
    )
    if missing == "usage":
        del payload["usage"]
    if missing == "usage_fields":
        payload["usage"] = {}

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            ),
            base_url="http://test",
        ) as client:
            return await run_benchmark(
                cases=dataset(), fixed_models=["mock/fast"], client=client
            )

    report = asyncio.run(exercise())
    assert all(row.request_cost_usd is None for row in report.results)
    assert all(arm.total_cost_usd is None for arm in report.arms)
    if missing != "pricing":
        assert all(row.input_tokens is None for row in report.results)


def test_payload_parity_concurrency_and_pricing():
    payloads = []
    active = peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        payloads.append(json.loads(request.content))
        await asyncio.sleep(0)
        active -= 1
        return httpx.Response(200, json=response_payload())

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        ) as client:
            return await run_benchmark(
                cases=dataset(),
                fixed_models=["mock/fast", "mock/balanced"],
                client=client,
                repetitions=2,
                concurrency=3,
                temperature=0,
                max_tokens=17,
            )

    report = asyncio.run(exercise())
    assert peak == 3
    assert len(report.results) == 24
    assert {
        body["inferenceiq"]["policy"] for body in payloads if body["model"] == "auto"
    } == {"cost", "latency", "quality", "balanced"}
    assert all(
        body["temperature"] == 0 and body["max_tokens"] == 17 for body in payloads
    )
    assert all(row.request_cost_usd == 0.00005 for row in report.results)


@pytest.mark.parametrize("failure", ["timeout", "http", "malformed"])
def test_request_errors_are_sanitized_and_all_requests_continue(failure):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("do not expose request details", request=request)
        if failure == "http":
            return httpx.Response(
                429, json={"detail": "do not expose response details"}
            )
        return httpx.Response(200, text="invalid json")

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        ) as client:
            return await run_benchmark(
                cases=dataset(), fixed_models=["mock/fast"], client=client
            )

    report = asyncio.run(exercise())
    assert len(report.results) == 10
    assert all(arm.success_rate == 0 for arm in report.arms)
    assert "do not expose" not in report.model_dump_json()
    assert all(
        row.quality_score is row.request_cost_usd is None for row in report.results
    )


def test_cli_preserves_legacy_and_accepts_smoke(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["evaluation", "--dataset", "evaluations/smoke.json", "--model", "mock/fast"],
    )
    assert not parse_args().benchmark
    monkeypatch.setattr(
        sys,
        "argv",
        ["evaluation", "--dataset", "evaluations/smoke.json", "--mock-smoke"],
    )
    assert parse_args().fixed_models == ["mock/fast"]


def test_benchmark_rejects_duplicate_cases_before_requests():
    async def exercise():
        async with httpx.AsyncClient(base_url="http://test") as client:
            case = EvaluationCase(
                id="same",
                messages=[{"role": "user", "content": "Hi"}],
                required_substrings=["Hi"],
            )
            with pytest.raises(ValueError, match="unique"):
                await run_benchmark(
                    cases=[case, case], fixed_models=["mock/fast"], client=client
                )

    asyncio.run(exercise())


@pytest.mark.parametrize("adapter", ["anthropic", "ollama", "openai"])
@pytest.mark.parametrize("available", [True, False])
def test_adapters_forward_parameters_and_mark_missing_usage(
    adapter, available, monkeypatch
):
    captured = {}

    async def ainvoke(messages, **kwargs):
        captured.update(kwargs)
        return AIMessage(
            content="Hello",
            usage_metadata=(
                {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
                if available
                else None
            ),
        )

    if adapter == "anthropic":
        provider = LangChainAnthropicProvider(
            api_key="unused",
            default_model="test",
            model_factory=lambda model: SimpleNamespace(ainvoke=ainvoke),
        )
    elif adapter == "ollama":
        provider = LangChainOllamaProvider(
            base_url="http://unused",
            default_model="test",
            model_factory=lambda model: SimpleNamespace(ainvoke=ainvoke),
        )
    else:

        async def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                id="test",
                created=0,
                model="test",
                choices=[
                    SimpleNamespace(
                        index=0,
                        message=SimpleNamespace(content="Hello"),
                        finish_reason="stop",
                    )
                ],
                usage=(
                    SimpleNamespace(
                        prompt_tokens=2, completion_tokens=3, total_tokens=5
                    )
                    if available
                    else None
                ),
            )

        monkeypatch.setattr(
            "app.providers.openai.AsyncOpenAI",
            lambda **kwargs: SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
        )
        provider = OpenAIProvider(api_key="unused", default_model="test")

    response = asyncio.run(
        provider.complete(
            ChatCompletionRequest(
                model=f"{adapter}/test",
                messages=[{"role": "user", "content": "Hello"}],
                temperature=0,
                max_tokens=17,
            )
        )
    )
    generation = captured["options"] if adapter == "ollama" else captured
    assert generation["temperature"] == 0
    assert generation["num_predict" if adapter == "ollama" else "max_tokens"] == 17
    assert response.metadata["usage_available"] is available


def test_unknown_explicit_model_does_not_get_free_pricing():
    from app.inference.engine import InferenceDecisionEngine
    from app.inference.models import InferenceOptions, ModelProfile

    decision = InferenceDecisionEngine(
        [
            ModelProfile(
                model_id="mock/fast",
                provider="mock",
                quality_score=0.5,
                estimated_latency_ms=100,
                input_cost_per_million=1,
                output_cost_per_million=2,
            )
        ]
    ).decide(requested_model="mock/unknown", options=InferenceOptions())
    assert decision.model.input_cost_per_million is None
    assert decision.model.output_cost_per_million is None


def test_observed_zero_usage_and_cost_remain_known():
    payload = response_payload()
    payload["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            ),
            base_url="http://test",
        ) as client:
            return await run_benchmark(
                cases=dataset(), fixed_models=["mock/fast"], client=client
            )

    report = asyncio.run(exercise())
    assert all(
        row.input_tokens == row.output_tokens == row.request_cost_usd == 0
        for row in report.results
    )
    assert all(
        arm.total_cost_usd == 0 and arm.cost_known_requests == 2 for arm in report.arms
    )


def test_cli_rejects_benchmark_uploads(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluation",
            "--dataset",
            "evaluations/smoke.json",
            "--mock-smoke",
            "--langsmith",
        ],
    )
    with pytest.raises(SystemExit):
        parse_args()
    assert "do not support external LangSmith uploads" in capsys.readouterr().err
