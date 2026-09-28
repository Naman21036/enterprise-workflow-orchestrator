import sys
import os
import asyncio

# Ensure parent path in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.surfaces.playwright import PlaywrightWebSurface
from backend.app.discovery.agent import MistralDiscoveryAgent
from backend.app.core.config import settings

async def main():
    print("=" * 70)
    print("MISTRAL AI DISCOVERY RUNNER")
    print("Target Application:", settings.TARGET_APP_URL)
    print("Mistral Model:", settings.MISTRAL_MODEL)
    print("=" * 70)

    goal = "Find member 1002 and retrieve their savings balance."
    run_id = f"wf_disc_{os.urandom(3).hex()}"

    surface = PlaywrightWebSurface(headless=True)
    agent = MistralDiscoveryAgent(surface, max_steps=10)

    try:
        success, trace, outputs = await agent.run_discovery(
            goal=goal,
            target_url=settings.TARGET_APP_URL,
            run_id=run_id
        )
        print(f"\n[+] Discovery Finished!")
        print(f"    Success: {success}")
        print(f"    Total Steps Executed: {len(trace)}")
        print(f"    Extracted Outputs: {outputs}")
        print(f"    Trace evidence saved in: ./evidence/discovery/{run_id}/")
    finally:
        await surface.close()

if __name__ == "__main__":
    asyncio.run(main())
