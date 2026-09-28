import os
import json
import asyncio
import re
from typing import Dict, Any, Tuple, List, Optional
from backend.app.surfaces.playwright import PlaywrightWebSurface
from backend.app.artifacts.schema import CapabilityArtifact, ReplayStep
from backend.app.core.errors import BusinessOutcomeException, ReplayExecutionError, HandoffRequiredException
from backend.app.core.config import settings
from backend.app.core.logging import logger
from backend.app.safety.policy import default_safety_policy

class DeterministicReplayEngine:
    """
    Production Deterministic Replay Engine.
    STRICT REQUIREMENT: NEVER CALLS ANY LLM OR LANGGRAPH AGENT.
    Executes compiled capability artifacts deterministically.
    """

    def __init__(self, surface: PlaywrightWebSurface):
        self.surface = surface

    async def _capture_screenshot(self, path: str, run_id: str, step_number: int) -> tuple[Optional[str], Optional[str]]:
        try:
            return await self.surface.capture_screenshot(path), None
        except Exception as exc:
            logger.warning(
                "Replay screenshot capture failed; preserving the browser action result",
                run_id=run_id,
                step_number=step_number,
                error_type=type(exc).__name__,
            )
            return None, f"Screenshot unavailable ({type(exc).__name__})."

    async def execute_replay(
        self,
        artifact: CapabilityArtifact,
        inputs: Dict[str, Any],
        run_id: str
    ) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
        logger.info(
            "Starting Deterministic Replay",
            capability_id=artifact.capability_id,
            version=artifact.version,
            input_names=sorted(inputs),
            run_id=run_id
        )

        evidence_run_dir = os.path.join(settings.EVIDENCE_DIR, "replay", run_id)
        os.makedirs(evidence_run_dir, exist_ok=True)

        step_logs: List[Dict[str, Any]] = []
        extracted_outputs: Dict[str, Any] = {}
        status = "SUCCESS"
        error_msg = None

        declared_inputs = {item.name: item for item in artifact.parameters}
        missing_inputs = [name for name, item in declared_inputs.items() if item.required and name not in inputs]
        unknown_inputs = sorted(set(inputs) - set(declared_inputs))
        if missing_inputs or unknown_inputs:
            return "FAILED", [], {}, f"Input validation failed; missing={missing_inputs}, unknown={unknown_inputs}"
        for name, value in inputs.items():
            definition = declared_inputs[name]
            if definition.param_type == "string" and not isinstance(value, str):
                return "FAILED", [], {}, f"Input '{name}' must be a string"
            if name == "member_id" and not str(value).isdigit():
                return "FAILED", [], {}, "Input 'member_id' must contain digits only"
        try:
            artifact = CapabilityArtifact.validate_for_publication(artifact.model_dump())
        except Exception as exc:
            return "FAILED", [], {}, f"Artifact validation failed: {exc}"

        # Always start at target application homepage. Classify startup failures
        # before any browser actions so they are not mistaken for a failed step.
        try:
            await self.surface.connect(settings.TARGET_APP_URL)
        except PermissionError as exc:
            operation = self.surface.last_failed_operation or "browser_initialization"
            if operation == "playwright_driver_start" and getattr(exc, "winerror", None) == 5:
                code = "PLAYWRIGHT_DRIVER_PERMISSION_DENIED"
                error = "Playwright could not create its Windows driver IPC pipe; no browser actions ran. Check the process execution policy and permissions for child-process IPC."
            else:
                code = "BROWSER_PERMISSION_DENIED"
                error = f"Browser initialization was denied during {operation}; no browser actions ran."
            logger.error("Replay initialization failed", run_id=run_id, error_code=code, operation=operation, winerror=getattr(exc, "winerror", None))
            return "FAILED", [], {}, f"{code}: {error}"
        except Exception as exc:
            operation = self.surface.last_failed_operation or "browser_initialization"
            logger.error("Replay initialization failed", run_id=run_id, error_code="BROWSER_INITIALIZATION_FAILED", operation=operation, error_type=type(exc).__name__)
            return "FAILED", [], {}, f"BROWSER_INITIALIZATION_FAILED: Browser initialization failed during {operation} ({type(exc).__name__}); no browser actions ran."

        for step in artifact.steps:
            step_start = asyncio.get_event_loop().time()
            step_status = "SUCCESS"
            step_error = None
            
            screenshot_path = os.path.join(evidence_run_dir, f"step_{step.step_number}.png")

            # Check for Unexpected Dialog / HITL Block condition before step execution
            obs = await self.surface.observe()
            if "unexpected-dialog-modal" in obs.get("page_text_summary", "") or "Confirm Action" in obs.get("page_text_summary", ""):
                logger.info("Replay encountered blocking unexpected confirmation dialog", step=step.step_number)
                captured_path, evidence_error = await self._capture_screenshot(screenshot_path, run_id, step.step_number)
                step_logs.append({
                    "step_number": step.step_number,
                    "action_type": step.action_type,
                    "status": "BLOCKED",
                    "error": "Unexpected confirmation dialog blocking execution",
                    "screenshot_path": captured_path,
                    "evidence_error": evidence_error,
                })
                return "BLOCKED", step_logs, extracted_outputs, "Unexpected confirmation dialog requires human confirmation."

            # Determine targeting selector using primary + fallback strategy
            candidate_selectors = [step.target.primary_selector] + step.target.fallback_selectors
            if step.target.text_fallback:
                candidate_selectors.append(f'text="{step.target.text_fallback}"')

            active_selector = await self.surface.locate(candidate_selectors)

            act_type = step.action_type.lower()
            action_value = str(inputs.get(step.parameter_ref, step.value_expression or "")) if step.parameter_ref else str(step.value_expression or "")
            action_url = step.value_expression if act_type == "navigate" else await self.surface.current_url()
            allowed, reason = default_safety_policy.validate_action(
                act_type,
                action_url or settings.TARGET_APP_URL,
                selector=active_selector or step.target.primary_selector,
                value=action_value,
            )
            if not allowed:
                captured_path, evidence_error = await self._capture_screenshot(screenshot_path, run_id, step.step_number)
                step_logs.append({
                    "step_number": step.step_number,
                    "action_type": step.action_type,
                    "target_selector": active_selector or step.target.primary_selector,
                    "status": "FAILED",
                    "error": f"Safety policy blocked this action: {reason}",
                    "screenshot_path": captured_path,
                    "evidence_error": evidence_error,
                })
                return "FAILED", step_logs, extracted_outputs, f"SAFETY_POLICY_BLOCKED: Step {step.step_number} was rejected by the safety policy."
            
            # Action: Navigate
            if act_type == "navigate":
                target_url = step.value_expression or settings.TARGET_APP_URL
                await self.surface.navigate(target_url)

            # Action: Fill or select a value using the compiled parameter binding.
            elif act_type in {"fill", "select"}:
                bound_value = ""
                if step.parameter_ref and step.parameter_ref in inputs:
                    bound_value = str(inputs[step.parameter_ref])
                elif step.value_expression:
                    bound_value = step.value_expression

                if not active_selector:
                    active_selector = step.target.primary_selector
                
                ok = await self.surface.fill(active_selector, bound_value) if act_type == "fill" else await self.surface.select(active_selector, bound_value)
                if not ok:
                    step_status = "FAILED"
                    step_error = f"Failed to {act_type} on element '{active_selector}'"

            # Action: Click
            elif act_type == "click":
                if not active_selector:
                    active_selector = step.target.primary_selector

                ok = await self.surface.click(active_selector)
                if not ok:
                    step_status = "FAILED"
                    step_error = f"Failed to click element '{active_selector}'"

            # Action: Extract
            elif act_type == "extract":
                if active_selector:
                    extracted_val = await self.surface.extract(active_selector)
                    if step.parameter_ref:
                        extracted_outputs[step.parameter_ref] = extracted_val

            elif act_type not in {"assert"}:
                return "FAILED", step_logs, extracted_outputs, f"Unsupported replay action: {act_type}"

            captured_path, evidence_error = await self._capture_screenshot(screenshot_path, run_id, step.step_number)

            # Detect a dialog raised by the just-completed action before treating a failed checkpoint as drift.
            post_action_observation = await self.surface.observe()
            page_text = post_action_observation.get("page_text_summary", "")
            if "unexpected-dialog-modal" in page_text or "Confirm Action" in page_text:
                step_logs.append({
                    "step_number": step.step_number,
                    "action_type": step.action_type,
                    "target_selector": active_selector or step.target.primary_selector,
                    "status": "BLOCKED",
                    "error": "Unexpected confirmation dialog requires operator intervention",
                    "screenshot_path": captured_path,
                    "evidence_error": evidence_error,
                })
                return "BLOCKED", step_logs, extracted_outputs, "Unexpected confirmation dialog requires human confirmation."

            # Check for Business Outcome: Member Not Found
            if "Member not found" in page_text or "does not exist in the system" in page_text:
                logger.info("Detected business outcome during replay", error_code="MEMBER_NOT_FOUND", run_id=run_id)
                step_logs.append({
                    "step_number": step.step_number,
                    "action_type": step.action_type,
                    "status": "BUSINESS_OUTCOME",
                    "error": "Member not found in core banking system",
                    "screenshot_path": captured_path,
                    "evidence_error": evidence_error,
                })
                return "BUSINESS_OUTCOME", step_logs, {"error_code": "MEMBER_NOT_FOUND", "message": "The requested member ID does not exist in the system."}, None

            if artifact.capability_id == "member_savings_lookup" and "Multiple active savings accounts are linked" in page_text:
                logger.info("Detected business outcome during replay", error_code="AMBIGUOUS_SAVINGS_ACCOUNT", run_id=run_id)
                step_logs.append({
                    "step_number": step.step_number,
                    "action_type": step.action_type,
                    "status": "BUSINESS_OUTCOME",
                    "error": "More than one active savings account matches the request",
                    "screenshot_path": captured_path,
                    "evidence_error": evidence_error,
                })
                return "BUSINESS_OUTCOME", step_logs, {
                    "error_code": "AMBIGUOUS_SAVINGS_ACCOUNT",
                    "message": "Multiple active savings accounts are linked; specify an account to continue.",
                }, None

            if artifact.capability_id == "member_savings_lookup" and "No active savings account is linked" in page_text:
                logger.info("Detected business outcome during replay", error_code="SAVINGS_ACCOUNT_NOT_FOUND", run_id=run_id)
                step_logs.append({
                    "step_number": step.step_number,
                    "action_type": step.action_type,
                    "status": "BUSINESS_OUTCOME",
                    "error": "No active savings account is linked to the member",
                    "screenshot_path": captured_path,
                    "evidence_error": evidence_error,
                })
                return "BUSINESS_OUTCOME", step_logs, {
                    "error_code": "SAVINGS_ACCOUNT_NOT_FOUND",
                    "message": "No active savings account is linked to this member.",
                }, None

            # Verify Step Checkpoints
            for cp in step.checkpoints:
                if cp.rule_type == "url_contains":
                    curr_url = await self.surface.current_url()
                    if cp.target not in curr_url:
                        step_status = "FAILED"
                        step_error = f"Checkpoint failed: current URL '{curr_url}' does not contain '{cp.target}'"

            duration_ms = int((asyncio.get_event_loop().time() - step_start) * 1000)
            step_logs.append({
                "step_number": step.step_number,
                "action_type": step.action_type,
                "target_selector": active_selector or step.target.primary_selector,
                "status": step_status,
                "duration_ms": duration_ms,
                "error": step_error,
                "screenshot_path": captured_path,
                "evidence_error": evidence_error,
            })

            if step_status == "FAILED":
                return "FAILED", step_logs, extracted_outputs, step_error

            await asyncio.sleep(0.3)

        # Extract Output Definitions declared in Artifact
        for out_def in artifact.outputs:
            val = await self.surface.extract(out_def.selector, out_def.attribute)
            if val:
                if out_def.shape == "currency" and not re.fullmatch(r"\$?\s*(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}", val.strip()):
                    return "FAILED", step_logs, extracted_outputs, f"OUTPUT_VALIDATION_FAILED: Output '{out_def.name}' did not contain a valid currency amount."
                if out_def.name == "member_id" and "member_id" in inputs and val.strip() != str(inputs["member_id"]):
                    return "FAILED", step_logs, extracted_outputs, "OUTPUT_VALIDATION_FAILED: The page member ID did not match the requested member."
                extracted_outputs[out_def.name] = val

        # Verify the artifact-level terminal condition after all actions.
        condition = artifact.success_condition
        condition_value = None
        if condition.rule_type == "element_visible":
            condition_value = await self.surface.extract(condition.target)
            passed = condition_value is not None
        elif condition.rule_type == "text_present":
            passed = condition.target in (await self.surface.observe()).get("page_text_summary", "")
        elif condition.rule_type == "url_contains":
            passed = condition.target in await self.surface.current_url()
        elif condition.rule_type == "title_contains":
            passed = condition.target in (await self.surface.observe()).get("title", "")
        else:
            return "FAILED", step_logs, extracted_outputs, f"Unsupported success condition: {condition.rule_type}"
        if not passed:
            return "FAILED", step_logs, extracted_outputs, f"Success condition failed: expected {condition.description or condition.target}; observed no match"
        
        # Ensure member_id is returned in outputs if passed in inputs
        if "member_id" in inputs and "member_id" not in extracted_outputs:
            extracted_outputs["member_id"] = inputs["member_id"]

        # Save replay evidence log
        replay_log_path = os.path.join(evidence_run_dir, "replay_execution.json")
        with open(replay_log_path, "w", encoding="utf-8") as f:
            json.dump({
                "run_id": run_id,
                "capability_id": artifact.capability_id,
                "status": status,
                "inputs": {key: "[REDACTED]" for key in inputs},
                "output_fields": list(extracted_outputs),
                "outputs_redacted": True,
                "steps": step_logs
            }, f, indent=2)

        return "SUCCESS", step_logs, extracted_outputs, None
