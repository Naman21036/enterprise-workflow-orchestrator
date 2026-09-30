"""Central fail-closed policy for the supported APEX simulator operation set."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional, Tuple
from urllib.parse import unquote, urlparse

from backend.app.core.config import settings
from backend.app.observability import record_safety_rejection, span


class PolicyAction(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class RiskLevel:
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    # Backward-compatible names used by older artifacts/API payloads.
    SAFE = LOW
    REVERSIBLE = MEDIUM
    RISKY = HIGH
    IRREVERSIBLE = CRITICAL


@dataclass(frozen=True)
class PolicyDecision:
    decision: PolicyAction
    policy_rule_id: str
    risk: str
    reason_code: str
    action_id: str | None
    policy_version: str
    timestamp: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["decision"] = self.decision.value
        return value


class SafetyPolicy:
    POLICY_VERSION = "2.0.0"
    # These are application-specific semantic permissions. Unknown elements/actions
    # are denied; strings from an LLM do not grant permission.
    INPUT_FIELDS = {"#member-id-input"}
    READ_ONLY_CLICKS = {"#search-btn", "#back-btn"}
    READ_ONLY_EXTRACTS = {
        "#member-name-val", "#member-id-val", "#savings-balance-val", ".status-badge",
    }
    APPROVAL_ONLY_CLICKS = {"#confirm-dialog-btn"}
    SAFE_KEYS = {"Escape", "ArrowDown", "ArrowUp"}

    def __init__(self, allowed_domains: Optional[list] = None, allow_risky_actions: bool = False, allowed_routes: Optional[list] = None):
        self.allowed_domains = set(allowed_domains or settings.allowed_domains_list)
        self.allow_risky_actions = False  # legacy option intentionally cannot override action rules
        self.allowed_routes = tuple(allowed_routes or ["/", "/member/"])

    def validate_url(self, url: str, *, method: str = "GET") -> bool:
        if not isinstance(url, str) or not url or len(url) > 2048 or method.upper() != "GET":
            return False
        try:
            parsed = urlparse(url)
            hostname = (parsed.hostname or "").lower()
            port = parsed.port
        except ValueError:
            return False
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return False
        if hostname not in self.allowed_domains or port not in (None, 80, 443, 3001):
            return False
        path = unquote(parsed.path or "/")
        if "%" in path or "\\" in path or "//" in path or any(x in {".", ".."} for x in path.split("/")):
            return False
        if parsed.query or parsed.fragment:
            return False
        if path == "/":
            return True
        # This simulator exposes only member result routes. A broad `/member/*`
        # prefix would also admit admin and ambiguous encoded routes.
        return bool(re.fullmatch(r"/member/\d{4,5}/?", path))

    def classify_action_risk(self, action_type: str, selector: str = "", text_value: str = "") -> str:
        action = str(action_type).lower()
        target = str(selector).strip()
        value = str(text_value)
        if action in {"navigate"}:
            return RiskLevel.LOW
        if action in {"extract", "assert", "complete"} and target in self.READ_ONLY_EXTRACTS | {"body", "#member-id-input"}:
            return RiskLevel.LOW
        if action in {"fill", "select", "type"}:
            if target in self.INPUT_FIELDS and re.fullmatch(r"\d{4,5}", value):
                return RiskLevel.MEDIUM
            return RiskLevel.CRITICAL
        if action == "click":
            if target in self.READ_ONLY_CLICKS:
                return RiskLevel.LOW
            if target in self.APPROVAL_ONLY_CLICKS:
                return RiskLevel.HIGH
            return RiskLevel.CRITICAL
        if action == "press_key":
            if value in self.SAFE_KEYS:
                return RiskLevel.MEDIUM
            if value == "Enter":
                return RiskLevel.HIGH
            return RiskLevel.CRITICAL
        return RiskLevel.CRITICAL

    def evaluate_action(
        self,
        action_type: str,
        target_url: str,
        selector: str = "",
        value: str = "",
        *,
        action_id: str | None = None,
        method: str = "GET",
        approval_bound: bool = False,
    ) -> PolicyDecision:
        action = str(action_type).lower()
        now = datetime.now(timezone.utc).isoformat()
        if not self.validate_url(target_url, method=method):
            return PolicyDecision(PolicyAction.DENY, "route.allowlist", RiskLevel.CRITICAL, "ROUTE_DENIED", action_id, self.POLICY_VERSION, now, "Target URL or HTTP method is outside the allowlist.")
        risk = self.classify_action_risk(action, selector, value)
        if risk == RiskLevel.LOW:
            return PolicyDecision(PolicyAction.ALLOW, "action.simulator.read_only", risk, "READ_ONLY_ALLOWED", action_id, self.POLICY_VERSION, now, "Read-only simulator operation is allowed.")
        if risk == RiskLevel.MEDIUM:
            return PolicyDecision(PolicyAction.ALLOW, "action.simulator.reversible_input", risk, "REVERSIBLE_INPUT_ALLOWED", action_id, self.POLICY_VERSION, now, "Reversible member search input is allowed.")
        if risk == RiskLevel.HIGH:
            if approval_bound and action == "click" and selector in self.APPROVAL_ONLY_CLICKS:
                return PolicyDecision(PolicyAction.ALLOW, "approval.simulator.confirmation", risk, "BOUND_OPERATOR_APPROVAL", action_id, self.POLICY_VERSION, now, "The exact active simulator confirmation is bound to this operator action.")
            return PolicyDecision(PolicyAction.REQUIRE_APPROVAL, "approval.required", risk, "EXPLICIT_APPROVAL_REQUIRED", action_id, self.POLICY_VERSION, now, "This operation requires a run- and action-bound approval.")
        return PolicyDecision(PolicyAction.DENY, "action.default_deny", risk, "ACTION_NOT_PERMITTED", action_id, self.POLICY_VERSION, now, "Action semantics are unknown or critical and are denied by default.")

    def validate_action(self, action_type: str, target_url: str, selector: str = "", value: str = "", *, action_id: str | None = None, approval_bound: bool = False) -> Tuple[bool, str]:
        with span("apex.safety.evaluate", {"action.type": str(action_type).lower(), "safety.policy_version": self.POLICY_VERSION}):
            result = self.evaluate_action(action_type, target_url, selector, value, action_id=action_id, approval_bound=approval_bound)
            if result.decision != PolicyAction.ALLOW:
                record_safety_rejection(str(action_type).lower())
            return result.decision == PolicyAction.ALLOW, result.reason

    def validate_artifact(self, artifact) -> list[PolicyDecision]:
        """Re-evaluate every action in a capability against current policy."""
        decisions: list[PolicyDecision] = []
        for step in artifact.steps:
            target = step.target.primary_selector
            value = step.value_expression or ""
            route = value if step.action_type == "navigate" else settings.TARGET_APP_URL
            decision = self.evaluate_action(step.action_type, route, target, value, action_id=f"{artifact.capability_id}:{artifact.version}:{step.step_number}")
            decisions.append(decision)
        return decisions

    def sanitize_sensitive_data(self, data: Any) -> Any:
        if isinstance(data, str):
            data = re.sub(r'(?i)(password|secret|key|token|ssn)=["\']?[^"\'\s]+["\']?', r'\1=[REDACTED]', data)
            data = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', 'XXX-XX-XXXX', data)
            data = re.sub(r'\$\s?[\d,]+(?:\.\d{2})?', '[FINANCIAL DATA REDACTED]', data)
            data = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[EMAIL REDACTED]', data)
            data = re.sub(r'\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b', '[PHONE REDACTED]', data)
            data = re.sub(r'\b\d{8,}\b', '[IDENTIFIER REDACTED]', data)
            data = re.sub(r'(?i)\b(member\s*(?:id)?\s*[:#]?\s*)\d{3,10}\b', r'\1[REDACTED]', data)
            return data
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                name = str(key).lower()
                if any(word in name for word in ("password", "secret", "token", "api_key", "ssn", "value", "member_id", "member_name", "account", "balance", "card", "phone", "email", "address")):
                    result[key] = "[REDACTED]"
                elif name in {"page_text_summary", "text", "inner_text"}:
                    result[key] = "[UI text redacted]"
                else:
                    result[key] = self.sanitize_sensitive_data(value)
            return result
        if isinstance(data, list):
            return [self.sanitize_sensitive_data(item) for item in data]
        return data


default_safety_policy = SafetyPolicy(allowed_domains=settings.allowed_domains_list, allow_risky_actions=False)
