import os
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from backend.app.db.database import get_db
from backend.app.db.models import RunModel, RunStepModel
from backend.app.core.errors import execution_outcome_category

router = APIRouter()

@router.get("/runs")
async def list_runs(limit: int = 20, db: AsyncSession = Depends(get_db)):
    stmt = select(RunModel).order_by(RunModel.created_at.desc()).limit(limit)
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
async def get_run_details(run_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(RunModel).where(RunModel.id == run_id).options(
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
import os
