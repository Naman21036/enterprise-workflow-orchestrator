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
from backend.app.observability import span, set_attributes
from backend.app.core.errors import ArtifactValidationError

class ArtifactStorage:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    @staticmethod
    def _version_key(value: str) -> tuple[int, int, int]:
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value or "")
        if not match:
            raise ValueError("Invalid semantic artifact version")
        return tuple(map(int, match.groups()))

    async def save_artifact(self, artifact: CapabilityArtifact, commit: bool = True) -> CapabilityArtifact:
        with span("apex.artifact.persist", {"capability.id": artifact.capability_id, "artifact.version": artifact.version}) as current:
            saved = await self._save_artifact(artifact, commit)
            set_attributes(current, {"artifact.persisted": True})
            return saved

    async def _save_artifact(self, artifact: CapabilityArtifact, commit: bool = True) -> CapabilityArtifact:
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
        with span("apex.artifact.retrieve", {"capability.id": capability_id, "artifact.version": version or "latest"}) as current:
            artifact = await self._get_artifact(capability_id, version)
            set_attributes(current, {"artifact.found": artifact is not None, "artifact.version": artifact.version if artifact else version})
            return artifact

    async def _get_artifact(self, capability_id: str, version: Optional[str] = None) -> Optional[CapabilityArtifact]:
        stmt = select(CapabilityVersionModel).where(CapabilityVersionModel.capability_id == capability_id)
        if version:
            stmt = stmt.where(CapabilityVersionModel.version == version)
        res = await self.db.execute(stmt)
        candidates = list(res.scalars().all())
        if version:
            ver_model = next((item for item in candidates if item.version == version), None)
        else:
            try:
                ver_model = max(candidates, key=lambda item: self._version_key(item.version)) if candidates else None
            except ValueError as exc:
                raise ArtifactValidationError(capability_id) from exc
        if not ver_model:
            # Fallback to local disk file if DB table empty
            artifact_dir = os.path.join(settings.EVIDENCE_DIR, "artifacts")
            if version:
                candidates = [f"{capability_id}_v{version}.json"]
            else:
                prefix = f"{capability_id}_v"
                candidates = [name for name in os.listdir(artifact_dir) if name.startswith(prefix) and name.endswith(".json")] if os.path.isdir(artifact_dir) else []
                try:
                    candidates.sort(key=lambda name: self._version_key(name[len(prefix):-5]), reverse=True)
                except ValueError as exc:
                    raise ArtifactValidationError(capability_id) from exc
            filepath = os.path.join(artifact_dir, candidates[0]) if candidates else ""
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    artifact = CapabilityArtifact.validate_for_publication(data)
                    if artifact.capability_id != capability_id or (version and artifact.version != version):
                        raise ValueError("Artifact identity does not match its storage key")
                    return artifact
                except Exception as exc:
                    raise ArtifactValidationError(capability_id, version) from exc
            return None

        try:
            artifact = CapabilityArtifact.validate_for_publication(ver_model.artifact_json)
            if artifact.capability_id != capability_id or artifact.version != ver_model.version:
                raise ValueError("Artifact identity does not match its database version row")
            return artifact
        except Exception as exc:
            raise ArtifactValidationError(capability_id, ver_model.version) from exc

    async def list_capabilities(self) -> List[dict]:
        stmt = select(CapabilityModel)
        res = await self.db.execute(stmt)
        caps = res.scalars().all()
        result = []
        for c in caps:
            stmt_v = select(CapabilityVersionModel).where(CapabilityVersionModel.capability_id == c.capability_id)
            res_v = await self.db.execute(stmt_v)
            versions = list(res_v.scalars().all())
            try:
                latest = max(versions, key=lambda item: self._version_key(item.version)) if versions else None
            except ValueError as exc:
                raise ArtifactValidationError(c.capability_id) from exc
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
            mirrors: dict[str, list[tuple[tuple[int, int, int], str, str]]] = {}
            for filename in os.listdir(artifacts_dir):
                match = re.fullmatch(r"([A-Za-z0-9_-]{1,80})_v(\d+\.\d+\.\d+)\.json", filename)
                if match:
                    cap_id, version = match.groups()
                    mirrors.setdefault(cap_id, []).append((self._version_key(version), version, filename))
            for cap_id, candidates in mirrors.items():
                if cap_id in known:
                    continue
                _, version, filename = max(candidates)
                try:
                    with open(os.path.join(artifacts_dir, filename), "r", encoding="utf-8") as stream:
                        artifact = CapabilityArtifact.validate_for_publication(json.load(stream))
                    if artifact.capability_id != cap_id or artifact.version != version:
                        raise ValueError("Artifact contents do not match the mirror filename")
                except Exception as exc:
                    raise ArtifactValidationError(cap_id, version) from exc
                result.append({
                    "capability_id": artifact.capability_id,
                    "name": artifact.name,
                    "description": artifact.description,
                    "target_app": artifact.target_application,
                    "is_active": True,
                    "latest_version": artifact.version,
                    "created_at": artifact.creation_metadata.get("compiled_at"),
                })
        return result
