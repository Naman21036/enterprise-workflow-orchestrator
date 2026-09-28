import re
from typing import Dict, Any, Optional, Literal
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.app.db.database import get_db
from backend.app.db.models import HandoffRecordModel, RunModel
from backend.app.escalation.manager import session_manager
from backend.app.safety.policy import default_safety_policy
from backend.app.core.config import settings

router = APIRouter()

class OperatorActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: Literal["click", "type", "press_key", "take_screenshot"] = Field(..., example="click")
    params: Dict[str, Any] = Field(default_factory=dict, max_length=8, example={"selector": "#confirm-dialog-btn"})

    @field_validator("params")
    @classmethod
    def validate_params(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        selector = value.get("selector")
        if selector is not None and (not isinstance(selector, str) or len(selector) > 200):
            raise ValueError("selector must be a string no longer than 200 characters")
        text = value.get("text")
        if text is not None and (not isinstance(text, str) or len(text) > 1000):
            raise ValueError("text must be a string no longer than 1000 characters")
        key = value.get("key")
        if key is not None and key not in {"Enter", "Escape", "Tab", "ArrowDown", "ArrowUp"}:
            raise ValueError("key must be one of Enter, Escape, Tab, ArrowDown, or ArrowUp")
        return value

@router.get("/runs/{run_id}/handoff")
async def get_handoff_info(run_id: str, db: AsyncSession = Depends(get_db)):
    state = session_manager.get_handoff_state(run_id)
    stmt = select(HandoffRecordModel).where(HandoffRecordModel.run_id == run_id).order_by(HandoffRecordModel.created_at.desc())
    res = await db.execute(stmt)
    rec = res.scalar_one_or_none()

    if not rec and not state:
        raise HTTPException(status_code=404, detail=f"No active handoff record found for run '{run_id}'")

    return {
        "run_id": run_id,
        "status": state.get("status") if state else (rec.status if rec else "UNKNOWN"),
        "reason": state.get("reason") if state else (rec.reason if rec else ""),
        "step_number": state.get("step_number") if state else (rec.step_number if rec else 1),
        "screenshot_path": state.get("screenshot_path") if state else (rec.screenshot_path if rec else None),
        "operator_actions": state.get("operator_actions", []) if state else (rec.operator_actions_json if rec else []),
        "session_active": session_manager.get_surface(run_id) is not None
    }

@router.post("/runs/{run_id}/handoff/action")
async def submit_operator_action(run_id: str, req: OperatorActionRequest, db: AsyncSession = Depends(get_db)):
    state = session_manager.get_handoff_state(run_id)
    handoff_result = await db.execute(
        select(HandoffRecordModel)
        .where(HandoffRecordModel.run_id == run_id)
        .order_by(HandoffRecordModel.created_at.desc())
    )
    handoff_record = handoff_result.scalars().first()
    if not state or state.get("status") != "AWAITING_HUMAN" or not handoff_record or handoff_record.status != "AWAITING_HUMAN":
        raise HTTPException(status_code=409, detail="This run does not have an active human intervention request.")
    if req.action_type == "type" and not req.params.get("selector"):
        raise HTTPException(status_code=422, detail="A selector is required for typed operator input")
    if req.action_type == "click":
        selector = req.params.get("selector", "#confirm-dialog-btn")
        # The only risky action exposed during a verified handoff is the explicit
        # confirmation button in the configured local banking simulator.
        if selector != "#confirm-dialog-btn":
            allowed, reason = default_safety_policy.validate_action("click", settings.TARGET_APP_URL, selector=selector)
            if not allowed:
                raise HTTPException(status_code=403, detail=reason)
    elif req.action_type in {"type", "press_key"}:
        allowed, reason = default_safety_policy.validate_action(
            req.action_type, settings.TARGET_APP_URL,
            selector=str(req.params.get("selector") or ""),
            value=str(req.params.get("text") or req.params.get("key") or ""),
        )
        if not allowed:
            raise HTTPException(status_code=403, detail=reason)

    res = await session_manager.execute_operator_action(run_id, req.action_type, req.params)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Action failed"))

    # Update DB record
    stmt = select(HandoffRecordModel).where(HandoffRecordModel.run_id == run_id).order_by(HandoffRecordModel.created_at.desc())
    db_res = await db.execute(stmt)
    rec = db_res.scalar_one_or_none()
    if rec:
        actions = list(rec.operator_actions_json or [])
        sanitized = default_safety_policy.sanitize_sensitive_data(req.params)
        if isinstance(sanitized, dict) and "text" in sanitized:
            sanitized["text"] = "[REDACTED]"
        actions.append({"action_type": req.action_type, "params": sanitized, "timestamp": datetime.now(timezone.utc).isoformat()})
        rec.operator_actions_json = actions
        await db.commit()

    return res

@router.post("/runs/{run_id}/resume")
async def resume_run_after_handoff(run_id: str, db: AsyncSession = Depends(get_db)):
    surface = session_manager.get_surface(run_id)
    if not surface or not surface.page:
        raise HTTPException(status_code=400, detail="Live browser session has expired or was closed.")

    member_name = await surface.extract("#member-name-val")
    if member_name:
        outputs = {"member_name": member_name}
        balance = await surface.extract("#savings-balance-val")
        member_id = await surface.extract("#member-id-val")
        if balance:
            outputs["savings_balance"] = balance
        if member_id:
            outputs["member_id"] = member_id
        final_status = "SUCCESS"
    else:
        page_state = await surface.observe()
        if "Member not found" in page_state.get("page_text_summary", ""):
            outputs = {"error_code": "MEMBER_NOT_FOUND", "message": "The requested member ID does not exist in the system."}
            final_status = "BUSINESS_OUTCOME"
        else:
            raise HTTPException(status_code=409, detail="Resume checkpoint failed: the member details page is not verified. The live session remains available.")

    # Update run status to RUNNING / RESUMED
    stmt = select(RunModel).where(RunModel.id == run_id)
    res = await db.execute(stmt)
    run = res.scalar_one_or_none()
    if run:
        run.status = final_status
        run.result_json = outputs
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()

    # Update handoff record
    stmt_h = select(HandoffRecordModel).where(HandoffRecordModel.run_id == run_id)
    res_h = await db.execute(stmt_h)
    rec = res_h.scalar_one_or_none()
    if rec:
        rec.status = "RESUMED"
        rec.resolved_at = datetime.now(timezone.utc)
        await db.commit()

    await session_manager.close_session(run_id)

    return {
        "run_id": run_id,
        "status": final_status,
        "message": "The same live session passed its business checkpoint after operator intervention.",
        "outputs": outputs,
    }
