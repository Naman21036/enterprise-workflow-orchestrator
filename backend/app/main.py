import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.core.config import settings
from backend.app.core.logging import setup_logging, logger
from backend.app.db.database import init_db, AsyncSessionLocal
from backend.app.escalation.manager import session_manager
from backend.app.api.v1.endpoints import health, workflows, runs, capabilities, handoff, safety, recordings

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("Initializing APEX Automation Backend Database...")
    await init_db()
    async with AsyncSessionLocal() as db:
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

# Mount evidence folder for static screenshot inspection
os.makedirs(settings.EVIDENCE_DIR, exist_ok=True)
app.mount("/evidence", StaticFiles(directory=settings.EVIDENCE_DIR), name="evidence")

app.include_router(health.router, prefix="/api/v1", tags=["Health"])
app.include_router(workflows.router, prefix="/api/v1", tags=["Workflows"])
app.include_router(runs.router, prefix="/api/v1", tags=["Runs"])
app.include_router(capabilities.router, prefix="/api/v1", tags=["Capabilities"])
app.include_router(handoff.router, prefix="/api/v1", tags=["Handoff"])
app.include_router(safety.router, prefix="/api/v1", tags=["Safety"])
app.include_router(recordings.router, prefix="/api/v1", tags=["Recordings"])

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
