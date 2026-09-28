import re
from typing import Dict, Any, Tuple, Optional
from urllib.parse import urlparse
from backend.app.core.config import settings
from backend.app.core.errors import SafetyViolationError

class RiskLevel:
    SAFE = "SAFE"
    REVERSIBLE = "REVERSIBLE"
    RISKY = "RISKY"
    IRREVERSIBLE = "IRREVERSIBLE"

class SafetyPolicy:
    def __init__(self, allowed_domains: Optional[list] = None, allow_risky_actions: bool = False, allowed_routes: Optional[list] = None):
        self.allowed_domains = allowed_domains or settings.allowed_domains_list
        self.allow_risky_actions = allow_risky_actions
        self.allowed_routes = allowed_routes or ["/", "/member/"]

    def validate_url(self, url: str) -> bool:
        if not url:
            return False
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return False
        # Match allowed domain or localhost variants
        for allowed in self.allowed_domains:
            if hostname == allowed or hostname.startswith("127.0.0.") or hostname == "localhost":
                path = parsed.path or "/"
                if path == "/" or any(route != "/" and route.endswith("/") and path.startswith(route) for route in self.allowed_routes):
                    return True
        return False

    def classify_action_risk(self, action_type: str, selector: str = "", text_value: str = "") -> str:
        action_type = action_type.lower()
        sel = selector.lower()
        val = text_value.lower()

        # Check for dangerous/irreversible operations
        if any(term in sel or term in val for term in ["delete", "remove", "close", "destroy", "wire", "transfer"]):
            if "search" not in sel:
                return RiskLevel.IRREVERSIBLE

        if action_type in ["click", "fill", "select"]:
            if any(term in sel for term in ["submit", "confirm", "save", "close", "delete", "transfer", "wire", "create-account", "update-account"]):
                return RiskLevel.RISKY

        # Searching, navigating, reading, extracting are SAFE
        return RiskLevel.SAFE

    def validate_action(self, action_type: str, target_url: str, selector: str = "", value: str = "") -> Tuple[bool, str]:
        if target_url and not self.validate_url(target_url):
            return False, f"Target URL '{target_url}' is not in the allowed domains allowlist: {self.allowed_domains}"

        risk = self.classify_action_risk(action_type, selector, value)
        if risk in [RiskLevel.RISKY, RiskLevel.IRREVERSIBLE] and not self.allow_risky_actions:
            return False, f"Action '{action_type}' on '{selector}' classified as {risk}, which is blocked by safety policy."

        return True, "Allowed"

    def sanitize_sensitive_data(self, data: Any) -> Any:
        """Redact secrets, passwords, tokens, SSNs from logs/artifacts."""
        if isinstance(data, str):
            # Redact password patterns
            data = re.sub(r'(?i)(password|secret|key|token|ssn)=["\']?[^"\'\s]+["\']?', r'\1=[REDACTED]', data)
            # Redact SSN pattern
            data = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', 'XXX-XX-XXXX', data)
            data = re.sub(r'\$\s?[\d,]+(?:\.\d{2})?', '[FINANCIAL DATA REDACTED]', data)
            data = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[EMAIL REDACTED]', data)
            data = re.sub(r'\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b', '[PHONE REDACTED]', data)
            data = re.sub(r'\b\d{8,}\b', '[IDENTIFIER REDACTED]', data)
            data = re.sub(r'(?i)\b(member\s*(?:id)?\s*[:#]?\s*)\d{3,10}\b', r'\1[REDACTED]', data)
            return data
        elif isinstance(data, dict):
            new_dict = {}
            for k, v in data.items():
                key = k.lower()
                if any(sec in key for sec in ["password", "secret", "token", "api_key", "ssn", "value", "member_id", "member_name", "account", "balance", "card", "phone", "email", "address"]):
                    new_dict[k] = "[REDACTED]"
                elif key in {"page_text_summary", "text", "inner_text"}:
                    new_dict[k] = "[UI text redacted]"
                else:
                    new_dict[k] = self.sanitize_sensitive_data(v)
            return new_dict
        elif isinstance(data, list):
            return [self.sanitize_sensitive_data(item) for item in data]
        return data

default_safety_policy = SafetyPolicy()
