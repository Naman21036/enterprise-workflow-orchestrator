"""Durable replay checkpoints; raw input values and browser state are never persisted."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.artifacts.schema import CapabilityArtifact, ReplayStep
from backend.app.core.config import settings
from backend.app.db.models import ExecutionCheckpointModel, HandoffSessionModel
from backend.app.safety.policy import RiskLevel, default_safety_policy


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def artifact_fingerprint(artifact: CapabilityArtifact) -> str:
    return hashlib.sha256(_canonical(artifact.model_dump(mode="json")).encode("utf-8")).hexdigest()


def stable_action_id(artifact: CapabilityArtifact, step: ReplayStep) -> str:
    digest = hashlib.sha256(
        f"{artifact.capability_id}:{artifact.version}:{_canonical(step.model_dump(mode='json'))}".encode("utf-8")
    ).hexdigest()
    return f"act_{digest[:40]}"


def _retry_policy(step: ReplayStep) -> str:
    value = step.value_expression or ""
    risk = default_safety_policy.classify_action_risk(step.action_type, step.target.primary_selector, value)
    if step.action_type.lower() in {"fill", "select"} and step.target.primary_selector == "#member-id-input" and step.parameter_ref:
        # The replay engine validates bound values as four or five digits before input.
        risk = RiskLevel.MEDIUM
    if risk in {RiskLevel.LOW, RiskLevel.MEDIUM}:
        return "SAFE_TO_RETRY_AFTER_STATE_RECONSTRUCTION"
    if risk == RiskLevel.HIGH:
        return "VERIFY_BEFORE_RETRY"
    return "NEVER_AUTOMATICALLY_RETRY"


def sanitize_surface_state(url: str | None) -> dict:
    if not url:
        return {"available": False}
    parsed = urlparse(url)
    route_class = "member_result" if re.fullmatch(r"/member/\d{4,5}/?", parsed.path) else "application_root" if parsed.path == "/" else "unknown"
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        return {"available": False}
    authority = f"{host}:{port}" if port is not None else host
    return {"available": bool(parsed.scheme and host), "origin": f"{parsed.scheme}://{authority}" if parsed.scheme and host else "", "route_class": route_class}


async def create_execution_checkpoint(
    db: AsyncSession,
    *,
    run_id: str,
    tenant_id: str,
    artifact: CapabilityArtifact,
    input_names: list[str],
) -> ExecutionCheckpointModel:
    fingerprint = artifact_fingerprint(artifact)
    checkpoint_id = "cp_" + hashlib.sha256(f"{run_id}:{fingerprint}:0".encode()).hexdigest()[:40]
    existing = await db.get(ExecutionCheckpointModel, checkpoint_id)
    if existing:
        return existing
    action_plan = [
        {
            "action_id": stable_action_id(artifact, step),
            "step_number": step.step_number,
            "action_type": step.action_type,
            "target": step.target.primary_selector if step.action_type != "navigate" else "[ROUTE REDACTED]",
            "status": "PENDING",
            "retry_policy": _retry_policy(step),
        }
        for step in artifact.steps
    ]
    checkpoint = ExecutionCheckpointModel(
        checkpoint_id=checkpoint_id,
        run_id=run_id,
        tenant_id=tenant_id,
        capability_id=artifact.capability_id,
        capability_version=artifact.version,
        plan_sha256=fingerprint,
        current_step=0,
        state="RUNNING",
        completed_actions_json=[],
        pending_actions_json=[item["action_id"] for item in action_plan],
        action_history_json=action_plan,
        required_input_names_json=sorted(set(input_names)),
        surface_state_json={"available": False},
        version=1,
    )
    db.add(checkpoint)
    await db.commit()
    return checkpoint


async def persist_step_progress(
    db: AsyncSession,
    checkpoint_id: str,
    artifact: CapabilityArtifact,
    step: ReplayStep,
    event: str,
    status: str,
    surface_url: str | None,
) -> None:
    checkpoint = await db.get(ExecutionCheckpointModel, checkpoint_id)
    if checkpoint is None:
        raise RuntimeError("Execution checkpoint disappeared while the workflow was running")
    action_id = stable_action_id(artifact, step)
    history = list(checkpoint.action_history_json or [])
    action = next((item for item in history if item["action_id"] == action_id), None)
    if action is None:
        raise RuntimeError("Checkpoint action plan no longer matches the capability artifact")
    if event == "IN_PROGRESS":
        action["status"] = "IN_PROGRESS"
    else:
        action["status"] = status
        action["outcome_class"] = status
        if status in {"SUCCESS", "VERIFIED", "BUSINESS_OUTCOME", "RESOLVED_BY_OPERATOR"}:
            action["completed_at"] = datetime.now(timezone.utc).isoformat()
    completed = [item["action_id"] for item in history if item.get("status") in {"SUCCESS", "VERIFIED", "BUSINESS_OUTCOME", "RESOLVED_BY_OPERATOR"}]
    pending = [item["action_id"] for item in history if item.get("status") not in {"SUCCESS", "VERIFIED", "BUSINESS_OUTCOME", "RESOLVED_BY_OPERATOR"}]
    checkpoint.current_step = max(checkpoint.current_step, step.step_number)
    checkpoint.completed_actions_json = completed
    checkpoint.pending_actions_json = pending
    checkpoint.action_history_json = history
    checkpoint.surface_state_json = sanitize_surface_state(surface_url)
    checkpoint.version += 1
    checkpoint.updated_at = datetime.now(timezone.utc)
    await db.commit()


async def set_checkpoint_state(db: AsyncSession, checkpoint_id: str, state: str) -> ExecutionCheckpointModel:
    checkpoint = await db.get(ExecutionCheckpointModel, checkpoint_id)
    if checkpoint is None:
        raise RuntimeError("Execution checkpoint not found")
    checkpoint.state = state
    checkpoint.version += 1
    checkpoint.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return checkpoint


async def resolve_current_checkpoint_action(db: AsyncSession, checkpoint_id: str) -> None:
    """Bind successful simulator confirmation to the blocked action in its checkpoint."""
    checkpoint = await db.get(ExecutionCheckpointModel, checkpoint_id)
    if checkpoint is None:
        raise RuntimeError("Execution checkpoint not found")
    history = list(checkpoint.action_history_json or [])
    current = next((item for item in history if item.get("step_number") == checkpoint.current_step), None)
    if current is None:
        raise RuntimeError("Blocked action is missing from the checkpoint plan")
    retry_current_action = current.get("status") == "BLOCKED_BEFORE_ACTION"
    current["status"] = "PENDING" if retry_current_action else "RESOLVED_BY_OPERATOR"
    current["outcome_class"] = "OPERATOR_APPROVED_RETRY" if retry_current_action else "RESOLVED_BY_OPERATOR"
    current["completed_at"] = datetime.now(timezone.utc).isoformat()
    checkpoint.action_history_json = history
    checkpoint.completed_actions_json = [item["action_id"] for item in history if item.get("status") in {"SUCCESS", "VERIFIED", "BUSINESS_OUTCOME", "RESOLVED_BY_OPERATOR"}]
    checkpoint.pending_actions_json = [item["action_id"] for item in history if item.get("status") not in {"SUCCESS", "VERIFIED", "BUSINESS_OUTCOME", "RESOLVED_BY_OPERATOR"}]
    checkpoint.version += 1
    checkpoint.updated_at = datetime.now(timezone.utc)
    await db.commit()


async def create_handoff_session(db: AsyncSession, checkpoint: ExecutionCheckpointModel, reason: str, step_number: int) -> HandoffSessionModel:
    found = await db.execute(
        select(HandoffSessionModel).where(
            HandoffSessionModel.run_id == checkpoint.run_id,
            HandoffSessionModel.checkpoint_id == checkpoint.checkpoint_id,
        )
    )
    session = found.scalar_one_or_none()
    if session:
        if session.state in {"AWAITING_OPERATOR", "RESUMING", "RECOVERY_REQUIRED"}:
            return session
        raise RuntimeError("A terminal handoff already exists for this checkpoint")
    now = datetime.now(timezone.utc)
    session = HandoffSessionModel(
        run_id=checkpoint.run_id,
        tenant_id=checkpoint.tenant_id,
        checkpoint_id=checkpoint.checkpoint_id,
        state="AWAITING_OPERATOR",
        reason=reason[:1000],
        details_json={"step_number": step_number, "recovery_mode": "LIVE_SURFACE_ONLY"},
        version=1,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=max(1, settings.APEX_HANDOFF_SESSION_TTL_MINUTES)),
    )
    db.add(session)
    await db.flush()
    return session
