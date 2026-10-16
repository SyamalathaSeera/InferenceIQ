import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.persistence.models import (
    ApiKeyRecord,
    InferenceRecord,
    Project,
    RequestAdmission,
    utc_now,
)


@dataclass(frozen=True)
class ProjectUsage:
    admitted_requests: int
    completed_requests: int
    input_tokens: int
    output_tokens: int
    spend_usd: float
    unknown_cost_requests: int


@dataclass(frozen=True)
class AccessContext:
    project: Project
    api_key_id: str


def require_admin(request: Request, supplied_token: str | None) -> None:
    expected = request.app.state.settings.inferenceiq_admin_token
    if not supplied_token or not secrets.compare_digest(supplied_token, expected):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")


async def create_project(
    session: AsyncSession,
    *,
    name: str,
    rate_limit_rpm: int,
    monthly_budget_usd: float | None,
    pii_policy: str,
) -> Project:
    existing = await session.scalar(select(Project.id).where(Project.name == name))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Project name already exists")
    project = Project(
        name=name,
        rate_limit_rpm=rate_limit_rpm,
        monthly_budget_usd=monthly_budget_usd,
        pii_policy=pii_policy,
    )
    session.add(project)
    await session.flush()
    return project


async def issue_api_key(
    session: AsyncSession,
    *,
    project_id: str,
    name: str,
) -> tuple[ApiKeyRecord, str]:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    secret = f"ii_live_{secrets.token_urlsafe(32)}"
    record = ApiKeyRecord(
        project_id=project_id,
        name=name,
        key_prefix=secret[:16],
        key_hash=_hash_key(secret),
    )
    session.add(record)
    await session.flush()
    return record, secret


async def revoke_api_key(
    session: AsyncSession,
    *,
    project_id: str,
    key_id: str,
) -> ApiKeyRecord:
    record = await session.scalar(
        select(ApiKeyRecord).where(
            ApiKeyRecord.id == key_id,
            ApiKeyRecord.project_id == project_id,
        )
    )
    if record is None:
        raise HTTPException(status_code=404, detail="API key not found")
    if record.revoked_at is None:
        record.revoked_at = utc_now()
        await session.flush()
    return record


async def authenticate_and_admit(
    session: AsyncSession,
    request: Request,
) -> AccessContext | None:
    settings: Settings = request.app.state.settings
    authorization = request.headers.get("authorization")
    if authorization is None and not settings.inferenceiq_auth_required:
        return None
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="A Bearer API key is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    secret = authorization.removeprefix("Bearer ").strip()
    key = await _find_key(session, secret)
    if key is None:
        await session.rollback()
        raise HTTPException(
            status_code=401,
            detail="Invalid or revoked API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # PostgreSQL serializes admissions for one project. SQLite remains suitable
    # for local development, where writes are already single-writer.
    project = await session.scalar(
        select(Project).where(Project.id == key.project_id).with_for_update()
    )
    if project is None or not project.enabled:
        await session.rollback()
        raise HTTPException(status_code=403, detail="Project is disabled")

    now = utc_now()
    recent = await session.scalar(
        select(func.count(RequestAdmission.id)).where(
            RequestAdmission.project_id == project.id,
            RequestAdmission.created_at >= now - timedelta(minutes=1),
        )
    )
    if (recent or 0) >= project.rate_limit_rpm:
        await session.rollback()
        raise HTTPException(
            status_code=429,
            detail="Project request rate limit exceeded",
            headers={"Retry-After": "60"},
        )

    usage = await load_project_usage(session, project.id, now=now)
    if (
        project.monthly_budget_usd is not None
        and usage.spend_usd >= project.monthly_budget_usd
    ):
        await session.rollback()
        raise HTTPException(status_code=402, detail="Project monthly budget exhausted")

    session.add(RequestAdmission(project_id=project.id, created_at=now))
    await session.commit()
    return AccessContext(project=project, api_key_id=key.id)


async def authenticate_without_admission(
    session: AsyncSession,
    request: Request,
) -> AccessContext | None:
    settings: Settings = request.app.state.settings
    authorization = request.headers.get("authorization")
    if authorization is None and not settings.inferenceiq_auth_required:
        return None
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="A Bearer API key is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    secret = authorization.removeprefix("Bearer ").strip()
    key = await _find_key(session, secret)
    if key is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or revoked API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    project = await session.get(Project, key.project_id)
    if project is None or not project.enabled:
        raise HTTPException(status_code=403, detail="Project is disabled")
    return AccessContext(project=project, api_key_id=key.id)


async def load_project_usage(
    session: AsyncSession,
    project_id: str,
    *,
    now: datetime | None = None,
) -> ProjectUsage:
    now = now or datetime.now(UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    admitted = await session.scalar(
        select(func.count(RequestAdmission.id)).where(
            RequestAdmission.project_id == project_id,
            RequestAdmission.created_at >= month_start,
        )
    )
    row = (
        await session.execute(
            select(
                func.count(InferenceRecord.receipt_id),
                func.coalesce(func.sum(InferenceRecord.actual_input_tokens), 0),
                func.coalesce(func.sum(InferenceRecord.actual_output_tokens), 0),
                func.coalesce(func.sum(InferenceRecord.actual_cost_usd), 0.0),
                func.count().filter(InferenceRecord.actual_cost_usd.is_(None)),
            ).where(
                InferenceRecord.project_id == project_id,
                InferenceRecord.created_at >= month_start,
            )
        )
    ).one()
    return ProjectUsage(
        admitted_requests=int(admitted or 0),
        completed_requests=int(row[0] or 0),
        input_tokens=int(row[1] or 0),
        output_tokens=int(row[2] or 0),
        spend_usd=round(float(row[3] or 0), 8),
        unknown_cost_requests=int(row[4] or 0),
    )


async def get_project(session: AsyncSession, project_id: str) -> Project | None:
    return await session.get(Project, project_id)


async def list_projects(session: AsyncSession) -> list[Project]:
    return list(
        (
            await session.scalars(select(Project).order_by(Project.created_at.desc()))
        ).all()
    )


async def list_project_inferences(
    session: AsyncSession,
    project_id: str,
    *,
    limit: int,
) -> list[InferenceRecord]:
    return list(
        (
            await session.scalars(
                select(InferenceRecord)
                .where(InferenceRecord.project_id == project_id)
                .order_by(InferenceRecord.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def _find_key(session: AsyncSession, secret: str) -> ApiKeyRecord | None:
    if not secret.startswith("ii_live_") or len(secret) < 24:
        return None
    candidates = list(
        (
            await session.scalars(
                select(ApiKeyRecord).where(
                    ApiKeyRecord.key_prefix == secret[:16],
                    ApiKeyRecord.revoked_at.is_(None),
                )
            )
        ).all()
    )
    supplied_hash = _hash_key(secret)
    return next(
        (
            candidate
            for candidate in candidates
            if secrets.compare_digest(candidate.key_hash, supplied_hash)
        ),
        None,
    )


def _hash_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
