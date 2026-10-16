from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inference.models import ModelHealth
from app.persistence.models import ProviderHealthRecord, utc_now


@dataclass(frozen=True)
class ProviderAttempt:
    model_id: str
    provider: str
    success: bool
    latency_ms: float
    error_category: str | None = None


async def load_model_health(
    session: AsyncSession,
    *,
    cooldown_seconds: int,
) -> dict[str, ModelHealth]:
    records = list((await session.scalars(select(ProviderHealthRecord))).all())
    now = utc_now()
    changed = False
    for record in records:
        if (
            record.circuit_state == "open"
            and record.opened_at is not None
            and now - _as_utc(record.opened_at) >= timedelta(seconds=cooldown_seconds)
        ):
            record.circuit_state = "half_open"
            record.updated_at = now
            changed = True
    if changed:
        await session.commit()
    return {record.model_id: _snapshot(record) for record in records}


async def record_provider_attempts(
    session: AsyncSession,
    attempts: list[ProviderAttempt],
    *,
    failure_threshold: int,
    ewma_alpha: float,
) -> None:
    if not attempts:
        return
    now = utc_now()
    for attempt in attempts:
        record = await session.scalar(
            select(ProviderHealthRecord)
            .where(ProviderHealthRecord.model_id == attempt.model_id)
            .with_for_update()
        )
        if record is None:
            record = ProviderHealthRecord(
                model_id=attempt.model_id,
                provider=attempt.provider,
            )
            session.add(record)
            await session.flush()

        record.latency_ewma_ms = _ewma(
            record.latency_ewma_ms,
            attempt.latency_ms,
            ewma_alpha,
        )
        health_failure = (
            not attempt.success and attempt.error_category != "invalid_request"
        )
        record.failure_ewma = _ewma(
            record.failure_ewma, 1.0 if health_failure else 0.0, ewma_alpha
        )
        record.updated_at = now
        if attempt.success:
            record.total_successes += 1
            record.consecutive_failures = 0
            record.last_error_category = None
            record.circuit_state = "closed"
            record.opened_at = None
        else:
            record.total_failures += 1
            record.last_error_category = attempt.error_category
            if health_failure:
                record.consecutive_failures += 1
                if (
                    record.circuit_state == "half_open"
                    or record.consecutive_failures >= failure_threshold
                ):
                    record.circuit_state = "open"
                    record.opened_at = now
    await session.commit()


async def reset_circuit(session: AsyncSession, model_id: str) -> bool:
    record = await session.get(ProviderHealthRecord, model_id)
    if record is None:
        return False
    record.circuit_state = "closed"
    record.consecutive_failures = 0
    record.failure_ewma = 0.0
    record.last_error_category = None
    record.opened_at = None
    record.updated_at = utc_now()
    await session.commit()
    return True


async def list_health_records(session: AsyncSession) -> list[ProviderHealthRecord]:
    return list(
        (
            await session.scalars(
                select(ProviderHealthRecord).order_by(
                    ProviderHealthRecord.provider,
                    ProviderHealthRecord.model_id,
                )
            )
        ).all()
    )


def health_score(record: ProviderHealthRecord) -> float:
    return round(max(0.0, min(1.0, 1.0 - record.failure_ewma)), 4)


def _snapshot(record: ProviderHealthRecord) -> ModelHealth:
    return ModelHealth(
        model_id=record.model_id,
        provider=record.provider,
        health_score=health_score(record),
        circuit_state=record.circuit_state,
        observed_latency_ms=round(record.latency_ewma_ms, 2)
        if record.latency_ewma_ms is not None
        else None,
        consecutive_failures=record.consecutive_failures,
    )


def _ewma(current: float | None, observed: float, alpha: float) -> float:
    if current is None:
        return observed
    return alpha * observed + (1 - alpha) * current


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
