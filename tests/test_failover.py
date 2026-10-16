import asyncio
import json
import sys
from argparse import Namespace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.evaluation.cli import benchmark_command, parse_args
from app.evaluation.models import EvaluationCase, FaultScenario
from app.health import ProviderAttempt, record_provider_attempts
from app.main import create_app
from app.providers.mock import MockProvider


def run_scenario(tmp_path, scenario, *, requests=8, attempts=2):
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    args = Namespace(
        mock_smoke=True,
        fault_scenario=path,
        fixed_models=["mock/fast"],
        repetitions=requests,
        concurrency=1,
        temperature=0,
        max_tokens=16,
        max_attempts=attempts,
        timeout=10,
    )
    case = EvaluationCase(
        id="hello",
        messages=[{"role": "user", "content": "Hello"}],
        expected_exact="Mock response: Hello",
    )
    return asyncio.run(benchmark_command(args, [case]))


def scenario(
    error="unavailable", *, end=3, threshold=2, recovery_start=4, recovery_end=8
):
    return {
        "name": "test-failover",
        "circuit_failure_threshold": threshold,
        "circuit_cooldown_seconds": 3,
        "request_interval_seconds": 1,
        "faults": [{"start": 1, "end": end, "models": ["mock/fast"], "error": error}],
        "recovery_windows": [{"start": recovery_start, "end": recovery_end}],
    }


@pytest.mark.parametrize(
    "error,status",
    [
        ("rate_limit", 429),
        ("timeout", 504),
        ("unavailable", 503),
        ("authentication", 401),
    ],
)
def test_injected_errors_open_circuit_and_recover_on_cooldown(tmp_path, error, status):
    report = run_scenario(tmp_path, scenario(error))
    rows = [row for row in report.results if row.arm == "fixed:mock/fast"]
    assert rows[0].http_status == rows[0].attempt_details[0].status_code == status
    assert rows[1].circuit_after["mock/fast"] == "open"
    assert rows[2].gateway_attempts == rows[3].gateway_attempts == 0
    assert rows[4].attempt_details[0].circuit_state == "half_open"
    assert rows[4].success and rows[4].circuit_after["mock/fast"] == "closed"
    assert all(row.simulated is True for row in report.results)
    for arm in report.arms:
        selected = [row for row in report.results if row.arm == arm.arm]
        assert selected[0].circuit_before == {}
        assert [row.fault_request_index for row in selected] == list(range(1, 9))
        assert [row.fault_window for row in selected] == [True] * 3 + [False] * 5
    automatic = next(row for row in report.results if row.arm == "auto:cost")
    if error == "authentication":
        assert not automatic.success and automatic.gateway_attempts == 1
        assert automatic.attempt_details[0].retryable is False
        assert automatic.fallback_models == []
    else:
        assert automatic.success and automatic.gateway_attempts == 2
        assert automatic.fallback_models
        assert automatic.request_cost_usd is None


def test_failed_half_open_probe_reopens_then_healthy_probe_closes(tmp_path):
    report = run_scenario(
        tmp_path, scenario(end=6, recovery_start=7, recovery_end=10), requests=10
    )
    rows = [row for row in report.results if row.arm == "fixed:mock/fast"]
    assert rows[4].attempt_details[0].circuit_state == "half_open"
    assert not rows[4].success and rows[4].circuit_after["mock/fast"] == "open"
    assert rows[6].gateway_attempts == 0
    assert rows[7].attempt_details[0].circuit_state == "half_open"
    assert rows[7].success and rows[7].circuit_after["mock/fast"] == "closed"


@pytest.mark.parametrize("attempts", [1, 2])
def test_all_candidates_failing_respects_attempt_limit(tmp_path, attempts):
    config = scenario(end=1, recovery_start=2, recovery_end=2)
    config["faults"][0]["models"] = ["mock/fast", "mock/balanced", "mock/quality"]
    report = run_scenario(tmp_path, config, requests=2, attempts=attempts)
    rows = [row for row in report.results if row.fault_request_index == 1]
    assert all(not row.success for row in rows)
    assert all(
        row.gateway_attempts == (1 if row.arm.startswith("fixed:") else attempts)
        for row in rows
    )
    assert all(row.input_tokens is row.request_cost_usd is None for row in rows)


def test_automatic_routing_avoids_open_circuit(tmp_path):
    report = run_scenario(tmp_path, scenario(threshold=1))
    rows = [row for row in report.results if row.arm == "auto:cost"]
    assert rows[0].circuit_after["mock/fast"] == "open"
    assert rows[1].success and "mock/fast" in rows[1].avoided_open_models
    assert all(attempt.model != "mock/fast" for attempt in rows[1].attempt_details)
    assert report.arms[1].reliability.circuit_avoidance_successes > 0


def test_scenario_metrics_and_comparisons_use_observed_denominators(tmp_path):
    report = run_scenario(tmp_path, scenario())
    baseline = report.arms[0]
    assert baseline.reliability.total_gateway_attempts == 6
    assert baseline.reliability.circuit_blocked_requests == 2
    assert baseline.reliability.recovery_window_requests == 5
    assert baseline.reliability.recovery_success_rate == 4 / 5
    assert baseline.reliability.fallback_success_rate is None
    assert baseline.reliability.primary_failure_recovery_rate == 0
    assert baseline.reliability.half_open_probes == 1
    for comparison in report.reliability_comparisons:
        automatic = next(
            arm for arm in report.arms if arm.arm == comparison.automatic_arm
        )
        assert comparison.recovery_success_improvement_pp == pytest.approx(
            (automatic.reliability.recovery_success_rate - 4 / 5) * 100
        )
        assert comparison.success_improvement_pp == pytest.approx(
            (automatic.success_rate - baseline.success_rate) * 100
        )


def test_fault_schedule_and_outcomes_repeat_with_fresh_arm_state(tmp_path):
    first = run_scenario(tmp_path, scenario())
    second = run_scenario(tmp_path, scenario())
    assert [row.model_dump(exclude={"latency_ms"}) for row in first.results] == [
        row.model_dump(exclude={"latency_ms"}) for row in second.results
    ]


def test_scenario_rejects_ambiguous_windows_and_insufficient_requests(tmp_path):
    config = scenario()
    config["faults"].append(dict(config["faults"][0]))
    with pytest.raises(ValidationError, match="overlap"):
        FaultScenario.model_validate(config)
    config = scenario(recovery_start=3)
    with pytest.raises(ValidationError, match="fault-free"):
        FaultScenario.model_validate(config)
    with pytest.raises(ValueError, match="cover every"):
        run_scenario(tmp_path, scenario(), requests=2)


@pytest.mark.parametrize("extra", [[], ["--mock-smoke", "--concurrency", "2"]])
def test_cli_rejects_remote_or_concurrent_faults(monkeypatch, extra):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluation",
            "--dataset",
            "evaluations/smoke.json",
            "--fault-scenario",
            "evaluations/failover.json",
            *extra,
        ],
    )
    with pytest.raises(SystemExit):
        parse_args()


def test_fault_cli_refuses_to_overwrite_existing_output(monkeypatch, tmp_path):
    output = tmp_path / "preserved.json"
    output.write_text("preserve me", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluation",
            "--dataset",
            "evaluations/smoke.json",
            "--mock-smoke",
            "--fault-scenario",
            "evaluations/failover.json",
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit):
        parse_args()
    assert output.read_text(encoding="utf-8") == "preserve me"


def test_concurrent_half_open_probe_safety_gap(tmp_path):
    """Characterize the known gap; two callers can enter one half-open circuit.

    A future atomic, expiring per-model probe lease must replace this expectation.
    Fault benchmarks are serial until that cross-worker fix is implemented.
    """

    async def exercise():
        moment = datetime(2026, 1, 1, tzinfo=UTC)
        entered = 0
        both_entered = asyncio.Event()
        release = asyncio.Event()

        class BlockingProvider(MockProvider):
            async def complete(self, request):
                nonlocal entered
                entered += 1
                if entered == 2:
                    both_entered.set()
                await release.wait()
                return await super().complete(request)

        settings = Settings(
            _env_file=None,
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'health.db'}",
            langgraph_checkpoint_url=":memory:",
            circuit_cooldown_seconds=3,
        )
        app = create_app(settings, health_clock=lambda: moment + timedelta(seconds=3))
        async with app.router.lifespan_context(app):
            async with app.state.database.session_factory() as session:
                await record_provider_attempts(
                    session,
                    [
                        ProviderAttempt(
                            model_id="mock/fast",
                            provider="mock",
                            success=False,
                            latency_ms=1,
                            error_category="unavailable",
                        )
                    ],
                    failure_threshold=1,
                    ewma_alpha=0.2,
                    now=moment,
                )
            app.state.providers = {"mock": BlockingProvider()}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                payload = {
                    "model": "mock/fast",
                    "messages": [{"role": "user", "content": "Hi"}],
                }
                pending = [
                    asyncio.create_task(
                        client.post("/v1/chat/completions", json=payload)
                    )
                    for _ in range(2)
                ]
                try:
                    await asyncio.wait_for(both_entered.wait(), timeout=5)
                finally:
                    release.set()
                    responses = await asyncio.gather(*pending)
        assert entered == 2
        assert all(response.status_code == 200 for response in responses)

    asyncio.run(exercise())
