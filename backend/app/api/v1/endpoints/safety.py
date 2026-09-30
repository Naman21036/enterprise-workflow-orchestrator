from fastapi import APIRouter, Depends
from backend.app.safety.policy import default_safety_policy
from backend.app.security.auth import Principal, authorize, get_current_principal

router = APIRouter()

@router.get("/safety/policy")
async def get_safety_policy(principal: Principal = Depends(get_current_principal)):
    authorize(principal, "safety:read")
    return {
        "allowed_domains": default_safety_policy.allowed_domains,
        "allowed_routes": default_safety_policy.allowed_routes,
        "allow_risky_actions": False,
        "policy_version": default_safety_policy.POLICY_VERSION,
        "risk_levels": {
            "LOW": "Explicit simulator navigation, search and extraction operations",
            "MEDIUM": "Explicit member search input and reversible navigation keys",
            "HIGH": "Run/action-bound approval required",
            "CRITICAL": "Unknown or consequential operations are denied",
        },
        "redaction_rules": [
            "API keys and secrets",
            "Passwords and tokens",
            "Social Security Numbers (SSNs)",
            "Full PII data from persistent logs"
        ]
    }
