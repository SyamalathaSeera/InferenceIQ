import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import AuditEvent, Project, utc_now


@dataclass(frozen=True)
class AuditIntegrity:
    valid: bool
    event_count: int
    first_invalid_sequence: int | None
    chain_head: str | None


async def append_audit_event(
    session: AsyncSession,
    *,
    signing_key: str,
    project_id: str,
    event_type: str,
    actor_type: str,
    actor_id: str | None,
    details: dict,
) -> AuditEvent:
    project = await session.scalar(
        select(Project).where(Project.id == project_id).with_for_update()
    )
    if project is None:
        raise ValueError("Cannot append an audit event for an unknown project")

    sequence = project.audit_sequence + 1
    occurred_at = utc_now()
    event_id = f"aud_{uuid4().hex}"
    event_hash = calculate_event_hash(
        signing_key=signing_key,
        event_id=event_id,
        project_id=project.id,
        sequence=sequence,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        details=details,
        occurred_at=occurred_at,
        previous_hash=project.audit_chain_head,
    )
    event = AuditEvent(
        id=event_id,
        project_id=project.id,
        sequence=sequence,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        details=details,
        occurred_at=occurred_at,
        previous_hash=project.audit_chain_head,
        event_hash=event_hash,
    )
    project.audit_sequence = sequence
    project.audit_chain_head = event_hash
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def load_audit_events(
    session: AsyncSession,
    project_id: str,
    *,
    limit: int | None = None,
) -> list[AuditEvent]:
    statement = (
        select(AuditEvent)
        .where(AuditEvent.project_id == project_id)
        .order_by(AuditEvent.sequence)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list((await session.scalars(statement)).all())


async def verify_audit_chain(
    session: AsyncSession,
    *,
    signing_key: str,
    project_id: str,
) -> AuditIntegrity:
    project = await session.get(Project, project_id)
    if project is None:
        raise ValueError("Unknown project")
    events = await load_audit_events(session, project_id)
    previous_hash: str | None = None
    for expected_sequence, event in enumerate(events, start=1):
        expected_hash = calculate_event_hash(
            signing_key=signing_key,
            event_id=event.id,
            project_id=event.project_id,
            sequence=event.sequence,
            event_type=event.event_type,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            details=event.details,
            occurred_at=event.occurred_at,
            previous_hash=event.previous_hash,
        )
        if (
            event.sequence != expected_sequence
            or event.previous_hash != previous_hash
            or not hmac.compare_digest(event.event_hash, expected_hash)
        ):
            return AuditIntegrity(
                valid=False,
                event_count=len(events),
                first_invalid_sequence=expected_sequence,
                chain_head=project.audit_chain_head,
            )
        previous_hash = event.event_hash
    head_is_valid = (
        project.audit_sequence == len(events)
        and project.audit_chain_head == previous_hash
    )
    return AuditIntegrity(
        valid=head_is_valid,
        event_count=len(events),
        first_invalid_sequence=None if head_is_valid else len(events) + 1,
        chain_head=project.audit_chain_head,
    )


def calculate_event_hash(
    *,
    signing_key: str,
    event_id: str,
    project_id: str,
    sequence: int,
    event_type: str,
    actor_type: str,
    actor_id: str | None,
    details: dict,
    occurred_at: datetime,
    previous_hash: str | None,
) -> str:
    payload = json.dumps(
        {
            "id": event_id,
            "project_id": project_id,
            "sequence": sequence,
            "event_type": event_type,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "details": details,
            "occurred_at": _canonical_time(occurred_at),
            "previous_hash": previous_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(signing_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _canonical_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds")
