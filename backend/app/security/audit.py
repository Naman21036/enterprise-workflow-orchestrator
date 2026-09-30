"""Application-level append-only audit event writer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import AuditEventModel
from backend.app.safety.policy import default_safety_policy
from backend.app.observability import current_trace_id


async def append_audit(
    db: AsyncSession,
    *,
    event_type: str,
    tenant_id: str = "default",
    run_id: str | None = None,
    session_id: str | None = None,
    actor_id: str | None = None,
    correlation_id: str | None = None,
    payload: dict[str, Any] | None = None,
    commit: bool = True,
) -> AuditEventModel:
    clean = default_safety_policy.sanitize_sensitive_data(payload or {})
    event = AuditEventModel(
        tenant_id=tenant_id,
        run_id=run_id,
        session_id=session_id,
        actor_id=actor_id,
        event_type=event_type,
        correlation_id=correlation_id or current_trace_id() or str(uuid.uuid4()),
        payload_json=clean,
        created_at=datetime.now(timezone.utc),
    )
    db.add(event)
    if commit:
        await db.commit()
    return event
