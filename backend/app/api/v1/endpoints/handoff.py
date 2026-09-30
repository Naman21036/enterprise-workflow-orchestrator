import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.database import get_db
from backend.app.db.models import ExecutionCheckpointModel, HandoffRecordModel, HandoffSessionModel, RunModel, RunStepModel
from backend.app.artifacts.storage import ArtifactStorage
from backend.app.orchestration.checkpoints import artifact_fingerprint, persist_step_progress, resolve_current_checkpoint_action, set_checkpoint_state
from backend.app.replay.engine import DeterministicReplayEngine
from backend.app.surfaces.playwright import PlaywrightWebSurface
from backend.app.escalation.manager import session_manager
from backend.app.safety.policy import default_safety_policy
from backend.app.observability import span, set_attributes, record_escalation, record_resume
from backend.app.security.auth import Principal, authorize, get_current_principal
from backend.app.security.audit import append_audit

router = APIRouter()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class OperatorActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: Literal["click", "type", "press_key", "take_screenshot"]
    action_id: Optional[str] = Field(None, min_length=8, max_length=128)
    params: Dict[str, Any] = Field(default_factory=dict, max_length=8)

    @field_validator("params")
    @classmethod
    def validate_params(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if set(value) - {"selector", "text", "key"}:
            raise ValueError("unsupported operator action parameter")
        selector = value.get("selector")
        if selector is not None and (not isinstance(selector, str) or not selector or len(selector) > 200):
            raise ValueError("selector must be a non-empty string no longer than 200 characters")
        text = value.get("text")
        if text is not None and (not isinstance(text, str) or len(text) > 1000):
            raise ValueError("text must be a string no longer than 1000 characters")
        key = value.get("key")
        if key is not None and key not in {"Enter", "Escape", "Tab", "ArrowDown", "ArrowUp"}:
            raise ValueError("key is not allowed")
        return value


async def _latest_handoff(db: AsyncSession, run_id: str) -> Optional[HandoffRecordModel]:
    result = await db.execute(
        select(HandoffRecordModel)
        .where(HandoffRecordModel.run_id == run_id)
        .order_by(HandoffRecordModel.created_at.desc())
    )
    return result.scalars().first()


@router.get("/runs/{run_id}/handoff")
async def get_handoff_info(run_id: str, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)):
    authorize(principal, "handoff:read")
    run = await db.get(RunModel, run_id)
    if not run or run.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="Run not found")
    state = session_manager.get_handoff_state(run_id)
    record = await _latest_handoff(db, run_id)
    session_result = await db.execute(select(HandoffSessionModel).where(HandoffSessionModel.run_id == run_id, HandoffSessionModel.tenant_id == principal.tenant_id).order_by(HandoffSessionModel.created_at.desc()))
    persistent_session = session_result.scalars().first()
    checkpoint = await db.get(ExecutionCheckpointModel, persistent_session.checkpoint_id) if persistent_session else None
    if not record and not state:
        run = await db.get(RunModel, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        raise HTTPException(status_code=404, detail="No handoff record exists for this run")

    run_steps = await db.execute(
        select(RunStepModel).where(RunStepModel.run_id == run_id).order_by(RunStepModel.step_number)
    )
    steps = run_steps.scalars().all()
    completed = [step.step_number for step in steps if step.status in {"SUCCESS", "VERIFIED"}]
    blocked = [step.step_number for step in steps if step.status == "BLOCKED"]
    return {
        "run_id": run_id,
        "execution_mode": run.execution_mode,
        "status": ("AWAITING_HUMAN" if persistent_session.state == "AWAITING_OPERATOR" else persistent_session.state) if persistent_session else (state.get("status") if state else record.status),
        "reason": persistent_session.reason if persistent_session else (state.get("reason") if state else record.reason),
        "step_number": (persistent_session.details_json or {}).get("step_number") if persistent_session else (state.get("step_number") if state else record.step_number),
        "session_id": persistent_session.id if persistent_session else None,
        "session_version": persistent_session.version if persistent_session else None,
        "checkpoint_id": persistent_session.checkpoint_id if persistent_session else None,
        "checkpoint_state": checkpoint.state if checkpoint else None,
        "checkpoint_version": checkpoint.version if checkpoint else None,
        "required_input_names": checkpoint.required_input_names_json if checkpoint else [],
        "expires_at": _utc(persistent_session.expires_at).isoformat() if persistent_session else None,
        "recovery_required": bool(persistent_session and persistent_session.state == "RECOVERY_REQUIRED"),
        "resume_available": bool(run.execution_mode == "Deterministic Replay" and persistent_session and persistent_session.state in {"AWAITING_OPERATOR", "RECOVERY_REQUIRED"}),
        "last_successful_step": max(completed, default=None),
        "failed_step": min(blocked, default=None),
        "screenshot_path": state.get("screenshot_path") if state else record.screenshot_path,
        "operator_actions": (record.operator_actions_json or []) if record and record.operator_actions_json else (state.get("operator_actions", []) if state else []),
        "session_active": session_manager.get_surface(run_id) is not None,
        "actions_allowed": bool(persistent_session and persistent_session.state == "AWAITING_OPERATOR" and _utc(persistent_session.expires_at) > datetime.now(timezone.utc)),
        "cancel_allowed": bool(persistent_session and persistent_session.state in {"AWAITING_OPERATOR", "RECOVERY_REQUIRED", "EXPIRED"}),
    }


@router.post("/runs/{run_id}/handoff/action")
@router.post("/runs/{run_id}/handoff/actions", include_in_schema=False)
async def submit_operator_action(
    run_id: str,
    req: OperatorActionRequest,
    db: AsyncSession = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: Principal = Depends(get_current_principal),
):
    authorize(principal, "handoff:act")
    action_id = req.action_id or idempotency_key
    async with session_manager.lock_for_run(run_id):
        run = await db.get(RunModel, run_id)
        if not run or run.tenant_id != principal.tenant_id:
            raise HTTPException(status_code=404, detail="Run not found")
        state = session_manager.get_handoff_state(run_id)
        record = await _latest_handoff(db, run_id)
        session_result = await db.execute(select(HandoffSessionModel).where(HandoffSessionModel.run_id == run_id, HandoffSessionModel.tenant_id == principal.tenant_id).order_by(HandoffSessionModel.created_at.desc()))
        persistent_session = session_result.scalars().first()
        surface = session_manager.get_surface(run_id)
        if run.status != "BLOCKED" or not persistent_session or persistent_session.state != "AWAITING_OPERATOR" or not record or record.status != "AWAITING_HUMAN":
            raise HTTPException(status_code=409, detail="Run is not awaiting human intervention")
        if _utc(persistent_session.expires_at) <= datetime.now(timezone.utc):
            persistent_session.state = "EXPIRED"
            persistent_session.version += 1
            checkpoint = await db.get(ExecutionCheckpointModel, persistent_session.checkpoint_id)
            if checkpoint:
                checkpoint.state = "EXPIRED"
                checkpoint.version += 1
                checkpoint.updated_at = datetime.now(timezone.utc)
            await append_audit(db, event_type="HANDOFF_EXPIRED", tenant_id=principal.tenant_id, run_id=run_id, session_id=persistent_session.id, actor_id=principal.operator_id, payload={"state": "EXPIRED"}, commit=False)
            await db.commit()
            raise HTTPException(status_code=410, detail="Handoff session has expired")
        if not surface or not surface.page or surface.page.is_closed():
            raise HTTPException(status_code=409, detail="Live browser session is unavailable; deterministic recovery is required")

        if not action_id or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", action_id):
            raise HTTPException(status_code=422, detail="A valid action_id or Idempotency-Key is required")
        previous = list(record.operator_actions_json or [])
        duplicate = next((item for item in previous if item.get("action_id") == action_id), None)
        if duplicate:
            return {
                "success": duplicate.get("status") == "SUCCESS",
                "duplicate": True,
                "in_progress": duplicate.get("status") == "PENDING",
                "screenshot_path": duplicate.get("screenshot_path"),
            }

        if req.action_type == "type" and not req.params.get("selector"):
            raise HTTPException(status_code=422, detail="A selector is required for typed operator input")
        if req.action_type == "click":
            selector = req.params.get("selector", "#confirm-dialog-btn")
            if selector == "#confirm-dialog-btn":
                observed = await surface.observe()
                if "unexpected-dialog-modal" not in observed.get("page_text_summary", "") and "Confirm Action" not in observed.get("page_text_summary", ""):
                    raise HTTPException(status_code=403, detail="Simulator confirmation is allowed only while its confirmation dialog is visible")
                decision = default_safety_policy.evaluate_action("click", await surface.current_url(), selector=selector, action_id=action_id, approval_bound=True)
                await append_audit(db, event_type="SAFETY_DECISION", tenant_id=principal.tenant_id, run_id=run_id, session_id=persistent_session.id, actor_id=principal.operator_id, payload=decision.as_dict())
                if decision.decision.value != "ALLOW":
                    raise HTTPException(status_code=403, detail=decision.reason)
            else:
                decision = default_safety_policy.evaluate_action("click", await surface.current_url(), selector=selector, action_id=action_id)
                if decision.decision.value != "ALLOW":
                    await append_audit(db, event_type="SAFETY_DECISION", tenant_id=principal.tenant_id, run_id=run_id, actor_id=principal.operator_id, payload=decision.as_dict())
                    raise HTTPException(status_code=403, detail=decision.reason)
        elif req.action_type in {"type", "press_key"}:
            decision = default_safety_policy.evaluate_action(
                req.action_type, await surface.current_url(),
                selector=str(req.params.get("selector") or ""),
                value=str(req.params.get("text") or req.params.get("key") or ""),
                action_id=action_id,
            )
            if decision.decision.value != "ALLOW":
                await append_audit(db, event_type="SAFETY_DECISION", tenant_id=principal.tenant_id, run_id=run_id, actor_id=principal.operator_id, payload=decision.as_dict())
                raise HTTPException(status_code=403, detail=decision.reason)
        elif not default_safety_policy.validate_url(await surface.current_url()):
            raise HTTPException(status_code=403, detail="Current browser page is outside the safety allowlist")

        sanitized = default_safety_policy.sanitize_sensitive_data(req.params)
        if isinstance(sanitized, dict) and "text" in sanitized:
            sanitized["text"] = "[REDACTED]"
        pending_action = {
            "action_id": action_id,
            "action_type": req.action_type,
            "params": sanitized,
            "status": "PENDING",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        expected_version = persistent_session.version
        action_claim = await db.execute(
            update(HandoffSessionModel)
            .where(
                HandoffSessionModel.id == persistent_session.id,
                HandoffSessionModel.state == "AWAITING_OPERATOR",
                HandoffSessionModel.version == expected_version,
                HandoffSessionModel.expires_at > datetime.now(timezone.utc),
            )
            .values(
                state="ACTION_IN_PROGRESS",
                version=expected_version + 1,
                updated_at=datetime.now(timezone.utc),
                details_json={**(persistent_session.details_json or {}), "active_action_id": action_id},
            )
        )
        if action_claim.rowcount != 1:
            await db.rollback()
            raise HTTPException(status_code=409, detail="Another operator action is in progress or the handoff changed")
        previous.append(pending_action)
        record.operator_actions_json = previous
        await append_audit(db, event_type="OPERATOR_ACTION_STARTED", tenant_id=principal.tenant_id, run_id=run_id, session_id=persistent_session.id, actor_id=principal.operator_id, payload={"action_id": action_id, "action_type": req.action_type}, commit=False)
        await db.commit()  # Persist the at-most-once marker before touching the live UI.
        with span("apex.handoff.operator_action", {"workflow.run_id": run_id, "action.type": req.action_type}) as action_span:
            try:
                result = await session_manager.execute_operator_action(run_id, req.action_type, req.params)
            except Exception as exc:
                result = {"success": False, "error": f"Operator action outcome is uncertain ({type(exc).__name__})."}
            if result.get("success") and req.action_type == "click" and req.params.get("selector", "#confirm-dialog-btn") == "#confirm-dialog-btn":
                await resolve_current_checkpoint_action(db, persistent_session.checkpoint_id)
            pending_action["status"] = "SUCCESS" if result.get("success") else "FAILED"
            pending_action["screenshot_path"] = result.get("screenshot_path")
            set_attributes(action_span, {"action.success": bool(result.get("success"))})
        record.operator_actions_json = previous
        persistent_session = await db.get(HandoffSessionModel, persistent_session.id)
        persistent_session.state = "AWAITING_OPERATOR" if result.get("success") else "RECOVERY_REQUIRED"
        persistent_session.reason = "Operator action completed." if result.get("success") else "Operator action outcome is uncertain; deterministic recovery is required."
        persistent_session.details_json = {key: value for key, value in (persistent_session.details_json or {}).items() if key != "active_action_id"}
        persistent_session.version += 1
        persistent_session.updated_at = datetime.now(timezone.utc)
        if not result.get("success"):
            checkpoint = await db.get(ExecutionCheckpointModel, persistent_session.checkpoint_id)
            if checkpoint:
                checkpoint.state = "RECOVERY_REQUIRED"
                checkpoint.version += 1
                checkpoint.updated_at = datetime.now(timezone.utc)
        await append_audit(db, event_type="OPERATOR_ACTION_FINISHED", tenant_id=principal.tenant_id, run_id=run_id, session_id=persistent_session.id, actor_id=principal.operator_id, payload={"action_id": action_id, "action_type": req.action_type, "success": bool(result.get("success"))}, commit=False)
        await db.commit()
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error", "Operator action failed"))
        return result


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputs: Dict[str, Any] = Field(default_factory=dict, max_length=32)
    session_version: Optional[int] = Field(None, ge=1)


@router.post("/runs/{run_id}/resume")
async def resume_run_after_handoff(
    run_id: str,
    req: ResumeRequest = Body(default=ResumeRequest()),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    authorize(principal, "runs:resume")
    resume_started = time.monotonic()
    with span("apex.handoff.resume", {"workflow.run_id": run_id}) as resume_span:
        async with session_manager.lock_for_run(run_id):
            run = await db.get(RunModel, run_id)
            if not run or run.tenant_id != principal.tenant_id:
                raise HTTPException(status_code=404, detail="Run not found")
            session_result = await db.execute(
                select(HandoffSessionModel).where(
                    HandoffSessionModel.run_id == run_id,
                    HandoffSessionModel.tenant_id == principal.tenant_id,
                ).order_by(HandoffSessionModel.created_at.desc())
            )
            handoff_session = session_result.scalars().first()
            record = await _latest_handoff(db, run_id)
            checkpoint = await db.get(ExecutionCheckpointModel, handoff_session.checkpoint_id) if handoff_session else None
            if run.execution_mode != "Deterministic Replay":
                raise HTTPException(status_code=409, detail="Discovery cannot continue from a saved deterministic checkpoint. Cancel this handoff and start a new discovery run after the operator action.")
            if run.status != "BLOCKED" or not handoff_session or not record or record.status != "AWAITING_HUMAN":
                raise HTTPException(status_code=409, detail="Run has no resumable handoff session")
            if req.session_version is not None and handoff_session.version != req.session_version:
                raise HTTPException(status_code=409, detail="Handoff session changed; refresh the session before resuming")
            expires_at = _utc(handoff_session.expires_at)
            if expires_at <= datetime.now(timezone.utc):
                handoff_session.state = "EXPIRED"
                handoff_session.version += 1
                if checkpoint:
                    checkpoint.state = "EXPIRED"
                    checkpoint.version += 1
                    checkpoint.updated_at = datetime.now(timezone.utc)
                await append_audit(db, event_type="HANDOFF_EXPIRED", tenant_id=principal.tenant_id, run_id=run_id, session_id=handoff_session.id, actor_id=principal.operator_id, payload={"state": "EXPIRED"}, commit=False)
                await db.commit()
                raise HTTPException(status_code=410, detail="Handoff session has expired")
            if not checkpoint or checkpoint.tenant_id != principal.tenant_id or checkpoint.run_id != run_id:
                raise HTTPException(status_code=409, detail="Durable execution checkpoint is unavailable")
            storage = ArtifactStorage(db)
            artifact = await storage.get_artifact(checkpoint.capability_id, checkpoint.capability_version)
            if not artifact or artifact_fingerprint(artifact) != checkpoint.plan_sha256:
                raise HTTPException(status_code=409, detail="Capability plan changed since this checkpoint was created")
            missing = sorted(set(checkpoint.required_input_names_json or []) - set(req.inputs))
            unknown = sorted(set(req.inputs) - set(checkpoint.required_input_names_json or []))
            if missing or unknown:
                raise HTTPException(status_code=422, detail={"message": "Resume inputs must match the checkpoint input names", "missing": missing, "unknown": unknown})

            live_surface = session_manager.get_surface(run_id)
            is_live = bool(live_surface and live_surface.page and not live_surface.page.is_closed())
            prior_state = handoff_session.state
            if prior_state == "AWAITING_OPERATOR" and not is_live:
                raise HTTPException(status_code=409, detail="The live browser session is unavailable. Restart recovery is required.")
            if prior_state not in {"AWAITING_OPERATOR", "RECOVERY_REQUIRED"}:
                raise HTTPException(status_code=409, detail=f"Handoff session is not resumable in state {prior_state}")
            if prior_state == "RECOVERY_REQUIRED":
                unsafe = [item for item in checkpoint.action_history_json or [] if item.get("retry_policy") != "SAFE_TO_RETRY_AFTER_STATE_RECONSTRUCTION"]
                if unsafe:
                    raise HTTPException(status_code=409, detail="Automatic restart recovery is blocked because this plan contains actions that cannot be safely retried")

            previous_version = handoff_session.version
            claim = await db.execute(
                update(HandoffSessionModel)
                .where(
                    HandoffSessionModel.id == handoff_session.id,
                    HandoffSessionModel.state == prior_state,
                    HandoffSessionModel.version == previous_version,
                    HandoffSessionModel.expires_at > datetime.now(timezone.utc),
                )
                .values(state="RESUMING", version=previous_version + 1, updated_at=datetime.now(timezone.utc))
            )
            if claim.rowcount != 1:
                await db.rollback()
                raise HTTPException(status_code=409, detail="Another operator has already claimed this resume")
            await db.commit()
            await db.refresh(handoff_session)
            await append_audit(
                db, event_type="HANDOFF_RECOVERY_STARTED" if prior_state == "RECOVERY_REQUIRED" else "HANDOFF_RESUME_STARTED",
                tenant_id=principal.tenant_id, run_id=run_id, session_id=handoff_session.id,
                actor_id=principal.operator_id, payload={"from_state": prior_state, "checkpoint_version": checkpoint.version},
            )

            if prior_state == "RECOVERY_REQUIRED":
                surface = PlaywrightWebSurface(headless=True)
                session_manager.register_surface(run_id, surface)
                start_step = 0
                page_already_live = False
            else:
                surface = live_surface
                current_action = next((item for item in checkpoint.action_history_json or [] if item.get("step_number") == checkpoint.current_step), None)
                retry_blocked_action = bool(current_action and current_action.get("outcome_class") == "OPERATOR_APPROVED_RETRY")
                start_step = max(0, checkpoint.current_step - 1 if retry_blocked_action else checkpoint.current_step)
                page_already_live = True

            async def persist_progress(step, event, status):
                await persist_step_progress(
                    db, checkpoint.checkpoint_id, artifact, step, event, status,
                    await surface.current_url() if surface.page and not surface.page.is_closed() else None,
                )

            try:
                replay_status, step_logs, outputs, error_msg = await DeterministicReplayEngine(surface).execute_replay(
                    artifact=artifact,
                    inputs=req.inputs,
                    run_id=run_id,
                    start_step=start_step,
                    page_already_live=page_already_live,
                    progress_callback=persist_progress,
                )
            except Exception as exc:
                replay_status, step_logs, outputs = "FAILED", [], {}
                error_msg = f"Deterministic resume failed ({type(exc).__name__})."

            now = datetime.now(timezone.utc)
            for step_log in step_logs:
                found_step = await db.execute(select(RunStepModel).where(
                    RunStepModel.run_id == run_id,
                    RunStepModel.step_number == step_log["step_number"],
                ))
                persisted_step = found_step.scalar_one_or_none()
                if persisted_step:
                    persisted_step.action_id = step_log.get("action_id") or persisted_step.action_id
                    persisted_step.status = step_log["status"]
                    persisted_step.target_description = step_log.get("target_selector") or persisted_step.target_description
                    persisted_step.error = step_log.get("error")
                    persisted_step.screenshot_path = step_log.get("screenshot_path")
                    persisted_step.duration_ms = step_log.get("duration_ms", persisted_step.duration_ms)
                else:
                    db.add(RunStepModel(
                        run_id=run_id,
                        step_number=step_log["step_number"],
                        action_id=step_log.get("action_id"),
                        action_type=step_log["action_type"],
                        target_description=step_log.get("target_selector"),
                        status=step_log["status"],
                        duration_ms=step_log.get("duration_ms", 0),
                        error=step_log.get("error"),
                        screenshot_path=step_log.get("screenshot_path"),
                    ))

            if replay_status == "BLOCKED":
                handoff_session.state = "AWAITING_OPERATOR"
                handoff_session.reason = error_msg or "Another operator confirmation is required."
                handoff_session.version += 1
                await set_checkpoint_state(db, checkpoint.checkpoint_id, "AWAITING_OPERATOR")
                run.status = "BLOCKED"
                run.error_code = "HUMAN_INTERVENTION_REQUIRED"
                run.error_message = handoff_session.reason
                record.status = "AWAITING_HUMAN"
                record.reason = handoff_session.reason
                record.step_number = step_logs[-1]["step_number"] if step_logs else checkpoint.current_step
                record.resolved_at = None
                session_manager.set_handoff_state(run_id, {
                    "run_id": run_id, "status": "AWAITING_HUMAN", "reason": handoff_session.reason,
                    "step_number": record.step_number, "checkpoint_id": checkpoint.checkpoint_id,
                    "session_id": handoff_session.id,
                })
            else:
                await set_checkpoint_state(db, checkpoint.checkpoint_id, replay_status)
                handoff_session.state = "COMPLETED" if replay_status in {"SUCCESS", "BUSINESS_OUTCOME"} else "FAILED"
                handoff_session.version += 1
                handoff_session.resolved_at = now
                run.status = replay_status
                run.result_json = {"output_fields": sorted(outputs.keys()), "values_redacted": True}
                run.error_message = error_msg
                run.error_code = outputs.get("error_code") if replay_status == "BUSINESS_OUTCOME" else (error_msg.split(":", 1)[0] if error_msg and ":" in error_msg else None)
                run.finished_at = now
                record.status = "RESUMED" if replay_status in {"SUCCESS", "BUSINESS_OUTCOME"} else "FAILED"
                record.resolved_at = now
                if surface:
                    await session_manager.close_session(run_id)
            await append_audit(
                db, event_type="HANDOFF_RESUME_FINISHED", tenant_id=principal.tenant_id,
                run_id=run_id, session_id=handoff_session.id, actor_id=principal.operator_id,
                payload={"status": replay_status, "recovery": prior_state == "RECOVERY_REQUIRED"}, commit=False,
            )
            await db.commit()
            record_resume(replay_status, prior_state == "RECOVERY_REQUIRED", time.monotonic() - resume_started)
            set_attributes(resume_span, {"workflow.status": replay_status, "escalation.required": replay_status == "BLOCKED"})
            return {
                "run_id": run_id,
                "status": replay_status,
                "message": "The deterministic workflow continued from its checkpoint." if page_already_live else "The validated workflow was reconstructed from the target root and replayed safely.",
                "outputs": outputs,
                "error": error_msg,
                "step_logs": step_logs,
                "checkpoint_id": checkpoint.checkpoint_id,
                "checkpoint_version": checkpoint.version,
                "session_version": handoff_session.version,
            }
