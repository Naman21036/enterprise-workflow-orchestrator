import os
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.models import HandoffRecordModel, RunModel
from backend.app.surfaces.playwright import PlaywrightWebSurface
from backend.app.core.config import settings
from backend.app.core.logging import logger

class SessionManager:
    """Manages active live Playwright browser contexts for HITL handoff."""
    _instance: Optional['SessionManager'] = None

    def __init__(self):
        self._active_surfaces: Dict[str, PlaywrightWebSurface] = {}
        self._handoff_states: Dict[str, Dict[str, Any]] = {}

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
        """Mark open handoffs from an earlier process as lost, never resumable."""
        result = await db.execute(
            select(HandoffRecordModel).where(HandoffRecordModel.status == "AWAITING_HUMAN")
        )
        records = result.scalars().all()
        if not records:
            return 0
        lost_at = datetime.now(timezone.utc)
        for record in records:
            record.status = "SESSION_LOST"
            record.resolved_at = lost_at
            run = await db.get(RunModel, record.run_id)
            if run and run.status in {"BLOCKED", "AWAITING_HUMAN"}:
                run.status = "FAILED"
                run.error_code = "SESSION_LOST"
                run.error_message = "The browser context was lost when the backend process stopped; this run cannot be resumed."
                run.finished_at = lost_at
        await db.commit()
        logger.warning("Marked persisted human intervention sessions as lost", count=len(records))
        return len(records)

    def get_handoff_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._handoff_states.get(run_id)

    async def execute_operator_action(self, run_id: str, action_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
        surface = self.get_surface(run_id)
        if not surface or not surface.page:
            return {"success": False, "error": "No active live browser session found for this run ID."}

        logger.info("Executing HITL operator action", run_id=run_id, action_type=action_type)
        
        try:
            if action_type == "click":
                selector = params.get("selector", "#confirm-dialog-btn")
                ok = await surface.click(selector)
                
            elif action_type == "type":
                selector = params.get("selector")
                text = params.get("text", "")
                if selector:
                    ok = await surface.fill(selector, text)
                else:
                    await surface.page.keyboard.type(text)
                    ok = True

            elif action_type == "press_key":
                key = params.get("key", "Enter")
                await surface.page.keyboard.press(key)
                ok = True

            elif action_type == "take_screenshot":
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
