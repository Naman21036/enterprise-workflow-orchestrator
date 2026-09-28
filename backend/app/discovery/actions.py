from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: Literal["navigate", "click", "fill", "select", "extract", "complete", "escalate"] = Field(..., description="One supported next action")
    selector: Optional[str] = Field(None, description="CSS selector or element ID for element interaction")
    value: Optional[str] = Field(None, description="Value to type or select")
    url: Optional[str] = Field(None, description="URL for navigate action")
    variable_name: Optional[str] = Field(None, description="Variable name to assign extracted output value")
    parameter_name: Optional[str] = Field(None, description="Suggested parameter name if value represents dynamic input (e.g. member_id)")
    description: Optional[str] = Field(None, description="Human readable reasoning for action")
    extracted_data: Optional[Dict[str, Any]] = Field(None, description="Extracted key-value outputs on task completion")
    reason: Optional[str] = Field(None, description="Reason for escalation or failure")

    @model_validator(mode="after")
    def validate_action_arguments(self):
        action = self.action_type
        if action == "navigate" and not self.url:
            raise ValueError("navigate requires a URL")
        if action in {"click", "fill", "select", "extract"} and not self.selector:
            raise ValueError(f"{action} requires a selector")
        if action in {"fill", "select"} and self.value is None:
            raise ValueError(f"{action} requires a value")
        if action == "extract" and not self.variable_name:
            raise ValueError("extract requires a variable_name")
        if action == "escalate" and not self.reason:
            raise ValueError("escalate requires a reason")
        return self

class DiscoveryStepResult(BaseModel):
    step_number: int
    action: AgentAction
    observation_summary: str
    status: str  # SUCCESS | FAILED | ESCALATED
    screenshot_path: Optional[str] = None
    observation_before: Optional[Dict[str, Any]] = None
    action_result: Optional[Dict[str, Any]] = None
    observation_after: Optional[Dict[str, Any]] = None
    validation_result: Optional[str] = None
    ui_action_executed: bool = False
