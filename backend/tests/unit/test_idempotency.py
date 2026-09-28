from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.database import Base
from backend.app.db.models import RunModel
from backend.app.orchestration.router import WorkflowRouter


def run(run_id):
    return RunModel(
        id=run_id,
        goal="Find member [REDACTED] and retrieve their savings balance.",
        target_app="APEX Federal",
        execution_mode="Discovery",
        status="RUNNING",
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_idempotency_key_reuses_active_run_and_rejects_different_request():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    key_hash = "a" * 64

    async with sessions() as session:
        router = WorkflowRouter(session)
        assert await router._claim_run(run("wf_original"), key_hash, "request-one") is None

    async with sessions() as session:
        router = WorkflowRouter(session)
        duplicate = await router._claim_run(run("wf_duplicate"), key_hash, "request-one")
        assert duplicate["run_id"] == "wf_original"
        assert duplicate["status"] == "RUNNING"
        assert duplicate["duplicate_request"] is True

    async with sessions() as session:
        router = WorkflowRouter(session)
        conflict = await router._claim_run(run("wf_conflict"), key_hash, "request-two")
        assert conflict["error_code"] == "IDEMPOTENCY_KEY_CONFLICT"
        assert conflict["status"] == "FAILED"

    await engine.dispose()
