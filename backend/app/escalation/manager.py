import os
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.models import HandoffRecordModel, HandoffSessionModel, ExecutionCheckpointModel, RunModel
from backend.app.surfaces.playwright import PlaywrightWebSurface
from backend.app.core.config import settings
from backend.app.core.logging import logger
from backend.app.safety.policy import default_safety_policy
from backend.app.security.audit import append_audit


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class SessionManager:
    """Manages active live Playwright browser contexts for HITL handoff."""
    _instance: Optional['SessionManager'] = None

    def __init__(self):
        self._active_surfaces: Dict[str, PlaywrightWebSurface] = {}
        self._handoff_states: Dict[str, Dict[str, Any]] = {}
        self._run_locks: Dict[str, asyncio.Lock] = {}

    @classmethod
    def get_instance(cls) -> 'SessionManager':
        if cls._instance is None:
            cls._instance = SessionManager()
        return cls._instance

    def register_surface(self, run_id: str, surface: PlaywrightWebSurface) -> None:
        self._active_surfaces[run_id] = surface
        logger.info("Registered active surface session for run", run_id=run_id)

    def get_surface(self, run_id: str) -> Optional[PlaywrightWebSurface]:
        return self._active_surfaces.get(run_id)

    def set_handoff_state(self, run_id: str, state: Dict[str, Any]) -> None:
        self._handoff_states[run_id] = state

    async def mark_persisted_sessions_lost(self, db: AsyncSession) -> int:
        """Require deterministic recovery for persisted handoffs after process restart."""
        result = await db.execute(
            select(HandoffSessionModel).where(HandoffSessionModel.state.in_({"AWAITING_OPERATOR", "ACTION_IN_PROGRESS", "RESUMING"}))
        )
        sessions = result.scalars().all()
        if not sessions:
            return 0
        now = datetime.now(timezone.utc)
        for session in sessions:
            session.state = "EXPIRED" if _utc(session.expires_at) <= now else "RECOVERY_REQUIRED"
            session.reason = "The live browser context ended with the backend process; restart the deterministic workflow from its validated checkpoint."
            session.version += 1
            session.updated_at = now
            checkpoint = await db.get(ExecutionCheckpointModel, session.checkpoint_id)
            if checkpoint:
                checkpoint.state = session.state
                checkpoint.version += 1
                checkpoint.updated_at = now
            run = await db.get(RunModel, session.run_id)
            if run and run.status == "BLOCKED":
                run.error_code = "RECOVERY_REQUIRED" if session.state == "RECOVERY_REQUIRED" else "HANDOFF_EXPIRED"
                run.error_message = "The live browser session is unavailable. Resume requires validated deterministic recovery." if session.state == "RECOVERY_REQUIRED" else "The operator handoff expired before recovery."
            await append_audit(
                db,
                event_type="HANDOFF_RECOVERY_REQUIRED" if session.state == "RECOVERY_REQUIRED" else "HANDOFF_EXPIRED",
                tenant_id=session.tenant_id,
                run_id=session.run_id,
                session_id=session.id,
                payload={"state": session.state},
                commit=False,
            )
        await db.commit()
        logger.warning("Marked persisted handoffs for deterministic recovery", count=len(sessions))
        return len(sessions)

    def get_handoff_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._handoff_states.get(run_id)

    def lock_for_run(self, run_id: str) -> asyncio.Lock:
        return self._run_locks.setdefault(run_id, asyncio.Lock())

    async def execute_operator_action(self, run_id: str, action_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
        surface = self.get_surface(run_id)
        if not surface or not surface.page:
            return {"success": False, "error": "No active live browser session found for this run ID."}

        logger.info("Executing HITL operator action", run_id=run_id, action_type=action_type)
        
        try:
            if action_type == "click":
                selector = params.get("selector", "#confirm-dialog-btn")
                if selector == "#confirm-dialog-btn":
                    observed = await surface.observe()
                    page_text = observed.get("page_text_summary", "")
                    if "unexpected-dialog-modal" not in page_text and "Confirm Action" not in page_text:
                        return {"success": False, "error": "The simulator confirmation dialog is not active."}
                    ok = await surface.click_approved_confirmation()
                else:
                    allowed, _ = default_safety_policy.validate_action(
                        "click", await surface.current_url(), selector=selector
                    )
                    if not allowed:
                        return {"success": False, "error": "Operator click was blocked by the safety policy."}
                    ok = await surface.click(selector)
                
            elif action_type == "type":
                selector = params.get("selector")
                text = params.get("text", "")
                if not selector:
                    return {"success": False, "error": "A selector is required for typed operator input."}
                allowed, _ = default_safety_policy.validate_action(
                    "type", await surface.current_url(), selector=selector, value=text
                )
                if not allowed:
                    return {"success": False, "error": "Typed operator input was blocked by the safety policy."}
                ok = await surface.fill(selector, text)

            elif action_type == "press_key":
                key = params.get("key", "Enter")
                if key not in {"Enter", "Escape", "Tab", "ArrowDown", "ArrowUp"}:
                    return {"success": False, "error": "Operator key is not allowed."}
                allowed, _ = default_safety_policy.validate_action(
                    "press_key", await surface.current_url(), value=key
                )
                if not allowed:
                    return {"success": False, "error": "Operator key was blocked by the safety policy."}
                await surface.page.keyboard.press(key)
                ok = True

            elif action_type == "take_screenshot":
                if not default_safety_policy.validate_url(await surface.current_url()):
                    return {"success": False, "error": "Current browser page is outside the safety allowlist."}
                filepath = os.path.join(settings.EVIDENCE_DIR, "escalations", f"{run_id}_operator.png")
                await surface.capture_screenshot(filepath)
                return {"success": True, "screenshot_path": filepath}
            else:
                return {"success": False, "error": f"Unknown operator action: {action_type}"}

            await asyncio.sleep(0.5)
            # Record action in state
            if run_id in self._handoff_states:
                safe_params = {
                    key: "[REDACTED]" if key.lower() in {"text", "value", "password", "token", "member_id", "account_number"} else value
                    for key, value in params.items()
                }
                self._handoff_states[run_id].setdefault("operator_actions", []).append({
                    "action_type": action_type,
                    "params": safe_params,
                    "status": "SUCCESS" if ok else "FAILED"
                })

            # Capture fresh screenshot
            shot_path = os.path.join(settings.EVIDENCE_DIR, "escalations", f"{run_id}_after_action.png")
            await surface.capture_screenshot(shot_path)
            
            return {"success": ok, "screenshot_path": shot_path}
        except Exception as e:
            logger.error("Failed to execute operator action", run_id=run_id, error=str(e))
            return {"success": False, "error": str(e)}

    async def close_session(self, run_id: str) -> None:
        surface = self._active_surfaces.pop(run_id, None)
        self._handoff_states.pop(run_id, None)
        if surface:
            await surface.close()
            logger.info("Closed live surface session for run", run_id=run_id)

session_manager = SessionManager.get_instance()
