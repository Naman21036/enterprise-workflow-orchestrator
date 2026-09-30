import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.artifacts.schema import CapabilityArtifact
from backend.app.artifacts.storage import ArtifactStorage
from backend.app.core.errors import ArtifactValidationError
from backend.app.db.database import Base
from backend.app.db.models import CapabilityModel, CapabilityVersionModel


def payload(version="1.0.0"):
    return {
        "schema_version": "1.0.0",
        "capability_id": "member_lookup",
        "version": version,
        "name": "Member lookup",
        "description": "Read synthetic member data",
        "target_application": "APEX Federal",
        "surface_type": "web",
        "steps": [{"step_number": 1, "action_type": "navigate", "target": {"primary_selector": "body"}, "value_expression": "http://localhost:3001"}],
        "success_condition": {"rule_type": "url_contains", "target": "/member/"},
    }


@pytest.mark.asyncio
async def test_storage_selects_highest_semver_not_latest_inserted_timestamp():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(CapabilityModel(capability_id="member_lookup", name="Lookup", description="Read", target_app="APEX Federal", is_active=True))
        db.add(CapabilityVersionModel(capability_id="member_lookup", version="1.10.0", artifact_json=payload("1.10.0")))
        db.add(CapabilityVersionModel(capability_id="member_lookup", version="1.9.0", artifact_json=payload("1.9.0")))
        await db.commit()
        result = await ArtifactStorage(db).get_artifact("member_lookup")
        assert result is not None
        assert result.version == "1.10.0"
    await engine.dispose()


@pytest.mark.asyncio
async def test_corrupted_latest_artifact_fails_closed_instead_of_falling_back():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(CapabilityModel(capability_id="member_lookup", name="Lookup", description="Read", target_app="APEX Federal", is_active=True))
        db.add(CapabilityVersionModel(capability_id="member_lookup", version="1.0.0", artifact_json=payload("1.0.0")))
        corrupt = payload("2.0.0")
        corrupt["schema_version"] = "99.0.0"
        db.add(CapabilityVersionModel(capability_id="member_lookup", version="2.0.0", artifact_json=corrupt))
        await db.commit()
        with pytest.raises(ArtifactValidationError):
            await ArtifactStorage(db).get_artifact("member_lookup")
    await engine.dispose()
