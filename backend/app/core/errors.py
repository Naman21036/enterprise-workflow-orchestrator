class OrchestrationException(Exception):
    """Base exception for computer use automation system."""
    def __init__(self, message: str, code: str = "INTERNAL_ERROR", details: dict = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

class CapabilityNotFoundError(OrchestrationException):
    def __init__(self, capability_id: str):
        super().__init__(
            message=f"Capability '{capability_id}' not found.",
            code="CAPABILITY_NOT_FOUND",
            details={"capability_id": capability_id}
        )


class ArtifactValidationError(OrchestrationException):
    def __init__(self, capability_id: str, version: str | None = None):
        label = f"{capability_id} v{version}" if version else capability_id
        super().__init__(
            message=f"Stored capability artifact '{label}' failed integrity validation.",
            code="ARTIFACT_VALIDATION_FAILED",
            details={"capability_id": capability_id, "version": version},
        )

class SafetyViolationError(OrchestrationException):
    def __init__(self, reason: str, action: str = None):
        super().__init__(
            message=f"Safety Policy Violation: {reason}",
            code="SAFETY_VIOLATION",
            details={"reason": reason, "action": action}
        )

class ReplayExecutionError(OrchestrationException):
    def __init__(self, step_number: int, reason: str, locator: str = None):
        super().__init__(
            message=f"Replay failed at step {step_number}: {reason}",
            code="REPLAY_FAILED",
            details={"step_number": step_number, "reason": reason, "locator": locator}
        )

class BusinessOutcomeException(OrchestrationException):
    """Represents expected business outcome (e.g. member not found). Not a system failure."""
    def __init__(self, outcome_code: str, message: str, extracted_data: dict = None):
        super().__init__(
            message=message,
            code=outcome_code,
            details={"extracted_data": extracted_data or {}}
        )

class HandoffRequiredException(OrchestrationException):
    """Execution blocked and escalated to Human-in-the-Loop."""
    def __init__(self, run_id: str, reason: str, step_number: int):
        super().__init__(
            message=f"Run '{run_id}' blocked at step {step_number}: {reason}",
            code="HITL_REQUIRED",
            details={"run_id": run_id, "reason": reason, "step_number": step_number}
        )

class LLMProviderException(OrchestrationException):
    def __init__(self, message: str, provider: str = "mistral", code: str = "LLM_PROVIDER_ERROR", retryable: bool = False):
        super().__init__(
            message=f"LLM Provider Error ({provider}): {message}",
            code=code,
            details={"provider": provider, "retryable": retryable}
        )


_RECOVERABLE_CODES = {
    "MISTRAL_RATE_LIMITED",
    "MISTRAL_PROVIDER_UNAVAILABLE",
    "MISTRAL_TIMEOUT",
    "MISTRAL_TRANSPORT_ERROR",
    "DISCOVERY_TIMEOUT",
    "REPLAY_EXECUTION_ERROR",
    "BROWSER_INITIALIZATION_FAILED",
    "NAVIGATION_FAILED",
    "LOCATOR_NOT_FOUND",
}


def execution_outcome_category(status: str, error_code: str | None = None) -> str:
    """Map storage statuses and diagnostic codes to stable API outcome categories."""
    status = (status or "").upper()
    code = (error_code or "").split(":", 1)[0].upper()
    if status == "SUCCESS":
        return "SUCCESS"
    if status == "BUSINESS_OUTCOME":
        return "BUSINESS_OUTCOME"
    if status in {"BLOCKED", "AWAITING_HUMAN"} or code == "HUMAN_INTERVENTION_REQUIRED":
        return "BLOCKED"
    if status == "RUNNING":
        return "RUNNING"
    return "RECOVERABLE_ERROR" if code in _RECOVERABLE_CODES else "HARD_FAILURE"
