import os
import re
import json
import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Tuple, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.db.models import RunModel, RunStepModel, HandoffRecordModel, DiscoveryRecordingModel, RecordingEventModel, WorkflowIdempotencyModel
from backend.app.artifacts.storage import ArtifactStorage
from backend.app.artifacts.compiler import ArtifactCompiler
from backend.app.artifacts.schema import CapabilityArtifact
from backend.app.surfaces.playwright import PlaywrightWebSurface
from backend.app.discovery.agent import MistralDiscoveryAgent
from backend.app.replay.engine import DeterministicReplayEngine
from backend.app.escalation.manager import session_manager
from backend.app.core.config import settings
from backend.app.core.logging import logger
from backend.app.core.errors import execution_outcome_category
from backend.app.safety.policy import default_safety_policy

_run_slots = asyncio.Semaphore(max(1, settings.MAX_CONCURRENT_RUNS))
_RECORDING_TRANSITIONS = {
    "CREATED": {"RECORDING", "FAILED", "CANCELLED"},
    "RECORDING": {"DISCOVERY_COMPLETED", "BLOCKED", "FAILED", "CANCELLED"},
    "DISCOVERY_COMPLETED": {"COMPILED", "FAILED"},
    "COMPILED": {"PUBLISHED", "FAILED"},
    "PUBLISHED": set(),
    "BLOCKED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}

class WorkflowRouter:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage = ArtifactStorage(db)
        self.compiler = ArtifactCompiler()

    @staticmethod
    def _transition_recording(recording: DiscoveryRecordingModel, target: str) -> None:
        if target not in _RECORDING_TRANSITIONS.get(recording.status, set()):
            raise ValueError(f"Invalid recording lifecycle transition: {recording.status} -> {target}")
        recording.status = target

    async def _idempotent_duplicate(self, submission: WorkflowIdempotencyModel, request_hash: str) -> Dict[str, Any]:
        if submission.request_hash != request_hash:
            return {
                "status": "FAILED",
                "error_code": "IDEMPOTENCY_KEY_CONFLICT",
                "error": "This Idempotency-Key was already used with a different workflow request.",
                "llm_decision_calls": 0,
            }
        run = await self.db.get(RunModel, submission.run_id)
        if not run:
            return {"status": "FAILED", "error_code": "IDEMPOTENCY_RECORD_INCONSISTENT", "error": "The idempotency record has no associated workflow run."}
        rec_result = await self.db.execute(
            select(DiscoveryRecordingModel.id).where(DiscoveryRecordingModel.run_id == run.id)
        )
        return {
            "run_id": run.id,
            "recording_id": rec_result.scalar_one_or_none(),
            "goal": run.goal,
            "execution_mode": run.execution_mode,
            "capability_id": run.capability_id,
            "capability_version": run.capability_version,
            "status": run.status,
            "duration_seconds": run.duration_seconds,
            "outputs": {},
            "step_logs": [],
            "error_code": run.error_code,
            "error": run.error_message,
            "llm_decision_calls": 0 if run.execution_mode == "Deterministic Replay" else None,
            "duplicate_request": True,
        }

    async def _claim_run(
        self,
        run: RunModel,
        key_hash: Optional[str],
        request_hash: str,
    ) -> Optional[Dict[str, Any]]:
        self.db.add(run)
        if key_hash:
            self.db.add(WorkflowIdempotencyModel(key_hash=key_hash, request_hash=request_hash, run_id=run.id))
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            if not key_hash:
                raise
            result = await self.db.execute(
                select(WorkflowIdempotencyModel).where(WorkflowIdempotencyModel.key_hash == key_hash)
            )
            existing = result.scalar_one_or_none()
            if existing:
                return await self._idempotent_duplicate(existing, request_hash)
            raise
        return None

    @staticmethod
    def _sanitize_recording_value(value):
        if isinstance(value, dict):
            return {
                str(key): ("[UI text redacted]" if str(key).lower() in {"page_text_summary", "text", "inner_text"} else WorkflowRouter._sanitize_recording_value(item))
                for key, item in value.items()
                if str(key).lower() not in {"value", "member_id", "account_number", "ssn", "token", "password", "authorization", "api_key", "cookie"}
            }
        if isinstance(value, list):
            return [WorkflowRouter._sanitize_recording_value(item) for item in value]
        if isinstance(value, str):
            value = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[REDACTED]', value)
            value = re.sub(r'(?i)\b\d{3}[- ]?\d{2}[- ]?\d{4}\b', '[REDACTED]', value)
            value = re.sub(r'\$\s?[\d,]+(?:\.\d{2})?', '[FINANCIAL DATA REDACTED]', value)
            value = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[EMAIL REDACTED]', value)
            return default_safety_policy.sanitize_sensitive_data(value)
        return value

    @staticmethod
    def _recorded_actions(trace):
        """Return only successfully executed UI actions, with sensitive values removed."""
        return [
            WorkflowRouter._recorded_step(item)
            for item in trace
            if getattr(item, "ui_action_executed", False)
        ]

    @staticmethod
    def _recorded_step(item):
        """Serialize an agent decision/event without retaining sensitive values."""
        clean = WorkflowRouter._sanitize_recording_value

        return {
            "sequence": item.step_number,
            "action_type": item.action.action_type,
            "why": f"Discovery selected a {item.action.action_type} action.",
            "target": clean(item.action.selector or item.action.url),
            "parameters": {"value": "[REDACTED]"} if item.action.value else {},
            "observation_before": clean(item.observation_before or {"summary": item.observation_summary}),
            "result": clean(item.action_result or {"status": item.status}),
            "observation_after": clean(item.observation_after or {}),
            "validation": item.validation_result or item.status,
            "screenshot_path": item.screenshot_path,
            "error_classification": clean(item.action.reason) if item.status in {"FAILED", "ESCALATED"} else None,
        }

    async def lookup_capability(self, goal: str, target_app: str = "APEX Federal", capability_id: str | None = None) -> Optional[CapabilityArtifact]:
        """Check capability registry for existing matching capability artifact."""
        caps = await self.storage.list_capabilities()
        goal_lower = goal.lower()

        for c in caps:
            cap_id = c["capability_id"]
            if not c.get("is_active", True) or c.get("target_app") != target_app:
                continue
            if capability_id:
                if capability_id == cap_id:
                    artifact = await self.storage.get_artifact(cap_id, c["latest_version"])
                    return CapabilityArtifact.validate_for_publication(artifact.model_dump()) if artifact else None
                continue
            if "savings" in goal_lower and "balance" in goal_lower:
                if cap_id == "member_savings_lookup":
                    artifact = await self.storage.get_artifact(cap_id, c["latest_version"])
                    return CapabilityArtifact.validate_for_publication(artifact.model_dump()) if artifact else None
            elif "profile" in goal_lower:
                if cap_id == "member_profile_lookup":
                    artifact = await self.storage.get_artifact(cap_id, c["latest_version"])
                    return CapabilityArtifact.validate_for_publication(artifact.model_dump()) if artifact else None

        return None

    def extract_member_id_from_goal(self, goal: str, inputs: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if inputs and "member_id" in inputs and inputs["member_id"]:
            value = str(inputs["member_id"]).strip()
            return value if re.fullmatch(r"\d{4,5}", value) else None

        match = re.search(r"\bmember(?:\s+id)?\s*#?\s*(\d{4,5})\b", goal, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    async def execute_goal(
        self,
        goal: str,
        target_app: str = "APEX Federal",
        input_parameters: Optional[Dict[str, Any]] = None,
        force_mode: Optional[str] = None,
        requested_capability_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        async with _run_slots:
            return await self._execute_goal(goal, target_app, input_parameters, force_mode, requested_capability_id, idempotency_key)

    async def _execute_goal(
        self,
        goal: str,
        target_app: str = "APEX Federal",
        input_parameters: Optional[Dict[str, Any]] = None,
        force_mode: Optional[str] = None,
        requested_capability_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        persisted_goal = default_safety_policy.sanitize_sensitive_data(goal)
        logger.info("Routing workflow goal request", goal=persisted_goal, target_app=target_app, input_names=sorted((input_parameters or {}).keys()))

        member_id = self.extract_member_id_from_goal(goal, input_parameters)
        if not goal.strip():
            return {"status": "FAILED", "error_code": "GOAL_REQUIRED", "error": "A non-empty workflow goal is required.", "llm_decision_calls": 0}
        if target_app != "APEX Federal":
            return {"status": "FAILED", "error_code": "UNSUPPORTED_TARGET", "error": "Only the configured APEX Federal target application is allowed.", "llm_decision_calls": 0}
        if force_mode and force_mode.upper() not in {"DISCOVERY", "REPLAY"}:
            return {"status": "FAILED", "error_code": "INVALID_EXECUTION_MODE", "error": "force_mode must be DISCOVERY or REPLAY.", "llm_decision_calls": 0}
        if not member_id:
            return {"status": "FAILED", "error_code": "MEMBER_ID_REQUIRED", "error": "Provide a four or five digit member ID in the goal or input_parameters.member_id.", "llm_decision_calls": 0}
        bound_inputs = {"member_id": member_id}
        if input_parameters:
            bound_inputs.update(input_parameters)

        key_hash = None
        if idempotency_key is not None:
            if not re.fullmatch(r"[\x21-\x7e]{8,255}", idempotency_key):
                return {"status": "FAILED", "error_code": "IDEMPOTENCY_KEY_INVALID", "error": "Idempotency-Key must be 8 to 255 visible ASCII characters.", "llm_decision_calls": 0}
            key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        request_fingerprint = json.dumps({
            "goal": goal.strip(),
            "target_app": target_app,
            "inputs": bound_inputs,
            "force_mode": force_mode.upper() if force_mode else None,
            "capability_id": requested_capability_id,
        }, sort_keys=True, separators=(",", ":"), default=str)
        request_hash = hashlib.sha256(request_fingerprint.encode("utf-8")).hexdigest()
        if key_hash:
            existing_result = await self.db.execute(
                select(WorkflowIdempotencyModel).where(WorkflowIdempotencyModel.key_hash == key_hash)
            )
            existing_submission = existing_result.scalar_one_or_none()
            if existing_submission:
                return await self._idempotent_duplicate(existing_submission, request_hash)

        # Capability Lookup
        matching_artifact = None
        if not force_mode or force_mode.upper() == "REPLAY":
            matching_artifact = await self.lookup_capability(goal, target_app, requested_capability_id)

        if force_mode and force_mode.upper() == "REPLAY" and not matching_artifact:
            return {
                "status": "FAILED",
                "execution_mode": "Deterministic Replay",
                "error": "No active, compatible, validated capability is available for this replay request.",
                "llm_decision_calls": 0,
            }

        run_id = f"wf_{os.urandom(4).hex()}"
        start_time = datetime.now(timezone.utc)

        if matching_artifact and (not force_mode or force_mode.upper() != "DISCOVERY"):
            # ROUTE TO DETERMINISTIC REPLAY (LLM-FREE)
            execution_mode = "Deterministic Replay"
            capability_id = matching_artifact.capability_id
            capability_version = matching_artifact.version

            run_model = RunModel(
                id=run_id,
                goal=persisted_goal,
                target_app=target_app,
                execution_mode=execution_mode,
                status="RUNNING",
                capability_id=capability_id,
                capability_version=capability_version,
                created_at=start_time
            )
            duplicate = await self._claim_run(run_model, key_hash, request_hash)
            if duplicate:
                return duplicate

            surface = PlaywrightWebSurface(headless=True)
            session_manager.register_surface(run_id, surface)
            engine = DeterministicReplayEngine(surface)

            try:
                result_status, step_logs, outputs, error_msg = await engine.execute_replay(
                    artifact=matching_artifact,
                    inputs=bound_inputs,
                    run_id=run_id
                )

                finish_time = datetime.now(timezone.utc)
                dur = (finish_time - start_time).total_seconds()
                replay_run_id = None
                replay_result = None

                run_model.status = result_status
                run_model.finished_at = finish_time
                run_model.duration_seconds = dur
                run_model.result_json = {"output_fields": sorted(outputs.keys()), "values_redacted": True}
                run_model.error_message = error_msg
                run_model.error_code = (
                    outputs.get("error_code") if result_status == "BUSINESS_OUTCOME"
                    else error_msg.split(":", 1)[0] if error_msg and ":" in error_msg
                    else None
                )

                # Persist step logs
                for s in step_logs:
                    self.db.add(RunStepModel(
                        run_id=run_id,
                        step_number=s["step_number"],
                        action_type=s["action_type"],
                        target_description=s.get("target_selector"),
                        status=s["status"],
                        duration_ms=s.get("duration_ms", 0),
                        error=s.get("error"),
                        screenshot_path=s.get("screenshot_path")
                    ))

                if result_status == "BLOCKED":
                    # Create Handoff Record
                    handoff = HandoffRecordModel(
                        run_id=run_id,
                        status="AWAITING_HUMAN",
                        reason=error_msg or "Unexpected dialog blocking execution",
                        step_number=step_logs[-1]["step_number"] if step_logs else 1,
                        screenshot_path=step_logs[-1].get("screenshot_path") if step_logs else None
                    )
                    self.db.add(handoff)
                    session_manager.set_handoff_state(run_id, {
                        "run_id": run_id,
                        "status": "AWAITING_HUMAN",
                        "reason": error_msg,
                        "step_number": handoff.step_number,
                        "screenshot_path": handoff.screenshot_path
                    })
                else:
                    await surface.close()

                await self.db.commit()

                return {
                    "run_id": run_id,
                    "goal": goal,
                    "execution_mode": execution_mode,
                    "capability_id": capability_id,
                    "capability_version": capability_version,
                    "status": result_status,
                    "outcome_category": execution_outcome_category(result_status, run_model.error_code),
                    "duration_seconds": dur,
                    "outputs": outputs,
                    "step_logs": step_logs,
                    "error_code": run_model.error_code,
                    "error": error_msg,
                    "llm_decision_calls": 0,
                }

            except Exception as e:
                await surface.close()
                error_code = "PERMISSION_DENIED" if isinstance(e, PermissionError) else "REPLAY_EXECUTION_ERROR"
                operation = surface.last_operation or "workflow_execution"
                safe_error = default_safety_policy.sanitize_sensitive_data(str(e))[:300]
                error_detail = (
                    "PERMISSION_DENIED: The operating system denied a replay operation. Check permissions for the reported operation."
                    if isinstance(e, PermissionError)
                    else f"REPLAY_EXECUTION_ERROR: {operation} failed with {type(e).__name__}: {safe_error}"
                )
                logger.error("Replay execution failed", error_code=error_code, operation=operation, error_type=type(e).__name__, diagnostic=safe_error)
                run_model.status = "FAILED"
                run_model.error_code = error_code
                run_model.error_message = error_detail
                run_model.finished_at = datetime.now(timezone.utc)
                run_model.duration_seconds = (run_model.finished_at - start_time).total_seconds()
                await self.db.commit()
                return {"run_id": run_id, "status": "FAILED", "outcome_category": execution_outcome_category("FAILED", error_code), "error_code": error_code, "error": error_detail, "llm_decision_calls": 0}

        else:
            # ROUTE TO MISTRAL AI DISCOVERY
            execution_mode = "Discovery"
            cap_id = f"cap_{os.urandom(3).hex()}"

            run_model = RunModel(
                id=run_id,
                goal=persisted_goal,
                target_app=target_app,
                execution_mode=execution_mode,
                status="RUNNING",
                capability_id=cap_id,
                capability_version="1.0.0",
                created_at=start_time
            )
            duplicate = await self._claim_run(run_model, key_hash, request_hash)
            if duplicate:
                return duplicate

            recording_id = f"rec_{os.urandom(5).hex()}"
            recording = DiscoveryRecordingModel(
                id=recording_id,
                run_id=run_id,
                goal=persisted_goal,
                target_application=target_app,
                status="CREATED",
                started_at=start_time,
                actions_json=[],
                checkpoints_json=[],
            )
            self.db.add(recording)
            await self.db.commit()
            self._transition_recording(recording, "RECORDING")
            await self.db.commit()

            mistral_key = settings.MISTRAL_API_KEY or ""
            if not mistral_key or "your_mistral_api_key" in mistral_key.lower():
                config_error = "Discovery is unavailable: configure MISTRAL_API_KEY in the local environment."
                finished = datetime.now(timezone.utc)
                self._transition_recording(recording, "FAILED")
                recording.completed_at = finished
                recording.checkpoints_json = [{"outcome": "FAILED", "classification": "CONFIGURATION_ERROR", "reason": config_error}]
                run_model.status = "FAILED"
                run_model.finished_at = finished
                run_model.duration_seconds = (finished - start_time).total_seconds()
                run_model.error_message = config_error
                run_model.error_code = "CONFIGURATION_ERROR"
                await self.db.commit()
                return {
                    "run_id": run_id,
                    "recording_id": recording_id,
                    "goal": goal,
                    "execution_mode": "Discovery",
                    "capability_id": cap_id,
                    "capability_version": "1.0.0",
                    "status": "FAILED",
                    "error_code": "CONFIGURATION_ERROR",
                    "duration_seconds": run_model.duration_seconds,
                    "outputs": {},
                    "step_logs": [],
                    "error": config_error,
                    "llm_decision_calls": 0,
                }

            surface = PlaywrightWebSurface(headless=True)
            session_manager.register_surface(run_id, surface)
            agent = MistralDiscoveryAgent(surface)
            replay_run_id = None
            replay_result = None
            artifact_to_mirror = None
            event_sequence = 0

            async def save_recording_event(event_type: str, payload: dict, evidence_path: Optional[str] = None):
                nonlocal event_sequence
                event_sequence += 1
                self.db.add(RecordingEventModel(
                    recording_id=recording_id,
                    sequence=event_sequence,
                    event_type=event_type,
                    payload_json=self._sanitize_recording_value(payload),
                    evidence_path=evidence_path,
                ))
                await self.db.commit()

            async def persist_recording_event(step):
                event_payload = self._recorded_step(step)
                if step.ui_action_executed:
                    recording.actions_json = [*(recording.actions_json or []), event_payload]
                    event_type = "BROWSER_ACTION_SUCCEEDED"
                elif step.status == "ESCALATED":
                    event_type = "HUMAN_INTERVENTION_REQUIRED"
                elif step.status == "FAILED":
                    event_type = "DISCOVERY_STEP_FAILED"
                else:
                    event_type = "DISCOVERY_DECISION"
                await save_recording_event(event_type, event_payload, step.screenshot_path)
                self.db.add(RunStepModel(
                    run_id=run_id,
                    step_number=step.step_number,
                    action_type=step.action.action_type,
                    target_description=step.action.selector or step.action.url,
                    status=step.status,
                    duration_ms=0,
                    error=step.action.reason if step.status in {"FAILED", "ESCALATED"} else None,
                    screenshot_path=step.screenshot_path,
                    observation_json={"summary": step.observation_summary},
                ))
                await self.db.commit()

            async def persist_observation(step_number: int, phase: str, observation: dict, screenshot_path: Optional[str], evidence_error: Optional[str]):
                payload = {
                    "step_number": step_number,
                    "phase": phase,
                    "observation": observation,
                    "evidence_error": evidence_error,
                }
                await save_recording_event("OBSERVATION", payload, screenshot_path)
                if phase == "AFTER_ACTION":
                    actions = list(recording.actions_json or [])
                    for action in actions:
                        if action.get("sequence") == step_number:
                            updated = dict(action)
                            updated["observation_after"] = self._sanitize_recording_value(observation)
                            updated["screenshot_path"] = screenshot_path
                            if evidence_error:
                                updated["evidence_error"] = evidence_error
                            actions[actions.index(action)] = updated
                            break
                    recording.actions_json = actions
                    await self.db.commit()

            async def persist_provider_event(payload: dict):
                await save_recording_event("MODEL_DECISION", payload)

            try:
                async with asyncio.timeout(max(1, settings.MAX_DISCOVERY_DURATION_SECONDS)):
                    success, trace, discovery_res = await agent.run_discovery(
                        goal=goal,
                        target_url=settings.TARGET_APP_URL,
                        run_id=run_id,
                        on_step=persist_recording_event,
                        on_observation=persist_observation,
                        on_provider_event=persist_provider_event,
                    )

                finish_time = datetime.now(timezone.utc)
                dur = (finish_time - start_time).total_seconds()

                if success:
                    # Compile artifact from successful discovery trace
                    goal_kind = goal.lower()
                    if "savings" in goal_kind and "balance" in goal_kind:
                        compiled_cap_id = "member_savings_lookup"
                    elif "profile" in goal_kind:
                        compiled_cap_id = "member_profile_lookup"
                    else:
                        compiled_cap_id = cap_id
                    artifact_version = await self.storage.next_version(compiled_cap_id)
                    artifact = self.compiler.compile_trace(
                        capability_id=compiled_cap_id,
                        name=f"Capability for: {persisted_goal[:40]}",
                        description=f"Auto-compiled capability artifact for '{persisted_goal}'",
                        goal=persisted_goal,
                        discovery_trace=trace,
                        extracted_outputs=discovery_res,
                        target_app=target_app,
                        source_recording_id=recording_id,
                        version=artifact_version,
                    )
                    artifact = CapabilityArtifact.validate_for_publication(artifact.model_dump())
                    self._transition_recording(recording, "DISCOVERY_COMPLETED")
                    recording.completed_at = finish_time
                    recording.actions_json = self._recorded_actions(trace)
                    self._transition_recording(recording, "COMPILED")
                    recording.checkpoints_json = [
                        {"sequence": a["sequence"], "outcome": a["validation"], "evidence": a["screenshot_path"]}
                        for a in recording.actions_json
                    ]

                    run_model.error_code = None
                    run_model.capability_id = compiled_cap_id
                    run_model.duration_seconds = dur
                    run_model.result_json = {"output_fields": sorted(discovery_res.keys()), "values_redacted": True}

                    # Prove the compiled capability on a clean browser context.
                    await surface.close()
                    replay_run_id = f"wf_replay_{os.urandom(4).hex()}"
                    replay_started = datetime.now(timezone.utc)
                    replay_run = RunModel(
                        id=replay_run_id,
                        goal=persisted_goal,
                        target_app=target_app,
                        execution_mode="Deterministic Replay",
                        status="RUNNING",
                        capability_id=compiled_cap_id,
                        capability_version=artifact.version,
                        created_at=replay_started,
                    )
                    self.db.add(replay_run)
                    await self.db.commit()
                    replay_surface = PlaywrightWebSurface(headless=True)
                    try:
                        replay_status, replay_steps, replay_outputs, replay_error = await DeterministicReplayEngine(replay_surface).execute_replay(
                            artifact=artifact,
                            inputs=bound_inputs,
                            run_id=replay_run_id,
                        )
                    finally:
                        await replay_surface.close()
                    replay_finished = datetime.now(timezone.utc)
                    replay_run.status = replay_status
                    replay_run.finished_at = replay_finished
                    replay_run.duration_seconds = (replay_finished - replay_started).total_seconds()
                    replay_run.result_json = {"output_fields": sorted(replay_outputs.keys()), "values_redacted": True}
                    replay_run.error_message = replay_error
                    for step in replay_steps:
                        self.db.add(RunStepModel(
                            run_id=replay_run_id,
                            step_number=step["step_number"],
                            action_type=step["action_type"],
                            target_description=step.get("target_selector"),
                            status=step["status"],
                            duration_ms=step.get("duration_ms", 0),
                            error=step.get("error"),
                            screenshot_path=step.get("screenshot_path"),
                        ))
                    replay_result = {"status": replay_status, "outputs": replay_outputs, "error": replay_error, "step_logs": replay_steps}
                    if replay_status == "SUCCESS":
                        self._transition_recording(recording, "PUBLISHED")
                        recording.artifact_id = artifact.capability_id
                        recording.artifact_version = artifact.version
                        await self.storage.save_artifact(artifact, commit=False)
                        artifact_to_mirror = artifact
                        run_model.status = "SUCCESS"
                    else:
                        self._transition_recording(recording, "FAILED")
                        recording.checkpoints_json.append({
                            "outcome": "FAILED",
                            "classification": "ARTIFACT_VALIDATION_FAILED",
                            "reason": default_safety_policy.sanitize_sensitive_data(replay_error or "Compiled artifact did not pass clean-context replay."),
                        })
                        run_model.status = replay_status
                        run_model.error_message = replay_error
                        run_model.error_code = replay_error.split(":", 1)[0] if replay_error and ":" in replay_error else None
                    recording.linked_replay_runs_json = [replay_run_id]
                    finish_time = replay_finished
                    dur = (finish_time - start_time).total_seconds()
                    run_model.finished_at = finish_time
                    run_model.duration_seconds = dur
                    run_model.result_json = {"output_fields": sorted(replay_outputs.keys()), "values_redacted": True}
                elif discovery_res.get("error") == "HITL_REQUIRED":
                    self._transition_recording(recording, "BLOCKED")
                    recording.completed_at = finish_time
                    recording.actions_json = self._recorded_actions(trace)
                    recording.checkpoints_json = [{"outcome": "BLOCKED", "classification": "HUMAN_INTERVENTION_REQUIRED", "reason": default_safety_policy.sanitize_sensitive_data(discovery_res.get("reason", "Human intervention required."))}]
                    run_model.status = "BLOCKED"
                    run_model.error_code = "HUMAN_INTERVENTION_REQUIRED"
                    run_model.finished_at = finish_time
                    run_model.duration_seconds = dur
                    run_model.error_message = discovery_res.get("reason")

                    handoff = HandoffRecordModel(
                        run_id=run_id,
                        status="AWAITING_HUMAN",
                        reason=discovery_res.get("reason", "Confirmation required"),
                        step_number=trace[-1].step_number if trace else 1,
                        screenshot_path=trace[-1].screenshot_path if trace else None
                    )
                    self.db.add(handoff)
                    session_manager.set_handoff_state(run_id, {
                        "run_id": run_id,
                        "status": "AWAITING_HUMAN",
                        "reason": discovery_res.get("reason"),
                        "step_number": handoff.step_number,
                        "screenshot_path": handoff.screenshot_path
                    })
                else:
                    self._transition_recording(recording, "FAILED")
                    recording.completed_at = finish_time
                    recording.actions_json = self._recorded_actions(trace)
                    recording.checkpoints_json = [{
                        "outcome": "FAILED",
                        "classification": discovery_res.get("error", "DISCOVERY_FAILED"),
                        "reason": default_safety_policy.sanitize_sensitive_data(str(discovery_res.get("details") or discovery_res.get("error") or "Discovery did not complete.")),
                    }]
                    run_model.status = "FAILED"
                    discovery_code = str(discovery_res.get("error") or "DISCOVERY_FAILED")
                    details = str(discovery_res.get("details") or "")
                    if discovery_code == "LLM_ERROR" and "MISTRAL_RATE_LIMITED" in details:
                        discovery_code = "MISTRAL_RATE_LIMITED"
                    run_model.error_code = discovery_code
                    run_model.finished_at = finish_time
                    run_model.duration_seconds = dur
                    run_model.error_message = default_safety_policy.sanitize_sensitive_data(
                        details or discovery_code
                    )
                    await surface.close()

                await self.db.commit()
                if artifact_to_mirror is not None:
                    self.storage.write_artifact_mirror(artifact_to_mirror)

                return {
                    "run_id": run_id,
                    "recording_id": recording_id,
                    "replay_run_id": replay_run_id,
                    "goal": goal,
                    "execution_mode": execution_mode,
                    "capability_id": run_model.capability_id,
                    "capability_version": "1.0.0",
                    "status": run_model.status,
                    "outcome_category": execution_outcome_category(run_model.status, run_model.error_code),
                    "duration_seconds": dur,
                    "outputs": replay_result["outputs"] if replay_result else discovery_res,
                    "step_logs": [t.model_dump() for t in trace],
                    "error_code": run_model.error_code,
                    "error": run_model.error_message,
                    "llm_decision_calls": agent.llm_decision_calls,
                }

            except asyncio.CancelledError:
                finished = datetime.now(timezone.utc)
                run_model.status = "CANCELLED"
                run_model.error_code = "RUN_CANCELLED"
                run_model.error_message = "Discovery was cancelled before completion."
                run_model.finished_at = finished
                run_model.duration_seconds = (finished - start_time).total_seconds()
                self._transition_recording(recording, "CANCELLED")
                recording.completed_at = finished
                recording.checkpoints_json = [{"outcome": "CANCELLED", "classification": "RUN_CANCELLED"}]
                try:
                    await asyncio.shield(self.db.commit())
                    await asyncio.shield(surface.close())
                finally:
                    raise
            except Exception as e:
                await surface.close()
                await self.db.rollback()
                run_model = await self.db.get(RunModel, run_id)
                recording = await self.db.get(DiscoveryRecordingModel, recording_id)
                error_detail = f"{type(e).__name__}: {e}" or type(e).__name__
                logger.error("Discovery execution failed", error=default_safety_policy.sanitize_sensitive_data(error_detail))
                run_model.status = "FAILED"
                run_model.error_code = "PERMISSION_DENIED" if isinstance(e, PermissionError) else "DISCOVERY_TIMEOUT" if isinstance(e, TimeoutError) else "DISCOVERY_EXECUTION_ERROR"
                run_model.error_message = (
                    "PERMISSION_DENIED: The operating system denied a discovery operation. Check permissions for the reported operation."
                    if isinstance(e, PermissionError)
                    else "DISCOVERY_TIMEOUT: Discovery exceeded its configured time limit."
                    if isinstance(e, TimeoutError)
                    else f"DISCOVERY_EXECUTION_ERROR: Discovery stopped because of an unexpected {type(e).__name__}."
                )
                run_model.finished_at = datetime.now(timezone.utc)
                run_model.duration_seconds = (run_model.finished_at - start_time).total_seconds()
                self._transition_recording(recording, "FAILED")
                recording.completed_at = run_model.finished_at
                # Keep any interaction events already committed by the callback.
                # A transient provider or browser error must not erase the trace.
                recording.actions_json = recording.actions_json or self._recorded_actions([])
                safe_error_detail = default_safety_policy.sanitize_sensitive_data(error_detail)
                recording.checkpoints_json = [{"outcome": "FAILED", "classification": run_model.error_code, "reason": run_model.error_message}]
                await self.db.commit()
                return {"run_id": run_id, "recording_id": recording_id, "execution_mode": "Discovery", "status": "FAILED", "outcome_category": execution_outcome_category("FAILED", run_model.error_code), "error_code": run_model.error_code, "error": run_model.error_message}
