-- Additive migration for idempotent workflow request keys.
-- Compatible with the supported local SQLite and configured PostgreSQL schemas.
CREATE TABLE IF NOT EXISTS workflow_idempotency (
    key_hash VARCHAR(64) PRIMARY KEY,
    request_hash VARCHAR(64) NOT NULL,
    run_id VARCHAR NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_workflow_idempotency_run_id ON workflow_idempotency (run_id);
