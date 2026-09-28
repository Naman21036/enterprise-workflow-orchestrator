import sys
import os
import asyncio
import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.db.database import init_db, AsyncSessionLocal
from backend.app.orchestration.router import WorkflowRouter
from backend.app.artifacts.storage import ArtifactStorage

async def main():
    print("=" * 70)
    print("APEX AUTOMATION COMPUTER USE SYSTEM - END-TO-END DEMO")
    print("=" * 70)

    await init_db()
    async with AsyncSessionLocal() as db:
        router = WorkflowRouter(db)
        storage = ArtifactStorage(db)

        # Ensure seed capability artifact exists
        artifact_path = os.path.join("evidence", "artifacts", "member_savings_lookup_v1.0.0.json")
        if os.path.exists(artifact_path):
            import json
            from backend.app.artifacts.schema import CapabilityArtifact
            with open(artifact_path, "r") as f:
                art = CapabilityArtifact.model_validate(json.load(f))
                await storage.save_artifact(art)

        print("\n1. Running Goal (Capability Lookup -> Deterministic Replay for Member 1002)...")
        res1 = await router.execute_goal(
            goal="Find member 1002 and retrieve their savings balance.",
            input_parameters={"member_id": "1002"}
        )
        print("   Result:", res1["status"])
        print("   Outputs:", res1.get("outputs"))

        print("\n2. Running Goal with Non-Existent Member 99999 (Business Outcome)...")
        res2 = await router.execute_goal(
            goal="Find member 99999 and retrieve their savings balance.",
            input_parameters={"member_id": "99999"}
        )
        print("   Result:", res2["status"])
        print("   Business Outcome Details:", res2.get("outputs"))

        print("\n3. Testing Dialog Simulator (HITL Escalation)...")
        try:
            async with httpx.AsyncClient() as client:
                await client.post("http://localhost:3001/api/simulators", json={"dialog": True})
        except Exception:
            pass

        res3 = await router.execute_goal(
            goal="Find member 1002 and retrieve their savings balance.",
            input_parameters={"member_id": "1002"}
        )
        print("   Result:", res3["status"])
        print("   Reason:", res3.get("error"))

        try:
            async with httpx.AsyncClient() as client:
                await client.post("http://localhost:3001/api/simulators", json={"dialog": False})
        except Exception:
            pass

        print("\n[OK] Demo Execution Completed Successfully!")

if __name__ == "__main__":
    asyncio.run(main())
