import pytest
from pydantic import ValidationError

from backend.app.artifacts.schema import CapabilityArtifact


def artifact_payload():
    return {
        "capability_id": "member_lookup",
        "name": "Member lookup",
        "description": "Read a member profile",
        "steps": [
            {"step_number": 1, "action_type": "navigate", "target": {"primary_selector": "body"}}
        ],
        "success_condition": {"rule_type": "url_contains", "target": "/member/"},
    }


def test_publication_validator_accepts_contiguous_typed_workflow():
    artifact = CapabilityArtifact.validate_for_publication(artifact_payload())
    assert artifact.steps[0].action_type == "navigate"


def test_artifact_rejects_unknown_actions_and_fields():
    payload = artifact_payload()
    payload["steps"][0]["action_type"] = "run_code"
    with pytest.raises(ValidationError):
        CapabilityArtifact.validate_for_publication(payload)


def test_publication_validator_rejects_non_contiguous_steps():
    payload = artifact_payload()
    payload["steps"][0]["step_number"] = 2
    with pytest.raises(ValueError, match="contiguous"):
        CapabilityArtifact.validate_for_publication(payload)
