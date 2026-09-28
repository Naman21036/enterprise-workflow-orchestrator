import sys
import os
import json
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.surfaces.playwright import PlaywrightWebSurface
from backend.app.artifacts.schema import CapabilityArtifact
from backend.app.replay.engine import DeterministicReplayEngine
from backend.app.llm.factory import LLMFactory
from backend.app.core.config import settings

async def main():
    print("=" * 70)
    print("DETERMINISTIC REPLAY RUNNER (STRICTLY LLM-FREE)")
    print("=" * 70)

    # Monkeypatch LLMFactory to throw an Exception if LLM is invoked during replay!
    class ForbiddenLLMClient:
        async def generate_structured(self, *args, **kwargs):
            raise RuntimeError("CRITICAL FAILURE: LLM was invoked during Deterministic Replay!")

    LLMFactory.set_client(ForbiddenLLMClient())
    print("[OK] LLM Factory patched to strictly reject any LLM calls.")

    artifact_path = os.path.join(settings.EVIDENCE_DIR, "artifacts", "member_savings_lookup_v1.0.0.json")
    if not os.path.exists(artifact_path):
        print(f"[!] Error: Artifact file not found at {artifact_path}")
        return

    with open(artifact_path, "r", encoding="utf-8") as f:
        artifact_data = json.load(f)
    artifact = CapabilityArtifact.model_validate(artifact_data)

    # Test 1: Successful Replay for Member 1002
    print("\n--- Test 1: Deterministic Replay for Member 1002 ---")
    run_id_1002 = f"wf_replay_1002_{os.urandom(2).hex()}"
    surface = PlaywrightWebSurface(headless=True)
    engine = DeterministicReplayEngine(surface)

    try:
        status, steps, outputs, err = await engine.execute_replay(
            artifact=artifact,
            inputs={"member_id": "1002"},
            run_id=run_id_1002
        )
        print(f"Status: {status}")
        print(f"Outputs Extracted: {outputs}")
        print(f"Steps Executed: {len(steps)}")
        print(f"Error: {err}")
    finally:
        await surface.close()

    # Test 2: Business Outcome for Non-existent Member 99999
    print("\n--- Test 2: Replay for Non-Existent Member 99999 (Business Outcome) ---")
    run_id_99999 = f"wf_replay_99999_{os.urandom(2).hex()}"
    surface2 = PlaywrightWebSurface(headless=True)
    engine2 = DeterministicReplayEngine(surface2)

    try:
        status, steps, outputs, err = await engine2.execute_replay(
            artifact=artifact,
            inputs={"member_id": "99999"},
            run_id=run_id_99999
        )
        print(f"Status: {status} (Expected: BUSINESS_OUTCOME)")
        print(f"Outcome Payload: {outputs}")
    finally:
        await surface2.close()

if __name__ == "__main__":
    asyncio.run(main())
