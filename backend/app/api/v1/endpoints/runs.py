import os
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from backend.app.db.database import get_db
from backend.app.db.models import ExecutionCheckpointModel, HandoffRecordModel, HandoffSessionModel, RunModel, RunStepModel
from backend.app.core.errors import execution_outcome_category
from backend.app.security.auth import Principal, authorize, get_current_principal
from backend.app.security.audit import append_audit
from backend.app.escalation.manager import session_manager

router = APIRouter()

@router.get("/runs")
async def list_runs(limit: int = 20, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)):
    authorize(principal, "runs:read")
    stmt = select(RunModel).where(RunModel.tenant_id == principal.tenant_id).order_by(RunModel.created_at.desc()).limit(max(1, min(limit, 100)))
    res = await db.execute(stmt)
    runs = res.scalars().all()
    return [
        {
            "run_id": r.id,
            "goal": r.goal,
            "target_app": r.target_app,
            "execution_mode": r.execution_mode,
            "status": r.status,
            "outcome_category": execution_outcome_category(r.status, r.error_code),
            "capability_id": r.capability_id,
            "capability_version": r.capability_version,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_seconds": r.duration_seconds,
            "error": r.error_message,
            "error_code": r.error_code,
            "result": r.result_json
        }
        for r in runs
    ]

@router.get("/runs/{run_id}")
async def get_run_details(run_id: str, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)):
    authorize(principal, "runs:read")
    stmt = select(RunModel).where(RunModel.id == run_id, RunModel.tenant_id == principal.tenant_id).options(
        selectinload(RunModel.steps),
        selectinload(RunModel.recording),
    )
    res = await db.execute(stmt)
    run = res.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    steps = [
        {
            "step_number": s.step_number,
            "action_type": s.action_type,
            "target_description": s.target_description,
            "status": s.status,
            "duration_ms": s.duration_ms,
            "error": s.error,
            "screenshot_path": s.screenshot_path,
            "evidence_available": bool(s.screenshot_path and os.path.isfile(s.screenshot_path)),
            "observation": s.observation_json
        }
        for s in sorted(run.steps, key=lambda x: x.step_number)
    ]

    return {
        "run_id": run.id,
        "goal": run.goal,
        "target_app": run.target_app,
        "execution_mode": run.execution_mode,
        "status": run.status,
        "outcome_category": execution_outcome_category(run.status, run.error_code),
        "capability_id": run.capability_id,
        "capability_version": run.capability_version,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "duration_seconds": run.duration_seconds,
        "error": run.error_message,
        "error_code": run.error_code,
        "result": run.result_json,
        "recording_id": run.recording.id if run.recording else None,
        "linked_replay_runs": (run.recording.linked_replay_runs_json or []) if run.recording else [],
        "steps": steps
    }


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)):
    authorize(principal, "runs:cancel")
    async with session_manager.lock_for_run(run_id):
        result = await db.execute(select(RunModel).where(RunModel.id == run_id, RunModel.tenant_id == principal.tenant_id))
        run = result.scalar_one_or_none()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.status != "BLOCKED":
            raise HTTPException(status_code=409, detail="Cancellation is supported at a blocked handoff boundary only")
        session_result = await db.execute(select(HandoffSessionModel).where(HandoffSessionModel.run_id == run_id, HandoffSessionModel.tenant_id == principal.tenant_id).order_by(HandoffSessionModel.created_at.desc()))
        handoff_session = session_result.scalars().first()
        if handoff_session and handoff_session.state not in {"AWAITING_OPERATOR", "RECOVERY_REQUIRED", "EXPIRED"}:
            raise HTTPException(status_code=409, detail="The handoff is currently claimed by another operation")
        now = datetime.now(timezone.utc)
        if handoff_session:
            claimed = await db.execute(
                update(HandoffSessionModel)
                .where(
                    HandoffSessionModel.id == handoff_session.id,
                    HandoffSessionModel.state == handoff_session.state,
                    HandoffSessionModel.version == handoff_session.version,
                )
                .values(state="CANCELLED", version=handoff_session.version + 1, updated_at=now, resolved_at=now)
            )
            if claimed.rowcount != 1:
                await db.rollback()
                raise HTTPException(status_code=409, detail="The handoff changed while cancellation was being claimed")
            await db.refresh(handoff_session)
        run.status = "CANCELLED"
        run.error_code = "CANCELLED_BY_OPERATOR"
        run.error_message = "Cancelled by an authorized operator at the handoff boundary."
        run.finished_at = now
        if handoff_session:
            checkpoint = await db.get(ExecutionCheckpointModel, handoff_session.checkpoint_id)
            if checkpoint:
                checkpoint.state = "CANCELLED"
                checkpoint.version += 1
                checkpoint.updated_at = now
        record_result = await db.execute(select(HandoffRecordModel).where(HandoffRecordModel.run_id == run_id).order_by(HandoffRecordModel.created_at.desc()))
        record = record_result.scalars().first()
        if record:
            record.status = "CANCELLED"
            record.resolved_at = now
        await append_audit(db, event_type="RUN_CANCELLED", tenant_id=principal.tenant_id, run_id=run_id, session_id=handoff_session.id if handoff_session else None, actor_id=principal.operator_id, payload={"status": "CANCELLED"}, commit=False)
        await db.commit()
        await session_manager.close_session(run_id)
        return {"run_id": run_id, "status": run.status}
import os
