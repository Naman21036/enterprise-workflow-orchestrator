import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.db import migration_runner


@pytest.mark.asyncio
async def test_security_migration_upgrades_legacy_sqlite_schema(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(migration_runner, "engine", engine)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE runs (id VARCHAR PRIMARY KEY, goal TEXT NOT NULL, target_app VARCHAR NOT NULL, execution_mode VARCHAR NOT NULL, status VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL)"))
        await connection.execute(text("CREATE TABLE run_steps (id VARCHAR PRIMARY KEY, run_id VARCHAR NOT NULL, step_number INTEGER NOT NULL, action_type VARCHAR NOT NULL, status VARCHAR NOT NULL)"))
        await connection.execute(text("CREATE TABLE operators (id VARCHAR(80) PRIMARY KEY, tenant_id VARCHAR(80) NOT NULL, username VARCHAR(160) NOT NULL UNIQUE, password_hash VARCHAR(256) NOT NULL, role VARCHAR(16) NOT NULL, is_active BOOLEAN NOT NULL, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL)"))

    await migration_runner.run_migrations()
    async with engine.begin() as connection:
        run_columns = await connection.run_sync(lambda sync: {item["name"] for item in inspect(sync).get_columns("runs")})
        step_columns = await connection.run_sync(lambda sync: {item["name"] for item in inspect(sync).get_columns("run_steps")})
        operator_columns = await connection.run_sync(lambda sync: {item["name"] for item in inspect(sync).get_columns("operators")})
        tenant_id = (await connection.execute(text("SELECT id FROM tenants WHERE id='default'"))).scalar_one()
        version = (await connection.execute(text("SELECT version FROM schema_migrations WHERE version='0003_handoff_security'"))).scalar_one()
        registration_version = (await connection.execute(text("SELECT version FROM schema_migrations WHERE version='0004_auth_registration'"))).scalar_one()
        bootstrap_state_exists = await connection.run_sync(lambda sync: "bootstrap_state" in inspect(sync).get_table_names())
        indexes = await connection.run_sync(lambda sync: {item["name"] for item in inspect(sync).get_indexes("runs")})
        operator_indexes = set((await connection.execute(text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='operators'"))).scalars().all())
    assert {"tenant_id", "owner_id"} <= run_columns
    assert "action_id" in step_columns
    assert tenant_id == "default"
    assert version == "0003_handoff_security"
    assert registration_version == "0004_auth_registration"
    assert bootstrap_state_exists
    assert "ix_runs_tenant_id" in indexes
    assert {"full_name", "email"} <= operator_columns
    assert {"uq_operators_username_casefold", "uq_operators_email_casefold"} <= operator_indexes
    async with engine.begin() as connection:
        await connection.execute(text("INSERT INTO operators (id, tenant_id, username, full_name, email, password_hash, role, is_active, created_at, updated_at) VALUES ('op-1', 'default', 'CaseUser', 'Case User', 'Case@Example.test', 'hash', 'OPERATOR', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO operators (id, tenant_id, username, full_name, email, password_hash, role, is_active, created_at, updated_at) VALUES ('op-2', 'default', 'caseuser', 'Duplicate', 'other@example.test', 'hash', 'OPERATOR', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO operators (id, tenant_id, username, full_name, email, password_hash, role, is_active, created_at, updated_at) VALUES ('op-3', 'default', 'another', 'Duplicate Email', 'case@example.TEST', 'hash', 'OPERATOR', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
    await migration_runner.run_migrations()
    await engine.dispose()
