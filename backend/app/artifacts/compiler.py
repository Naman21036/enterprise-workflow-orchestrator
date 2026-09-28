from datetime import datetime, timezone
import re
from typing import List, Dict, Any, Tuple
from backend.app.discovery.actions import DiscoveryStepResult
from backend.app.artifacts.schema import (
    CapabilityArtifact, TargetStrategy, ReplayStep, CheckpointRule, ParameterDef, OutputDef
)
from backend.app.core.logging import logger

class ArtifactCompiler:
    def compile_trace(
        self,
        capability_id: str,
        name: str,
        description: str,
        goal: str,
        discovery_trace: List[DiscoveryStepResult],
        extracted_outputs: Dict[str, Any],
        target_app: str = "APEX Federal",
        source_recording_id: str | None = None,
        version: str = "1.0.0",
    ) -> CapabilityArtifact:
        logger.info("Compiling discovery trace into CapabilityArtifact", capability_id=capability_id)

        replay_steps: List[ReplayStep] = []
        parameters: List[ParameterDef] = []
        outputs: List[OutputDef] = []
        param_names_found = set()

        step_idx = 1
        requested_member = re.search(r"\bmember(?:\s+id)?\s*#?\s*(\d{4,5})\b", goal, re.IGNORECASE)
        requested_member_id = requested_member.group(1) if requested_member else None
        for trace_step in discovery_trace:
            act = trace_step.action
            act_type = act.action_type.lower()

            if not getattr(trace_step, "ui_action_executed", False) or act_type in ["complete", "escalate"]:
                continue

            # Identify parameters
            val_expr = None
            param_ref = None
            target_meaning = f"{act.selector or ''} {act.description or ''}".lower()
            is_member_parameter = (
                act_type in {"fill", "select"}
                and act.value is not None
                and act.parameter_name == "member_id"
                and re.fullmatch(r"\d{4,5}", act.value.strip()) is not None
            ) or (
                act_type in {"fill", "select"}
                and act.value is not None
                and requested_member_id is not None
                and act.value.strip() == requested_member_id
                and "member" in target_meaning
            )
            if act_type in {"fill", "select"} and act.value is not None:
                if is_member_parameter:
                    param_name = "member_id"
                    param_ref = param_name
                    val_expr = f"${{inputs.{param_name}}}"

                    if param_name not in param_names_found:
                        param_names_found.add(param_name)
                        parameters.append(ParameterDef(
                            name=param_name,
                            param_type="string",
                            description=f"Target {param_name} for banking operation",
                            required=True,
                            default_value=None
                        ))
                else:
                    val_expr = act.value

            # Build targeting strategies with robust fallbacks
            primary_sel = act.selector or act.url or "body"
            fallbacks = []
            text_fb = None
            aria_fb = None

            if primary_sel.startswith("#"):
                elem_name = primary_sel[1:].replace("-", "_")
                fallbacks.append(f'input[name="{elem_name}"]')
                fallbacks.append(f'button[id="{primary_sel[1:]}"]')
                fallbacks.append(f'[aria-label="{act.description or elem_name}"]')

            if act.description and any(kw in act.description.lower() for kw in ["search", "submit"]):
                text_fb = "Search"

            target_strat = TargetStrategy(
                primary_selector=primary_sel,
                fallback_selectors=fallbacks,
                text_fallback=text_fb,
                aria_fallback=aria_fb
            )

            # Build step checkpoints
            checkpoints = []
            if act_type == "click" and "search" in primary_sel.lower():
                checkpoints.append(CheckpointRule(
                    rule_type="url_contains",
                    target="/member/",
                    description="Verify navigation to member details page"
                ))

            replay_steps.append(ReplayStep(
                step_number=step_idx,
                action_type=act_type,
                target=target_strat,
                value_expression=val_expr,
                parameter_ref=param_ref,
                checkpoints=checkpoints,
                description=act.description or f"Execute {act_type} on {primary_sel}"
            ))
            step_idx += 1

        # Declare outputs for the concrete read-only task being learned.
        is_profile_lookup = "profile" in goal.lower()
        if not is_profile_lookup:
            outputs.append(OutputDef(
                name="savings_balance",
                shape="currency",
                selector="#savings-balance-val",
                description="Extracted member savings balance"
            ))
        outputs.append(OutputDef(
            name="member_name",
            shape="string",
            selector="#member-name-val",
            description="Extracted member full name"
        ))
        if is_profile_lookup:
            outputs.append(OutputDef(
                name="member_status",
                shape="string",
                selector=".status-badge",
                description="Member account status"
            ))

        artifact = CapabilityArtifact(
            schema_version="1.0.0",
            capability_id=capability_id,
            version=version,
            name=name,
            description=description,
            target_application=target_app,
            surface_type="web",
            source_recording_id=source_recording_id,
            parameters=parameters,
            outputs=outputs,
            steps=replay_steps,
            success_condition=CheckpointRule(
                rule_type="element_visible",
                target="#member-name-val" if is_profile_lookup else "#savings-balance-val",
                description="Verify member profile is visible" if is_profile_lookup else "Verify savings balance element is visible"
            ),
            creation_metadata={
                "compiled_at": datetime.now(timezone.utc).isoformat(),
                "goal": goal,
                "source_recording_id": source_recording_id,
                "discovered_step_count": len(discovery_trace)
            }
        )

        return artifact
