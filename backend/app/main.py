import os
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi import Depends, HTTPException
from pathlib import Path

from backend.app.core.config import settings
from backend.app.core.logging import setup_logging, logger
from backend.app.db.database import init_db, AsyncSessionLocal, engine
from backend.app.escalation.manager import session_manager
from backend.app.api.v1.endpoints import health, workflows, runs, capabilities, handoff, safety, recordings, auth
from backend.app.observability import configure_observability
from backend.app.security.auth import Principal, get_current_principal
from backend.app.api.v1.endpoints.auth import ensure_bootstrap_operator
from backend.app.db.models import RunModel
from sqlalchemy import select

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    telemetry_status = configure_observability(app, engine)
    logger.info("Observability initialized", **telemetry_status)
    logger.info("Initializing APEX Automation Backend Database...")
    await init_db()
    async with AsyncSessionLocal() as db:
        await ensure_bootstrap_operator(db)
        await session_manager.mark_persisted_sessions_lost(db)
    yield
    logger.info("Shutting down APEX Automation Backend...")

app = FastAPI(
    title="APEX Automation Engine API",
    description="Computer Use Automation System with Mistral AI Discovery & LLM-Free Deterministic Replay",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Evidence is served only to an authenticated principal in the owning tenant.
os.makedirs(settings.EVIDENCE_DIR, exist_ok=True)

app.include_router(health.router, prefix="/api/v1", tags=["Health"])
app.include_router(workflows.router, prefix="/api/v1", tags=["Workflows"])
app.include_router(runs.router, prefix="/api/v1", tags=["Runs"])
app.include_router(capabilities.router, prefix="/api/v1", tags=["Capabilities"])
app.include_router(handoff.router, prefix="/api/v1", tags=["Handoff"])
app.include_router(safety.router, prefix="/api/v1", tags=["Safety"])
app.include_router(recordings.router, prefix="/api/v1", tags=["Recordings"])
app.include_router(auth.router, prefix="/api/v1", tags=["Authentication and operators"])


@app.get("/evidence/{file_path:path}")
async def get_evidence_file(file_path: str, principal: Principal = Depends(get_current_principal)):
    root = Path(settings.EVIDENCE_DIR).resolve()
    candidate = (root / file_path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Evidence file not found")
    parts = Path(file_path).parts
    run_id = None
    if len(parts) >= 2 and parts[0] in {"discovery", "replay"}:
        run_id = parts[1]
    elif len(parts) >= 1 and parts[0] == "escalations":
        match = re.fullmatch(r"(wf_[a-f0-9]+)_(?:operator|after_action)\.png", Path(parts[-1]).name)
        run_id = match.group(1) if match else None
    if not run_id:
        raise HTTPException(status_code=404, detail="Evidence file is not associated with an authorized run")
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(RunModel).where(RunModel.id == run_id, RunModel.tenant_id == principal.tenant_id))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Evidence file not found")
    return FileResponse(candidate)

@app.get("/")
async def root():
    return {
        "name": "APEX Automation Orchestration Console Engine",
        "docs": "/docs",
        "health": "/api/v1/health"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host=settings.BACKEND_HOST, port=settings.BACKEND_PORT, reload=True)
