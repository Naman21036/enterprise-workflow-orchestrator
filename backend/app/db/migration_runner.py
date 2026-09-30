"""Small, explicit additive migration runner for the repository's existing SQL migrations."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import inspect, select

from backend.app.db.database import Base, engine
from backend.app.db.models import SchemaMigrationModel, TenantModel


def _apply_security_handoff_schema(connection) -> None:
    """Add tenant scoping to legacy runs; create versioned session/audit tables."""
    inspector = inspect(connection)
    if "runs" in inspector.get_table_names():
        run_columns = {column["name"] for column in inspector.get_columns("runs")}
        if "tenant_id" not in run_columns:
            connection.exec_driver_sql(
                "ALTER TABLE runs ADD COLUMN tenant_id VARCHAR(80) NOT NULL DEFAULT 'default'"
            )
        if "owner_id" not in run_columns:
            connection.exec_driver_sql("ALTER TABLE runs ADD COLUMN owner_id VARCHAR(80) NULL")
    if "run_steps" in inspector.get_table_names():
        step_columns = {column["name"] for column in inspector.get_columns("run_steps")}
        if "action_id" not in step_columns:
            connection.exec_driver_sql("ALTER TABLE run_steps ADD COLUMN action_id VARCHAR(96) NULL")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_run_steps_action_id ON run_steps (action_id)")
        connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_run_step_action_id ON run_steps (run_id, action_id)")
    if "runs" in inspector.get_table_names():
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_runs_tenant_id ON runs (tenant_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_runs_owner_id ON runs (owner_id)")
    Base.metadata.create_all(connection)
    tenants = TenantModel.__table__
    existing_default = connection.execute(
        select(tenants.c.id).where(tenants.c.id == "default")
    ).first()
    if not existing_default:
        connection.execute(tenants.insert().values(id="default", name="Default tenant", is_active=True))


def _apply_auth_registration_schema(connection) -> None:
    """Add optional identity fields for legacy operators and invitation storage."""
    inspector = inspect(connection)
    if "operators" in inspector.get_table_names():
        operator_columns = {column["name"] for column in inspector.get_columns("operators")}
        if "full_name" not in operator_columns:
            connection.exec_driver_sql("ALTER TABLE operators ADD COLUMN full_name VARCHAR(160) NULL")
        if "email" not in operator_columns:
            connection.exec_driver_sql("ALTER TABLE operators ADD COLUMN email VARCHAR(320) NULL")
        # New API-created identities are normalized, and the indexes also protect
        # case-insensitive uniqueness against concurrent or direct DB inserts.
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_operators_username_casefold ON operators (lower(username))"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_operators_email_casefold ON operators (lower(email)) WHERE email IS NOT NULL"
        )
    Base.metadata.create_all(connection)


async def run_migrations() -> None:
    """Run only unapplied additive migrations, then ensure model-created tables exist."""
    async with engine.begin() as connection:
        await connection.run_sync(SchemaMigrationModel.__table__.create, checkfirst=True)
        versions = set((await connection.execute(select(SchemaMigrationModel.version))).scalars().all())
        if "0003_handoff_security" not in versions:
            await connection.run_sync(_apply_security_handoff_schema)
            await connection.execute(
                SchemaMigrationModel.__table__.insert().values(
                    version="0003_handoff_security",
                    applied_at=datetime.now(timezone.utc),
                )
            )
        if "0004_auth_registration" not in versions:
            await connection.run_sync(_apply_auth_registration_schema)
            await connection.execute(
                SchemaMigrationModel.__table__.insert().values(
                    version="0004_auth_registration",
                    applied_at=datetime.now(timezone.utc),
                )
            )
        await connection.run_sync(Base.metadata.create_all)
