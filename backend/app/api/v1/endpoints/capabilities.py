from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.database import get_db
from backend.app.artifacts.storage import ArtifactStorage

router = APIRouter()

@router.get("/capabilities")
async def list_capabilities(db: AsyncSession = Depends(get_db)):
    storage = ArtifactStorage(db)
    return await storage.list_capabilities()

@router.get("/capabilities/{capability_id}")
async def get_capability_artifact(capability_id: str, version: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    storage = ArtifactStorage(db)
    artifact = await storage.get_artifact(capability_id, version)
    if not artifact:
        raise HTTPException(status_code=404, detail=f"Capability artifact '{capability_id}' not found")
    return artifact.model_dump()
