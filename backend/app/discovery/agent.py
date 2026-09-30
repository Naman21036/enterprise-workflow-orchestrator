import asyncio
import json
import os
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from backend.app.core.config import settings
from backend.app.core.logging import logger
from backend.app.discovery.actions import AgentAction, DiscoveryStepResult
from backend.app.discovery.prompts import SYSTEM_DISCOVERY_PROMPT, build_user_discovery_prompt
from backend.app.llm.factory import LLMFactory
from backend.app.safety.policy import default_safety_policy
from backend.app.surfaces.playwright import PlaywrightWebSurface
from backend.app.observability import langsmith_traceable, span


ObservationCallback = Callable[[int, str, Dict[str, Any], Optional[str], Optional[str]], Awaitable[None]]
StepCallback = Callable[[DiscoveryStepResult], Awaitable[None]]
ProviderEventCallback = Callable[[Dict[str, Any]], Awaitable[None]]


class MistralDiscoveryAgent:
    def __init__(self, surface: PlaywrightWebSurface, max_steps: Optional[int] = None):
        self.surface = surface
        self.max_steps = max_steps or settings.DISCOVERY_MAX_STEPS
        self.safety_policy = default_safety_policy
        self.llm_decision_calls = 0

    @staticmethod
    def _bound_observation(observation: Dict[str, Any]) -> Dict[str, Any]:
        """Bound model input while retaining the page facts needed for targeting."""
        bounded = dict(observation)
        bounded["url"] = str(observation.get("url") or "")[:500]
        bounded["title"] = str(observation.get("title") or "")[:200]
        bounded["page_text_summary"] = str(observation.get("page_text_summary") or "")[:1200]
        elements = observation.get("interactive_elements") or []
        bounded["interactive_elements"] = [
            {
                key: str(value)[:400] if value is not None else None
                for key, value in element.items()
                if key in {"idx", "tag", "id", "name", "type", "role", "text", "primary_selector", "aria_label"}
            }
            for element in elements[: max(1, settings.DISCOVERY_OBSERVATION_MAX_ELEMENTS)]
            if isinstance(element, dict)
        ]
        return bounded

    async def _capture_screenshot(self, path: str, run_id: str, step: int) -> Tuple[Optional[str], Optional[str]]:
        try:
            await self.surface.capture_screenshot(path)
            return path, None
        except Exception as exc:
            logger.warning("Discovery screenshot capture failed", run_id=run_id, step=step, error_type=type(exc).__name__)
            return None, type(exc).__name__

    @langsmith_traceable(
        "apex.discovery",
        input_filter=lambda values: {
            "run_id": values.get("run_id"),
            "goal_chars": len(values.get("goal", "")),
            "step_limit": getattr(values.get("self"), "max_steps", None),
        },
        output_filter=lambda result: {
            "success": bool(result[0]) if isinstance(result, tuple) and result else False,
            "steps": len(result[1]) if isinstance(result, tuple) and len(result) > 1 else 0,
            "action_types": [item.action.action_type for item in result[1] if hasattr(item, "action")][:20] if isinstance(result, tuple) and len(result) > 1 else [],
            "result_fields": sorted(result[2].keys()) if isinstance(result, tuple) and len(result) > 2 and isinstance(result[2], dict) else [],
        },
    )
    async def run_discovery(
        self,
        goal: str,
        target_url: str,
        run_id: str,
        on_step: Optional[StepCallback] = None,
        on_observation: Optional[ObservationCallback] = None,
        on_provider_event: Optional[ProviderEventCallback] = None,
    ) -> Tuple[bool, List[DiscoveryStepResult], Dict[str, Any]]:
        logger.info("Starting Mistral discovery loop", goal=default_safety_policy.sanitize_sensitive_data(goal), target_url=target_url, run_id=run_id, model=settings.MISTRAL_MODEL)
        await self.surface.connect(target_url)

        trace: List[DiscoveryStepResult] = []
        final_extracted: Dict[str, Any] = {}
        success = False
        evidence_run_dir = os.path.join(settings.EVIDENCE_DIR, "discovery", run_id)
        os.makedirs(evidence_run_dir, exist_ok=True)

        async def append_trace(item: DiscoveryStepResult) -> None:
            trace.append(item)
            if on_step:
                await on_step(item)

        for step_num in range(1, self.max_steps + 1):
            observation = self._bound_observation(await self.surface.observe())
            before_path = os.path.join(evidence_run_dir, f"step_{step_num}_before.png")
            screenshot_path, screenshot_error = await self._capture_screenshot(before_path, run_id, step_num)
            if on_observation:
                await on_observation(step_num, "BEFORE_ACTION", observation, screenshot_path, screenshot_error)

            page_text = observation.get("page_text_summary", "")
            if "unexpected-dialog-modal" in page_text or "Confirm Action" in page_text:
                item = DiscoveryStepResult(
                    step_number=step_num,
                    action=AgentAction(action_type="escalate", reason="Unexpected confirmation dialog blocking execution"),
                    observation_summary="Unexpected confirmation dialog modal visible",
                    status="ESCALATED",
                    screenshot_path=screenshot_path,
                    observation_before=observation,
                    action_result={"status": "BLOCKED", "classification": "HUMAN_INTERVENTION_REQUIRED"},
                    validation_result="HUMAN_INTERVENTION_REQUIRED",
                )
                await append_trace(item)
                return False, trace, {"error": "HITL_REQUIRED", "reason": "Unexpected confirmation dialog blocking workflow execution."}

            if self.llm_decision_calls >= max(1, settings.MAX_LLM_REQUESTS_PER_RUN):
                item = DiscoveryStepResult(
                    step_number=step_num,
                    action=AgentAction(action_type="escalate", reason="Maximum model decision requests reached"),
                    observation_summary="Discovery stopped at the configured model request limit",
                    status="FAILED",
                    screenshot_path=screenshot_path,
                    observation_before=observation,
                    action_result={"status": "FAILED", "classification": "LLM_REQUEST_LIMIT"},
                    validation_result="LLM_REQUEST_LIMIT",
                )
                await append_trace(item)
                return False, trace, {"error": "LLM_REQUEST_LIMIT", "details": "Maximum model decision requests reached."}

            llm_client = LLMFactory.get_client()
            request_started = time.perf_counter()
            try:
                self.llm_decision_calls += 1
                user_prompt = build_user_discovery_prompt(goal, step_num, observation)
                with span("apex.discovery.model_decision", {
                    "workflow.run_id": run_id,
                    "execution.step_index": step_num,
                    "llm.provider": "mistral",
                    "llm.model": settings.MISTRAL_MODEL,
                }):
                    traced_call = getattr(llm_client, "generate_traced_decision", None)
                    if traced_call:
                        action_data = await traced_call(SYSTEM_DISCOVERY_PROMPT, user_prompt, AgentAction, run_id)
                    else:
                        action_data = await llm_client.generate_structured(
                            system_prompt=SYSTEM_DISCOVERY_PROMPT,
                            user_prompt=user_prompt,
                            response_schema=AgentAction,
                        )
                action = AgentAction.model_validate(action_data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                code = getattr(exc, "code", "MISTRAL_ACTION_ERROR")
                safe_message = default_safety_policy.sanitize_sensitive_data(str(exc))[:500]
                if on_provider_event:
                    await on_provider_event({
                        "step_number": step_num,
                        "provider": "mistral",
                        "model": settings.MISTRAL_MODEL,
                        "status": "FAILED",
                        "error_code": code,
                        "duration_ms": int((time.perf_counter() - request_started) * 1000),
                    })
                logger.error("Discovery model decision failed", run_id=run_id, step=step_num, error_code=code, error_type=type(exc).__name__)
                await append_trace(DiscoveryStepResult(
                    step_number=step_num,
                    action=AgentAction(action_type="escalate", reason="Model decision failed"),
                    observation_summary="Model decision failed after observing the page",
                    status="FAILED",
                    screenshot_path=screenshot_path,
                    observation_before=observation,
                    action_result={"status": "FAILED", "classification": code, "message": safe_message},
                    validation_result=code,
                ))
                return False, trace, {"error": code, "details": safe_message}
            if on_provider_event:
                await on_provider_event({
                    "step_number": step_num,
                    "provider": "mistral",
                    "model": settings.MISTRAL_MODEL,
                    "status": "SUCCESS",
                    "duration_ms": getattr(llm_client, "last_request_duration_ms", None) or int((time.perf_counter() - request_started) * 1000),
                    "usage": getattr(llm_client, "last_usage", {}),
                })

            if action.action_type not in {"navigate", "click", "fill", "select", "extract", "complete", "escalate"}:
                await append_trace(DiscoveryStepResult(
                    step_number=step_num,
                    action=action,
                    observation_summary="The model proposed an unsupported action",
                    status="FAILED",
                    screenshot_path=screenshot_path,
                    observation_before=observation,
                    action_result={"status": "REJECTED", "classification": "UNSUPPORTED_ACTION"},
                    validation_result="UNSUPPORTED_ACTION",
                ))
                return False, trace, {"error": "UNSUPPORTED_ACTION", "details": "The proposed action is not supported."}

            allowed, safety_reason = self.safety_policy.validate_action(
                action.action_type,
                action.url if action.action_type == "navigate" else target_url,
                selector=action.selector or "",
                value=action.value or "",
            )
            if not allowed:
                await append_trace(DiscoveryStepResult(
                    step_number=step_num,
                    action=action,
                    observation_summary="Safety policy rejected the proposed action",
                    status="FAILED",
                    screenshot_path=screenshot_path,
                    observation_before=observation,
                    action_result={"status": "REJECTED", "classification": "SAFETY_VIOLATION"},
                    validation_result="SAFETY_VIOLATION",
                ))
                return False, trace, {"error": "SAFETY_VIOLATION", "details": default_safety_policy.sanitize_sensitive_data(safety_reason)}

            if action.action_type == "escalate":
                await append_trace(DiscoveryStepResult(
                    step_number=step_num,
                    action=action,
                    observation_summary="Agent requested human intervention",
                    status="ESCALATED",
                    screenshot_path=screenshot_path,
                    observation_before=observation,
                    action_result={"status": "BLOCKED", "classification": "HUMAN_INTERVENTION_REQUIRED"},
                    validation_result="HUMAN_INTERVENTION_REQUIRED",
                ))
                return False, trace, {"error": "HITL_REQUIRED", "reason": action.reason}

            action_result: Dict[str, Any] = {"status": "SUCCESS"}
            ui_action_executed = False
            step_status = "SUCCESS"
            action_error = None
            act_type = action.action_type

            try:
                if act_type == "navigate":
                    ui_action_executed = bool(await self.surface.navigate(action.url or ""))
                elif act_type == "click":
                    ui_action_executed = await self.surface.click(action.selector or "")
                elif act_type == "fill":
                    ui_action_executed = await self.surface.fill(action.selector or "", action.value or "")
                elif act_type == "select":
                    ui_action_executed = await self.surface.select(action.selector or "", action.value or "")
                elif act_type == "extract":
                    extracted_value = await self.surface.extract(action.selector or "")
                    ui_action_executed = extracted_value is not None
                    if extracted_value is not None and action.variable_name:
                        final_extracted[action.variable_name] = extracted_value
                    else:
                        step_status = "FAILED"
                        action_error = "The requested page value could not be extracted."
                        action_result = {"status": "FAILED", "classification": "EXTRACTION_EMPTY"}
                elif act_type == "complete":
                    if action.extracted_data:
                        final_extracted.update(action.extracted_data)
                    if "savings_balance" not in final_extracted:
                        balance = await self.surface.extract("#savings-balance-val")
                        if balance:
                            final_extracted["savings_balance"] = balance
                    for selector, key in (("#member-name-val", "member_name"), ("#member-id-val", "member_id")):
                        if key not in final_extracted:
                            value = await self.surface.extract(selector)
                            if value:
                                final_extracted[key] = value
                    goal_lower = goal.lower()
                    requested_member = re.search(r"\bmember(?:\s+id)?\s*#?\s*(\d{4,5})\b", goal, re.IGNORECASE)
                    expected_member_id = requested_member.group(1) if requested_member else None
                    invalid_member = expected_member_id and final_extracted.get("member_id") and str(final_extracted["member_id"]).strip() != expected_member_id
                    missing_balance = "savings" in goal_lower and "balance" in goal_lower and not final_extracted.get("savings_balance")
                    missing_profile = "profile" in goal_lower and not (final_extracted.get("member_name") and final_extracted.get("member_id"))
                    if missing_balance or missing_profile or invalid_member:
                        step_status = "FAILED"
                        action_error = "The requested member result was not verified against the live page."
                        action_result = {"status": "FAILED", "classification": "GOAL_NOT_VERIFIED"}
                    else:
                        success = True
                        step_status = "VERIFIED"
                        action_result = {"status": "VERIFIED", "output_fields": sorted(final_extracted.keys())}
                else:
                    step_status = "FAILED"
                    action_error = "Unsupported action type."
                    action_result = {"status": "REJECTED", "classification": "UNSUPPORTED_ACTION"}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                step_status = "FAILED"
                action_error = f"Action failed during {type(exc).__name__}."
                action_result = {"status": "FAILED", "classification": "BROWSER_ACTION_FAILED", "error_type": type(exc).__name__}

            if act_type in {"navigate", "click", "fill", "select"} and not ui_action_executed and step_status == "SUCCESS":
                step_status = "FAILED"
                action_error = f"The {act_type} action did not complete successfully."
                action_result = {"status": "FAILED", "classification": "BROWSER_ACTION_FAILED"}

            item = DiscoveryStepResult(
                step_number=step_num,
                action=action,
                observation_summary=f"{act_type} action {'executed' if ui_action_executed else 'evaluated'}",
                status=step_status,
                screenshot_path=None,
                observation_before=observation,
                action_result=action_result,
                observation_after=None,
                validation_result=step_status,
                ui_action_executed=ui_action_executed,
            )
            # Commit successful UI actions before taking a post-action screenshot or observation.
            await append_trace(item)

            after_path = os.path.join(evidence_run_dir, f"step_{step_num}_after.png")
            item.screenshot_path, after_screenshot_error = await self._capture_screenshot(after_path, run_id, step_num)
            try:
                item.observation_after = self._bound_observation(await self.surface.observe())
                if on_observation:
                    await on_observation(step_num, "AFTER_ACTION", item.observation_after, item.screenshot_path, after_screenshot_error)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                item.observation_after = {"observation_error": type(exc).__name__}
                if on_observation:
                    await on_observation(step_num, "AFTER_ACTION", item.observation_after, item.screenshot_path, after_screenshot_error)

            if step_status == "FAILED":
                # Keep the public trace detail bounded and value-free.
                return False, trace, {"error": "DISCOVERY_ACTION_FAILED", "details": action_error or "Discovery action failed."}
            if act_type == "complete" and success:
                break
            await asyncio.sleep(0.25)

        if not success:
            return False, trace, {"error": "MAX_STEPS_REACHED", "details": "Discovery reached its configured step limit without verifying the goal."}

        trace_json_path = os.path.join(evidence_run_dir, "discovery_trace.json")
        with open(trace_json_path, "w", encoding="utf-8") as stream:
            json.dump({
                "run_id": run_id,
                "provider": "mistral",
                "model": settings.MISTRAL_MODEL,
                "goal": self.safety_policy.sanitize_sensitive_data(goal),
                "success": success,
                "extracted_outputs": self.safety_policy.sanitize_sensitive_data(final_extracted),
                "steps": self.safety_policy.sanitize_sensitive_data([step.model_dump() for step in trace]),
            }, stream, indent=2)
        return True, trace, final_extracted
