from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.database import get_db
from backend.app.artifacts.storage import ArtifactStorage
from backend.app.core.errors import ArtifactValidationError
from backend.app.security.auth import Principal, authorize, get_current_principal

router = APIRouter()

@router.get("/capabilities")
async def list_capabilities(db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)):
    authorize(principal, "capabilities:read")
    storage = ArtifactStorage(db)
    try:
        return await storage.list_capabilities()
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message}) from exc

@router.get("/capabilities/{capability_id}")
async def get_capability_artifact(capability_id: str, version: Optional[str] = None, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)):
    authorize(principal, "capabilities:read")
    storage = ArtifactStorage(db)
    try:
        artifact = await storage.get_artifact(capability_id, version)
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message}) from exc
    if not artifact:
        raise HTTPException(status_code=404, detail=f"Capability artifact '{capability_id}' not found")
    return artifact.model_dump()
