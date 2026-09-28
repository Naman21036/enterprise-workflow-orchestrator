# APEX Automation Engineering Report

This status report is a concise snapshot. The maintained technical guide is [docs/index.md](docs/index.md), with source-based detail, screenshots, API reference and known limits.

## 1. System shape

FastAPI routes workflow requests to either an existing compatible capability or the Mistral discovery agent. The browser surface uses Playwright against the local APEX Federal banking app. A successful discovery trace is compiled, schema-validated, and replayed in a clean browser context before the artifact is published. Subsequent compatible runs execute the artifact without a model decision loop. A run semaphore bounds local concurrency.

The local database is SQLite. Banking data and orchestration state share the configured database. SQLAlchemy enables SQLite foreign-key enforcement on application connections. Tables are initialized additively with SQLAlchemy metadata; this project does not currently have an Alembic migration history.

## 2. Synthetic banking database

`scripts.seed_database` deterministically creates 600 synthetic members by default, IDs 1000–1599, with related accounts, transactions, cards, loans, and statements. It includes meaningful cases such as members without deposit accounts, multiple savings accounts, closed or frozen accounts, pending transactions, and paid loans. Seed rows carry synthetic markers. A reset requires both `--reset-demo-data` and `--confirm-demo-reset` and deletes only the tagged APEX synthetic batch.

The target app loads member and related records from the database, rather than a hard-coded member map. It provides a member API and views for profile, accounts, transactions, cards, loans, and statements. Email, phone, and account numbers are masked in the member API. The banking information remains synthetic. Current account balances are sample snapshots; synthetic transaction histories are independent samples and are not a reconciled ledger.

## 3. Recording and artifact lifecycle

Discovery recordings persist the run association, sanitized action trace, checkpoints, evidence paths, and ordered interaction events. Each event is committed as the agent progresses so an interrupted attempt retains its trace. Recordings expose their events and linked replay runs through the recording detail API. Failed Mistral attempts remain visible as failed recordings.

Artifacts use a typed Pydantic schema with supported action/checkpoint literals, target validation, contiguous step numbering, declared inputs/outputs, and versioning. JSON artifact mirrors are written through a staged atomic replacement after the database commit. A newly compiled artifact is published only after successful deterministic replay. Screenshot capture is best effort and does not block browser work when the browser rejects evidence capture.

The recording lifecycle enforces valid transitions through discovery, compilation, publication, and terminal failure/block/cancel states. Events include sanitized observations, model decisions, and confirmed browser actions, with run correlation and evidence-availability metadata. Failed provider calls do not inflate action counts. Run and recording views poll active work and stop after terminal states. Optional idempotency keys are stored as hashes alongside request fingerprints, and conflicting reuse returns HTTP 409.

## 4. Replay and outcomes

Replay binds required inputs, enforces allowed origins/actions, executes stored selectors and steps, checks checkpoints and terminal conditions, and validates extracted values. It does not call the LLM. Verified runs return zero LLM decision calls. Member `1002` replay returned John Doe and `$7,250.00` in four successful steps. Member `1013` returned `AMBIGUOUS_SAVINGS_ACCOUNT`; unknown member `99999` returned `MEMBER_NOT_FOUND`. These are business outcomes rather than false successful balance lookups.

## 5. Windows browser diagnosis

The original `WinError 5` occurred while Python Playwright attempted to create its Windows driver IPC pipe, before Chromium actions began. It was not a banking application route or profile-file failure. The backend now records the failed startup operation and reports a specific permission-denied classification. The browser-backed acceptance tests passed when executed with the required process-child permissions. The health endpoint distinguishes Chromium installation from launch permission, which it reports as unprobed instead of claiming a browser launch was tested.

## 6. Human intervention and safety

Unexpected confirmation dialogs pause the run for a same-session operator handoff. Handoff requests accept only the supported action types and bounded parameters; resume checks the expected member page before returning a result. A process restart marks persisted handoffs as lost rather than claiming the previous browser session still exists.

Workflow inputs validate the target application and member identifier. Replay and discovery use the local target origin and safety policy. Persisted workflow details apply common pattern/key-based redaction; this is incomplete, screenshots may contain page data, and discovery observations are sent to Mistral. Configuration secrets are not returned by health endpoints. Error logs avoid raw provider response bodies, and surfaced discovery failures identify provider rate limiting without exposing credentials. The policy is a local guardrail, not production authorization or isolation.

Mistral requests have configured timeouts, bounded retries with capped `Retry-After` handling, bounded response size, a limited output budget, and per-run decision and duration limits. Provider errors are classified separately for rate limiting, authentication, transport, timeout, malformed output, and schema mismatch. Logs contain safe request metadata and usage totals, not credentials or prompt content.

## 7. Verification and current limits

- **2026-09-29:** full backend suite including browser replay: 20 passed with Windows process permissions for Playwright.
- **2026-09-29:** frontend production build passed (`tsc` and Vite; 1,477 modules transformed).
- **2026-09-29:** `python -m scripts.run_replay` passed for member 1002 (`SUCCESS`, four steps) and member 99999 (`BUSINESS_OUTCOME`, `MEMBER_NOT_FOUND`); the script configured an LLM client that throws if replay calls it.
- **2026-09-29:** current console screenshots were captured after the UI reported all systems operational; readiness had database and target app online and Chromium installed. Launch permission and Mistral reachability are not checked by the readiness endpoint.
- Previously verified local deterministic replay also covered ambiguous savings accounts for member 1013; each replay used zero LLM decision calls.
- The seeded SQLite database contains 600 members, 1,129 accounts, 13,548 transactions, 524 cards, 180 loans, and 3,387 statements.
- The seeded SQLite database contains 600 members, 1,129 accounts, 13,548 transactions, 524 cards, 180 loans, and 3,387 statements.
- Three real Mistral discovery attempts returned HTTP 429 (`MISTRAL_RATE_LIMITED`), so no successful new capability could be compiled during this run. The latest end-to-end attempt exercised the bounded-retry path and persisted its before-action observation, model decision, failure event, and screenshots with zero browser actions; retrying the same idempotency key returned the existing run without another model call, and conflicting reuse returned HTTP 409.

The local workspace currently has savings and profile capability mirrors. Transaction, card, loan, and statement data is present in the persisted target UI, but dedicated compiled workflows for those domains are not implemented. Cross-tenant automation, a desktop surface, distributed execution, API authentication, and a database migration framework remain outside the current implementation. Compose is not runnable from this checkout because its Dockerfiles and PostgreSQL driver are absent.
