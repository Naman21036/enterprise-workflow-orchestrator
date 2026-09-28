import os
import json
import pytest
import asyncio
from backend.app.surfaces.playwright import PlaywrightWebSurface
from backend.app.artifacts.schema import CapabilityArtifact
from backend.app.replay.engine import DeterministicReplayEngine
from backend.app.llm.factory import LLMFactory
from backend.app.core.config import settings

class ForbiddenLLMClient:
    async def generate_structured(self, *args, **kwargs):
        raise RuntimeError("TEST FAILURE: LLM Factory was invoked during Deterministic Replay!")

@pytest.mark.asyncio
async def test_critical_deterministic_replay_is_llm_free():
    """
    MANDATORY CRITICAL TEST:
    Monkeypatches LLMFactory to throw an exception if called.
    Verifies that Deterministic Replay completes with 100% success and NO LLM INVOCATIONS.
    """
    # 1. Patch LLMFactory
    LLMFactory.set_client(ForbiddenLLMClient())

    # 2. Load capability artifact
    artifact_path = os.path.join(settings.EVIDENCE_DIR, "artifacts", "member_savings_lookup_v1.0.0.json")
    assert os.path.exists(artifact_path), f"Artifact file missing at {artifact_path}"

    with open(artifact_path, "r", encoding="utf-8") as f:
        artifact = CapabilityArtifact.model_validate(json.load(f))

    # 3. Execute Deterministic Replay for Member 1002
    surface = PlaywrightWebSurface(headless=True)
    engine = DeterministicReplayEngine(surface)
    run_id = f"test_no_llm_{os.urandom(3).hex()}"

    try:
        status, steps, outputs, err = await engine.execute_replay(
            artifact=artifact,
            inputs={"member_id": "1002"},
            run_id=run_id
        )

        assert status == "SUCCESS", f"Replay failed with status {status}, error: {err}"
        assert outputs.get("savings_balance") == "$7,250.00"
        assert outputs.get("member_name") == "John Doe"
        assert len(steps) >= 3
        print("\n[OK] CRITICAL REPLAY TEST PASSED: 100% LLM-FREE EXECUTION VERIFIED!")
    finally:
        await surface.close()
        # Reset factory
        LLMFactory.set_client(None)
