import re
from typing import Optional, Dict, Any, Literal
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.database import get_db
from backend.app.orchestration.router import WorkflowRouter
from backend.app.artifacts.storage import ArtifactStorage
from backend.app.surfaces.playwright import PlaywrightWebSurface
from backend.app.replay.engine import DeterministicReplayEngine
from backend.app.db.models import RunModel, RunStepModel
from backend.app.core.config import settings

router = APIRouter()

class RunGoalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(..., min_length=1, max_length=1000, example="Find member 1002 and retrieve their savings balance.")
    target_app: Literal["APEX Federal"] = Field("APEX Federal", example="APEX Federal")
    input_parameters: Dict[str, Any] = Field(default_factory=dict, max_length=32, example={"member_id": "1002"})
    force_mode: Optional[Literal["DISCOVERY", "REPLAY"]] = Field(None, description="DISCOVERY or REPLAY (optional override)")

    @field_validator("goal")
    @classmethod
    def goal_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("goal must not be blank")
        return value

    @field_validator("input_parameters")
    @classmethod
    def validate_member_id(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if "member_id" in value and not re.fullmatch(r"\d{4,5}", str(value["member_id"])):
            raise ValueError("input_parameters.member_id must contain four or five digits")
        return value

class ExplicitReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]{1,80}$", example="member_savings_lookup")
    version: str = Field("1.0.0", pattern=r"^\d+\.\d+\.\d+$", example="1.0.0")
    parameters: Dict[str, Any] = Field(..., min_length=1, max_length=32, example={"member_id": "1002"})

    @field_validator("parameters")
    @classmethod
    def validate_member_id(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if "member_id" in value and not re.fullmatch(r"\d{4,5}", str(value["member_id"])):
            raise ValueError("parameters.member_id must contain four or five digits")
        return value

@router.post("/workflows/run")
async def run_workflow_goal(req: RunGoalRequest, db: AsyncSession = Depends(get_db), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    router_engine = WorkflowRouter(db)
    result = await router_engine.execute_goal(
        goal=req.goal,
        target_app=req.target_app,
        input_parameters=req.input_parameters,
        force_mode=req.force_mode,
        idempotency_key=idempotency_key,
    )
    if result.get("error_code") == "IDEMPOTENCY_KEY_CONFLICT":
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_CONFLICT", "message": result["error"]})
    return result

@router.post("/workflows/replay")
async def replay_capability(req: ExplicitReplayRequest, db: AsyncSession = Depends(get_db), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    storage = ArtifactStorage(db)
    artifact = await storage.get_artifact(req.capability_id, req.version)
    if not artifact:
        raise HTTPException(status_code=404, detail=f"Capability '{req.capability_id}' v{req.version} not found")

    router_engine = WorkflowRouter(db)
    result = await router_engine.execute_goal(
        goal=f"Replay capability {req.capability_id} with inputs",
        target_app=artifact.target_application,
        input_parameters=req.parameters,
        force_mode="REPLAY",
        requested_capability_id=req.capability_id,
        idempotency_key=idempotency_key,
    )
    if result.get("error_code") == "IDEMPOTENCY_KEY_CONFLICT":
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_CONFLICT", "message": result["error"]})
    return result
