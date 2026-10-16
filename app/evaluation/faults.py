"""Mock-only fault injection and observations for the existing HTTP benchmark."""

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI

from app.evaluation.models import (
    BenchmarkCaseResult,
    FaultAttemptResult,
    FaultScenario,
)
from app.health import list_health_records
from app.providers.base import ProviderError
from app.providers.mock import MockProvider


class FaultContext:
    def __init__(self, scenario: FaultScenario):
        self.scenario = scenario
        self.app: FastAPI | None = None
        self.index = 0
        self.attempts: list[FaultAttemptResult] = []
        self.before: dict[str, str] = {}

    def now(self) -> datetime:
        # Only circuit time is virtual. Client and provider wall clocks stay real.
        return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(
            seconds=max(0, self.index - 1) * self.scenario.request_interval_seconds
        )

    async def snapshot(self) -> dict[str, str]:
        if self.app is None:
            raise RuntimeError("Fault context must be attached to an application")
        async with self.app.state.database.session_factory() as session:
            records = await list_health_records(session)
            return {record.model_id: record.circuit_state for record in records}

    async def begin(self, index: int) -> None:
        if index != self.index + 1:
            raise ValueError("Fault schedules require consecutive per-arm requests")
        self.index = index
        self.attempts = []
        self.before = await self.snapshot()

    async def observe(self, row: BenchmarkCaseResult) -> None:
        row.simulated = True
        row.fault_request_index = self.index
        row.virtual_time_seconds = (
            self.index - 1
        ) * self.scenario.request_interval_seconds
        row.fault_window = any(
            window.start <= self.index <= window.end for window in self.scenario.faults
        )
        row.recovery_window = any(
            window.start <= self.index <= window.end
            for window in self.scenario.recovery_windows
        )
        row.attempt_details = list(self.attempts)
        row.gateway_attempts = len(self.attempts)
        row.fallback_models = [attempt.model for attempt in self.attempts[1:]]
        if self.attempts and not row.success:
            row.selected_model = self.attempts[-1].model
        row.circuit_before = dict(self.before)
        row.circuit_after = await self.snapshot()
        attempted = {attempt.model for attempt in self.attempts}
        row.avoided_open_models = [
            model
            for model, state in self.before.items()
            if state == "open"
            and model not in attempted
            and row.circuit_after.get(model) == "open"
        ]


class FaultInjectingMockProvider(MockProvider):
    def __init__(self, context: FaultContext):
        self.context = context

    async def complete(self, request):
        context = self.context
        state = (await context.snapshot()).get(request.model, "closed")
        window = next(
            (
                window
                for window in context.scenario.faults
                if window.start <= context.index <= window.end
                and request.model in window.models
            ),
            None,
        )
        if window is not None:
            if window.delay_ms:
                await asyncio.sleep(window.delay_ms / 1000)
            codes = {
                "rate_limit": 429,
                "timeout": 504,
                "unavailable": 503,
                "authentication": 401,
            }
            category = (
                "configuration" if window.error == "authentication" else window.error
            )
            retryable = window.error != "authentication"
            context.attempts.append(
                FaultAttemptResult(
                    model=request.model,
                    success=False,
                    status_code=codes[window.error],
                    error_category=category,
                    retryable=retryable,
                    circuit_state=state,
                )
            )
            raise ProviderError(
                "Injected mock fault.",
                status_code=codes[window.error],
                category=category,
                retryable=retryable,
            )
        response = await super().complete(request)
        context.attempts.append(
            FaultAttemptResult(
                model=request.model,
                success=True,
                status_code=200,
                circuit_state=state,
            )
        )
        return response
