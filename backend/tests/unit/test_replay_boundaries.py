import json

import pytest

from backend.app.artifacts.schema import CapabilityArtifact
from backend.app.core.config import settings
from backend.app.replay.engine import DeterministicReplayEngine


class FakeSurface:
    def __init__(self):
        self.url = ""
        self.text = "APEX Federal"
        self.title = "APEX Federal"
        self.visible = {"body", "#member-id-input", "#search-btn"}
        self.values = {"#savings-balance-val": "$1,234.56", "#member-name-val": "Synthetic Member", "#member-id-val": "1002"}
        self.fill_values = []
        self.connected = False

    async def connect(self, url):
        self.connected = True
        self.url = url
        return True

    async def navigate(self, url):
        self.url = url
        return True

    async def observe(self):
        return {"url": self.url, "title": self.title, "page_text_summary": self.text}

    async def locate(self, selectors):
        return next((selector for selector in selectors if selector in self.visible), None)

    async def click(self, selector):
        if selector not in self.visible:
            return False
        self.url = "http://localhost:3001/member/" + (self.fill_values[-1] if self.fill_values else "1002")
        self.text = "Member details and savings account"
        self.visible.update(self.values)
        self.values["#member-id-val"] = self.fill_values[-1] if self.fill_values else "1002"
        return True

    async def fill(self, selector, value):
        if selector not in self.visible:
            return False
        self.fill_values.append(value)
        return True

    async def select(self, selector, option):
        return selector in self.visible

    async def extract(self, selector, attribute=None):
        return self.values.get(selector)

    async def capture_screenshot(self, path):
        return path

    async def current_url(self):
        return self.url


def artifact_payload():
    return {
        "schema_version": "1.0.0",
        "capability_id": "member_savings_lookup",
        "version": "1.0.0",
        "name": "Savings lookup",
        "description": "Read a synthetic member's savings balance",
        "target_application": "APEX Federal",
        "surface_type": "web",
        "parameters": [{"name": "member_id", "param_type": "string", "description": "Member ID", "required": True}],
        "outputs": [{"name": "savings_balance", "shape": "currency", "selector": "#savings-balance-val"}],
        "steps": [
            {"step_number": 1, "action_type": "navigate", "target": {"primary_selector": "body"}, "value_expression": "http://localhost:3001"},
            {"step_number": 2, "action_type": "fill", "target": {"primary_selector": "#member-id-input"}, "value_expression": "${inputs.member_id}", "parameter_ref": "member_id"},
            {"step_number": 3, "action_type": "click", "target": {"primary_selector": "#search-btn"}, "checkpoints": [{"rule_type": "url_contains", "target": "/member/"}]},
            {"step_number": 4, "action_type": "extract", "target": {"primary_selector": "#savings-balance-val"}, "parameter_ref": "savings_balance"},
        ],
        "success_condition": {"rule_type": "element_visible", "target": "#savings-balance-val"},
    }


@pytest.mark.asyncio
async def test_replay_substitutes_different_input_without_llm_and_records_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "EVIDENCE_DIR", str(tmp_path))
    surface = FakeSurface()
    artifact = CapabilityArtifact.validate_for_publication(artifact_payload())
    status, steps, outputs, error = await DeterministicReplayEngine(surface).execute_replay(artifact, {"member_id": "1234"}, "run_reuse")
    assert status == "SUCCESS", error
    assert surface.fill_values == ["1234"]
    assert outputs["savings_balance"] == "$1,234.56"
    assert [step["step_number"] for step in steps] == [1, 2, 3, 4]
    evidence = json.loads((tmp_path / "replay" / "run_reuse" / "replay_execution.json").read_text())
    assert evidence["inputs"] == {"member_id": "[REDACTED]"}
    assert evidence["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_missing_input_is_rejected_before_browser_connect(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EVIDENCE_DIR", str(tmp_path))
    surface = FakeSurface()
    artifact = CapabilityArtifact.validate_for_publication(artifact_payload())
    status, steps, _, error = await DeterministicReplayEngine(surface).execute_replay(artifact, {}, "missing_input")
    assert status == "FAILED"
    assert "missing=['member_id']" in error
    assert not surface.connected
    assert not (tmp_path / "replay" / "missing_input").exists()


@pytest.mark.asyncio
async def test_disallowed_artifact_url_is_rejected_before_browser_connect(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EVIDENCE_DIR", str(tmp_path))
    payload = artifact_payload()
    payload["steps"][0]["value_expression"] = "https://attacker.example/member/1002"
    artifact = CapabilityArtifact.validate_for_publication(payload)
    surface = FakeSurface()
    status, steps, _, error = await DeterministicReplayEngine(surface).execute_replay(artifact, {"member_id": "1002"}, "unsafe_url")
    assert status == "FAILED"
    assert error.startswith("SAFETY_POLICY_BLOCKED")
    assert not surface.connected
    assert not steps


@pytest.mark.asyncio
async def test_replay_rejects_failed_checkpoint_even_when_click_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "EVIDENCE_DIR", str(tmp_path))
    payload = artifact_payload()
    payload["steps"][2]["checkpoints"] = [{"rule_type": "title_contains", "target": "Different application"}]
    artifact = CapabilityArtifact.validate_for_publication(payload)
    status, steps, _, error = await DeterministicReplayEngine(FakeSurface()).execute_replay(artifact, {"member_id": "1002"}, "bad_checkpoint")
    assert status == "FAILED"
    assert steps[-1]["status"] == "FAILED"
    assert "Checkpoint failed" in error
