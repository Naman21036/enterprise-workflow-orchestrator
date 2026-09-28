import os
import json
import re
import tempfile
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.models import CapabilityModel, CapabilityVersionModel
from backend.app.artifacts.schema import CapabilityArtifact
from backend.app.core.config import settings
from backend.app.core.logging import logger

class ArtifactStorage:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def save_artifact(self, artifact: CapabilityArtifact, commit: bool = True) -> CapabilityArtifact:
        artifact = CapabilityArtifact.validate_for_publication(artifact.model_dump())
        existing_stmt = select(CapabilityVersionModel).where(
            CapabilityVersionModel.capability_id == artifact.capability_id,
            CapabilityVersionModel.version == artifact.version,
        )
        existing_result = await self.db.execute(existing_stmt)
        existing = existing_result.scalar_one_or_none()
        if existing:
            if existing.artifact_json != artifact.model_dump():
                raise ValueError(
                    f"Published artifact {artifact.capability_id} v{artifact.version} is immutable; create a new version"
                )
            return artifact

        stmt = select(CapabilityModel).where(CapabilityModel.capability_id == artifact.capability_id)
        res = await self.db.execute(stmt)
        cap = res.scalar_one_or_none()

        if not cap:
            cap = CapabilityModel(
                capability_id=artifact.capability_id,
                name=artifact.name,
                description=artifact.description,
                target_app=artifact.target_application,
                is_active=True
            )
            self.db.add(cap)
            await self.db.flush()

        ver = CapabilityVersionModel(
            capability_id=artifact.capability_id,
            version=artifact.version,
            artifact_json=artifact.model_dump()
        )
        self.db.add(ver)
        try:
            await self.db.flush()
            if commit:
                await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        if commit:
            self.write_artifact_mirror(artifact)
        return artifact

    def write_artifact_mirror(self, artifact: CapabilityArtifact) -> None:
        filename = f"{artifact.capability_id}_v{artifact.version}.json"
        filepath = os.path.join(settings.EVIDENCE_DIR, "artifacts", filename)
        temp_path = None
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        try:
            fd, temp_path = tempfile.mkstemp(prefix=f".{filename}.", suffix=".tmp", dir=os.path.dirname(filepath))
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(artifact.model_dump(), stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, filepath)
            logger.info("Published immutable capability artifact", capability_id=artifact.capability_id, version=artifact.version)
        except OSError as exc:
            logger.warning("Capability is durable in the database but its JSON mirror could not be replaced", capability_id=artifact.capability_id, error_type=type(exc).__name__)
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    async def next_version(self, capability_id: str) -> str:
        result = await self.db.execute(
            select(CapabilityVersionModel.version).where(CapabilityVersionModel.capability_id == capability_id)
        )
        versions = list(result.scalars().all())
        artifact_dir = os.path.join(settings.EVIDENCE_DIR, "artifacts")
        if os.path.isdir(artifact_dir):
            prefix = f"{capability_id}_v"
            for filename in os.listdir(artifact_dir):
                if filename.startswith(prefix) and filename.endswith(".json"):
                    candidate = filename[len(prefix):-5]
                    if re.fullmatch(r"\d+\.\d+\.\d+", candidate):
                        versions.append(candidate)
        parsed = []
        for version in versions:
            match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
            if match:
                parsed.append(tuple(map(int, match.groups())))
        if not parsed:
            return "1.0.0"
        major, minor, patch = max(parsed)
        return f"{major}.{minor}.{patch + 1}"

    async def get_artifact(self, capability_id: str, version: Optional[str] = None) -> Optional[CapabilityArtifact]:
        stmt = select(CapabilityVersionModel).where(CapabilityVersionModel.capability_id == capability_id)
        if version:
            stmt = stmt.where(CapabilityVersionModel.version == version)
        else:
            stmt = stmt.order_by(CapabilityVersionModel.created_at.desc()).limit(1)

        res = await self.db.execute(stmt)
        ver_model = res.scalar_one_or_none()
        if not ver_model:
            # Fallback to local disk file if DB table empty
            filename = f"{capability_id}_v{version or '1.0.0'}.json"
            filepath = os.path.join(settings.EVIDENCE_DIR, "artifacts", filename)
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return CapabilityArtifact.model_validate(data)
            return None

        return CapabilityArtifact.model_validate(ver_model.artifact_json)

    async def list_capabilities(self) -> List[dict]:
        stmt = select(CapabilityModel)
        res = await self.db.execute(stmt)
        caps = res.scalars().all()
        result = []
        for c in caps:
            stmt_v = select(CapabilityVersionModel).where(CapabilityVersionModel.capability_id == c.capability_id).order_by(CapabilityVersionModel.created_at.desc()).limit(1)
            res_v = await self.db.execute(stmt_v)
            latest = res_v.scalar_one_or_none()
            result.append({
                "capability_id": c.capability_id,
                "name": c.name,
                "description": c.description,
                "target_app": c.target_app,
                "is_active": c.is_active,
                "latest_version": latest.version if latest else "1.0.0",
                "created_at": c.created_at.isoformat() if c.created_at else None
            })
        # Include reviewed, checked-in artifacts that have not yet been copied into a fresh DB.
        known = {item["capability_id"] for item in result}
        artifacts_dir = os.path.join(settings.EVIDENCE_DIR, "artifacts")
        if os.path.isdir(artifacts_dir):
            for filename in os.listdir(artifacts_dir):
                if not filename.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(artifacts_dir, filename), "r", encoding="utf-8") as stream:
                        artifact = CapabilityArtifact.model_validate(json.load(stream))
                    if artifact.capability_id in known:
                        continue
                    known.add(artifact.capability_id)
                    result.append({
                        "capability_id": artifact.capability_id,
                        "name": artifact.name,
                        "description": artifact.description,
                        "target_app": artifact.target_application,
                        "is_active": True,
                        "latest_version": artifact.version,
                        "created_at": artifact.creation_metadata.get("compiled_at"),
                    })
                except (OSError, ValueError, json.JSONDecodeError):
                    logger.warning("Ignoring invalid capability artifact on disk", filename=filename)
        return result
