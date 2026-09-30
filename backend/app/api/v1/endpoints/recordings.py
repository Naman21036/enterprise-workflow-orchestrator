import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.db.database import get_db
from backend.app.db.models import DiscoveryRecordingModel, RunModel
from backend.app.security.auth import Principal, authorize, get_current_principal

router = APIRouter()


def _evidence_available(path: str | None) -> bool:
    return bool(path and os.path.isfile(path))


def serialize(recording: DiscoveryRecordingModel) -> dict:
    duration = None
    if recording.completed_at and recording.started_at:
        duration = max(0, (recording.completed_at - recording.started_at).total_seconds())
    return {
        "recording_id": recording.id,
        "run_id": recording.run_id,
        "goal": recording.goal,
        "target_application": recording.target_application,
        "status": recording.status,
        "started_at": recording.started_at.isoformat() if recording.started_at else None,
        "completed_at": recording.completed_at.isoformat() if recording.completed_at else None,
        "duration_seconds": duration,
        "action_count": len(recording.actions_json or []),
        "actions": [
            {**action, "evidence_available": _evidence_available(action.get("screenshot_path"))}
            for action in (recording.actions_json or [])
        ],
        "checkpoints": recording.checkpoints_json or [],
        "error_classification": (recording.checkpoints_json or [{}])[0].get("classification"),
        "failure_reason": (recording.checkpoints_json or [{}])[0].get("reason"),
        "artifact_id": recording.artifact_id,
        "artifact_version": recording.artifact_version,
    }


@router.get("/recordings")
async def list_recordings(limit: int = 50, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)):
    authorize(principal, "recordings:read")
    result = await db.execute(
        select(DiscoveryRecordingModel).join(RunModel, RunModel.id == DiscoveryRecordingModel.run_id)
        .where(RunModel.tenant_id == principal.tenant_id)
        .order_by(DiscoveryRecordingModel.started_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    return [serialize(item) for item in result.scalars().all()]


@router.get("/recordings/{recording_id}")
async def get_recording(recording_id: str, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)):
    authorize(principal, "recordings:read")
    result = await db.execute(
        select(DiscoveryRecordingModel).join(RunModel, RunModel.id == DiscoveryRecordingModel.run_id)
        .where(DiscoveryRecordingModel.id == recording_id)
        .where(RunModel.tenant_id == principal.tenant_id)
        .options(selectinload(DiscoveryRecordingModel.events))
    )
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail=f"Recording '{recording_id}' not found")

    payload = serialize(recording)
    payload["events"] = [
        {
            "event_id": event.id,
            "run_id": recording.run_id,
            "sequence": event.sequence,
            "event_type": event.event_type,
            "payload": event.payload_json,
            "evidence_path": event.evidence_path,
            "evidence_available": _evidence_available(event.evidence_path),
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
        for event in recording.events
    ]
    replay_ids = recording.linked_replay_runs_json or []
    linked = await db.execute(select(RunModel).where(RunModel.id.in_(replay_ids), RunModel.tenant_id == principal.tenant_id)) if replay_ids else None
    runs_by_id = {run.id: run for run in linked.scalars().all()} if linked else {}
    payload["linked_replay_runs"] = [
        {
            "run_id": run_id,
            "status": runs_by_id[run_id].status,
            "created_at": runs_by_id[run_id].created_at.isoformat() if runs_by_id[run_id].created_at else None,
        }
        for run_id in replay_ids
        if run_id in runs_by_id
    ]
    return payload
