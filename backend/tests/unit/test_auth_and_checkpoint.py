from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import select

from backend.app.artifacts.schema import CapabilityArtifact, CheckpointRule, ParameterDef, ReplayStep, TargetStrategy
from backend.app.core.config import settings
from backend.app.db.database import Base
from backend.app.db.models import AuditEventModel, ExecutionCheckpointModel, HandoffSessionModel, RunModel, TenantModel
from backend.app.escalation.manager import SessionManager
from backend.app.orchestration.checkpoints import create_execution_checkpoint, stable_action_id
from backend.app.safety.policy import RiskLevel
from backend.app.security.auth import Principal, authorize, decode_access_token, hash_password, issue_access_token, verify_password


def _artifact():
    return CapabilityArtifact(
        capability_id="member_lookup", version="1.0.0", name="Member lookup", description="Read-only lookup",
        parameters=[ParameterDef(name="member_id")],
        steps=[ReplayStep(step_number=1, action_type="fill", target=TargetStrategy(primary_selector="#member-id-input"), parameter_ref="member_id")],
        success_condition=CheckpointRule(rule_type="element_visible", target="#member-name-val"),
    )


def test_jwt_checks_integrity_claims_and_expiration(monkeypatch):
    monkeypatch.setattr(settings, "APEX_AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "APEX_JWT_SIGNING_KEY", "k" * 40)
    monkeypatch.setattr(settings, "APEX_JWT_ISSUER", "issuer")
    monkeypatch.setattr(settings, "APEX_JWT_AUDIENCE", "audience")
    operator = SimpleNamespace(id="operator-1")
    token, _ = issue_access_token(operator, now=1000)
    assert decode_access_token(token, now=1001)["sub"] == "operator-1"
    with pytest.raises(HTTPException):
        decode_access_token(token, now=5000)
    header, payload, signature = token.split(".")
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(HTTPException):
        decode_access_token(f"{header}.{payload}.{tampered_signature}", now=1001)


def test_password_hash_and_role_permissions():
    encoded = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)
    with pytest.raises(HTTPException) as denied:
        authorize(Principal("v", "viewer", "VIEWER", "tenant-a"), "runs:create")
    assert denied.value.status_code == 403
    authorize(Principal("o", "operator", "OPERATOR", "tenant-a"), "runs:resume")


@pytest.mark.asyncio
async def test_checkpoint_persists_action_plan_and_input_names_only():
    artifact = _artifact()
    step = artifact.steps[0]
    action_id = stable_action_id(artifact, step)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        checkpoint = await create_execution_checkpoint(
            db, run_id="wf_checkpoint_test", tenant_id="tenant-a", artifact=artifact,
            input_names=["member_id"],
        )
        assert checkpoint.pending_actions_json == [action_id]
        assert checkpoint.required_input_names_json == ["member_id"]
        assert "1002" not in str(checkpoint.action_history_json)
        assert checkpoint.action_history_json[0]["retry_policy"] == "SAFE_TO_RETRY_AFTER_STATE_RECONSTRUCTION"
        assert RiskLevel.MEDIUM == "MEDIUM"
    await engine.dispose()


@pytest.mark.asyncio
async def test_process_restart_marks_waiting_handoff_recovery_required():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(TenantModel(id="tenant-a", name="Tenant A", is_active=True))
        run = RunModel(id="wf_recovery_test", goal="member lookup", target_app="APEX Federal", execution_mode="Deterministic Replay", status="BLOCKED", tenant_id="tenant-a")
        db.add(run)
        checkpoint = ExecutionCheckpointModel(
            checkpoint_id="cp_recovery_test", run_id=run.id, tenant_id="tenant-a", capability_id="member_lookup",
            capability_version="1.0.0", plan_sha256="a" * 64, state="AWAITING_OPERATOR",
            action_history_json=[], completed_actions_json=[], pending_actions_json=[], required_input_names_json=["member_id"],
        )
        db.add(checkpoint)
        session = HandoffSessionModel(
            id="hs_recovery_test", run_id=run.id, tenant_id="tenant-a", checkpoint_id=checkpoint.checkpoint_id,
            state="AWAITING_OPERATOR", reason="confirmation required", expires_at=datetime.now(timezone.utc)+timedelta(minutes=30),
        )
        db.add(session)
        await db.commit()

        manager = SessionManager()
        assert await manager.mark_persisted_sessions_lost(db) == 1
        await db.refresh(session)
        await db.refresh(checkpoint)
        await db.refresh(run)
        audit = await db.execute(select(AuditEventModel).where(AuditEventModel.event_type == "HANDOFF_RECOVERY_REQUIRED"))
        assert session.state == "RECOVERY_REQUIRED"
        assert checkpoint.state == "RECOVERY_REQUIRED"
        assert run.status == "BLOCKED"
        assert audit.scalar_one().tenant_id == "tenant-a"
    await engine.dispose()
