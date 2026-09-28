import json
from pathlib import Path

import pytest

from backend.app.artifacts.schema import CapabilityArtifact
from backend.app.core.config import settings
from backend.app.replay.engine import DeterministicReplayEngine


class PermissionDeniedSurface:
    last_failed_operation = "playwright_driver_start"

    async def connect(self, _target_url):
        error = PermissionError("synthetic Windows pipe permission failure")
        error.winerror = 5
        raise error


@pytest.mark.asyncio
async def test_windows_driver_permission_failure_is_classified_before_any_step():
    artifact_path = Path(settings.EVIDENCE_DIR) / "artifacts" / "member_savings_lookup_v1.0.0.json"
    artifact = CapabilityArtifact.model_validate(json.loads(artifact_path.read_text(encoding="utf-8")))
    status, steps, outputs, error = await DeterministicReplayEngine(PermissionDeniedSurface()).execute_replay(
        artifact=artifact,
        inputs={"member_id": "1002"},
        run_id="test-browser-start-denied",
    )

    assert status == "FAILED"
    assert steps == []
    assert outputs == {}
    assert error.startswith("PLAYWRIGHT_DRIVER_PERMISSION_DENIED:")
    assert "no browser actions ran" in error
