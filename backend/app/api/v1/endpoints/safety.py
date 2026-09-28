from fastapi import APIRouter
from backend.app.safety.policy import default_safety_policy
from backend.app.core.config import settings

router = APIRouter()

@router.get("/safety/policy")
async def get_safety_policy():
    return {
        "allowed_domains": default_safety_policy.allowed_domains,
        "allowed_routes": default_safety_policy.allowed_routes,
        "allow_risky_actions": default_safety_policy.allow_risky_actions,
        "risk_levels": {
            "SAFE": "Read-only navigation, element inspection, data extraction",
            "REVERSIBLE": "Form input, tab switching, filter selection",
            "RISKY": "Account updates, form submissions with financial impact",
            "IRREVERSIBLE": "Fund transfers, account deletions, external wires"
        },
        "redaction_rules": [
            "API keys and secrets",
            "Passwords and tokens",
            "Social Security Numbers (SSNs)",
            "Full PII data from persistent logs"
        ]
    }
