from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field, model_validator

class TargetStrategy(BaseModel):
    model_config = {"extra": "forbid"}
    primary_selector: str = Field(..., min_length=1, max_length=300, description="Primary CSS selector or ID")
    fallback_selectors: List[str] = Field(default_factory=list, max_length=10, description="Ordered list of fallback locators")
    text_fallback: Optional[str] = Field(None, description="Visible text fallback locator")
    aria_fallback: Optional[str] = Field(None, description="Accessible label or role fallback")

class CheckpointRule(BaseModel):
    model_config = {"extra": "forbid"}
    rule_type: Literal["element_visible", "url_contains", "text_present", "title_contains"]
    target: str = Field(..., description="Selector, URL fragment, or text string to verify")
    description: Optional[str] = None

class ParameterDef(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(..., pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$", description="Parameter name, e.g. member_id")
    param_type: Literal["string", "integer", "number", "boolean"] = Field("string", description="Parameter data type")
    description: str = Field("", description="Description of the input parameter")
    required: bool = True
    default_value: Optional[str] = None

class OutputDef(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(..., pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$", description="Output property name, e.g. savings_balance")
    shape: Literal["string", "currency", "object"] = Field("string", description="Output shape: string | currency | object")
    selector: str = Field(..., min_length=1, max_length=300, description="CSS selector to extract value from")
    attribute: Optional[str] = Field(None, description="HTML attribute to extract (or innerText if None)")
    description: str = Field("")

class ReplayStep(BaseModel):
    model_config = {"extra": "forbid"}
    step_number: int = Field(..., ge=1)
    action_type: Literal["navigate", "click", "fill", "select", "extract", "assert"]
    target: TargetStrategy
    value_expression: Optional[str] = Field(None, description="Template expression e.g. ${inputs.member_id}")
    parameter_ref: Optional[str] = Field(None, description="Referenced input parameter name")
    checkpoints: List[CheckpointRule] = Field(default_factory=list)
    description: Optional[str] = None

class CapabilityArtifact(BaseModel):
    model_config = {"extra": "forbid"}
    schema_version: str = "1.0.0"
    capability_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    version: str = Field("1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    name: str
    description: str
    target_application: str = "APEX Federal"
    surface_type: str = "web"
    source_recording_id: Optional[str] = None
    parameters: List[ParameterDef] = Field(default_factory=list)
    outputs: List[OutputDef] = Field(default_factory=list)
    steps: List[ReplayStep] = Field(default_factory=list)
    success_condition: CheckpointRule
    safety_metadata: Dict[str, Any] = Field(default_factory=lambda: {"risk_level": "SAFE", "allowed_routes": ["/", "/member/*"]})
    creation_metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_artifact_references(self):
        parameter_names = [parameter.name for parameter in self.parameters]
        output_names = [output.name for output in self.outputs]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("Capability parameter names must be unique")
        if len(output_names) != len(set(output_names)):
            raise ValueError("Capability output names must be unique")
        output_names_set = set(output_names)
        for step in self.steps:
            if not step.parameter_ref:
                continue
            allowed_names = output_names_set if step.action_type == "extract" else set(parameter_names)
            if step.parameter_ref not in allowed_names:
                raise ValueError("Replay steps may only reference declared input parameters or extraction outputs")
        if self.target_application != "APEX Federal":
            raise ValueError("Only APEX Federal artifacts are supported by this local execution service")
        return self

    @classmethod
    def validate_for_publication(cls, value: Dict[str, Any]) -> "CapabilityArtifact":
        """Validate structure and enforce the invariants required before replay."""
        artifact = cls.model_validate(value)
        if not artifact.steps:
            raise ValueError("A capability must contain at least one replay step")
        sequences = [step.step_number for step in artifact.steps]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("Replay step numbers must be contiguous and ordered from 1")
        return artifact
