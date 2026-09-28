import pytest
from backend.app.safety.policy import SafetyPolicy, RiskLevel

def test_safety_policy_domain_validation():
    policy = SafetyPolicy(allowed_domains=["localhost", "127.0.0.1"])
    assert policy.validate_url("http://localhost:3001/member/1002") is True
    assert policy.validate_url("http://localhost:3001/member/1002") is True
    assert policy.validate_url("http://127.0.0.1:8000/api") is False
    assert policy.validate_url("http://localhost:3001/admin/export") is False
    assert policy.validate_url("https://malicious-external-bank.com/steal") is False

def test_safety_policy_risk_classification():
    policy = SafetyPolicy()
    assert policy.classify_action_risk("click", "#search-btn") == RiskLevel.SAFE
    assert policy.classify_action_risk("fill", "#member-id-input", "1002") == RiskLevel.SAFE
    assert policy.classify_action_risk("click", "#delete-account-btn") == RiskLevel.IRREVERSIBLE

def test_sensitive_data_redaction():
    policy = SafetyPolicy()
    raw_log = "User logged in with password='Secret1234' and ssn='123-45-6789'"
    sanitized = policy.sanitize_sensitive_data(raw_log)
    assert "Secret1234" not in sanitized
    assert "123-45-6789" not in sanitized
    assert "[REDACTED]" in sanitized or "XXX-XX-XXXX" in sanitized
