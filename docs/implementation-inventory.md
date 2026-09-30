# Implementation inventory

This inventory records source and local verification through 2026-09-30. It is not a roadmap promise. “Verified” means exercised by a test or local API/UI check in this environment, not suitable for production.

## Implemented and verified

- FastAPI workflow, run, capability, recording, handoff, health and safety routes.
- JWT/HttpOnly-cookie authentication, PBKDF2 password verification, Admin/Operator/Viewer roles, tenant filters, one-time bootstrap admin and self-service registration into the default tenant with a server-assigned Operator role.
- SQLite-backed orchestration, identity and synthetic banking records through SQLAlchemy async; startup applies additive versions `0003_handoff_security` and `0004_auth_registration`.
- Synthetic banking API and web UI; the default seeder creates 600 records for member IDs 1000–1599.
- Savings lookup and profile lookup artifact records are listed by the running API at version `1.0.0`.
- Deterministic replay's normal path contains no LLM client call; the integration test replaces the LLM factory with a client that fails if called.
- Mistral structured HTTP requests, response/schema validation, retry bounds, discovery step/request/time limits, and provider error classification.
- Incremental recording events and best-effort screenshots; an observed live rate-limited run persisted `OBSERVATION`, `MODEL_DECISION`, and `DISCOVERY_STEP_FAILED` with zero completed actions.
- Hashed idempotency key behavior: same request returned the prior run; changed request returned HTTP 409 in local API verification.
- React pages include sign-in, invite registration, session restoration and handoff recovery; active run/recording views use interval polling.
- Backend test suite passed 44 tests and frontend production build succeeded on 2026-09-30 (see [Testing](testing.md)).

## Implemented but lightly tested

- Run cancellation/timeout persistence and enforced recording state transitions; there are no dedicated tests for cancellation races or all transition edges.
- Handoff API validation and resume checkpoint behavior; no automated HITL integration test exercises the complete UI-to-resume path.
- Recording event ordering, partial failure preservation, sensitive-value redaction and screenshot-missing states; coverage is incomplete.
- Artifact versioning, atomic JSON mirror replacement and storage fallback; tests do not cover crash consistency or concurrent publication.
- Capability goal matching; tests cover identifier extraction but not the full matching matrix or application drift.

## Partial or constrained

- **Parameterization:** compiler generalizes a matching 4–5 digit member ID in an executed fill/select. Other fill/select values are saved literally. It does not infer arbitrary reusable variables.
- **Checkpoints:** schema supports several rules, while compiler/replay behavior is narrower; compiler-generated click checkpoints check a `/member/` URL fragment. Replay checks step rules and final success condition, but this is not a general semantic verifier.
- **Action surface:** `select` is executed; artifact `assert` is accepted but does not perform an independent assertion beyond attached/final checks. `aria_fallback` is represented by schema but not consulted by replay locator resolution.
- **Handoff resume:** live resume continues after a verified operator-resolved checkpoint; restart recovery reconstructs only actions classified safe to retry after state reconstruction. Cookies/page memory are not restored and discovery has no published replay plan.
- **Evidence:** persisted metadata may point to screenshots, but capture is best effort. Evidence access checks authenticated tenant ownership and path containment.
- **Database portability:** model definitions are SQLAlchemy-based, but only SQLite is fully installed/documented. PostgreSQL is mentioned in Compose; `asyncpg` and both referenced Dockerfiles are absent.
- **Migration:** additive versions `0003_handoff_security` and `0004_auth_registration` run at startup. There is no automatic downgrade; back up SQLite before upgrade.
- **Risk policy:** exact simulator routes and action/selector allowlists fail closed. They are not a general browser sandbox or a production authorization boundary.
- **Authentication:** login and registration throttling are in-process and need a shared limiter for multi-worker deployment. Audit records are not tamper-proof against database administrators.

## Broken or inconsistent developer paths

- `docker-compose.yml` references root `Dockerfile` and `target-app/Dockerfile`, neither exists. It configures `postgresql+asyncpg`, but `asyncpg` is not declared. Do not use Compose as a documented setup path without fixing and verifying it.
- `scripts/run_replay.py` reads `evidence/artifacts/member_savings_lookup_v1.0.0.json` directly; it does not fall back to SQLite. The current workspace has the mirror, but an environment that omits the file can have a DB artifact that the script will not find. Use the API/console in that case.
- The existing `scripts/run_final_demo.py` changes the target simulator dialog state and creates workflow records. It is not a read-only health check and does not restore simulator state in a `finally` block if interrupted.
- Environment template suggests PostgreSQL support and exposes only a subset of settings; actual default is SQLite and optional settings are defined in `backend/app/core/config.py`.
- `SAFETY_ALLOW_RISKY_ACTIONS` is defined in settings but is not passed into `default_safety_policy`; changing the environment value does not change policy behavior.
- The current runtime reports Mistral configured, but live requests have returned 429. “Configured” is not a reachability or entitlement check.

## Planned or not implemented

- Native desktop automation, distributed workers, browser session restoration, tenant-specific capability registries, shared rate limiting, PostgreSQL deployment verification, production deployment, frontend unit tests, and automated frontend E2E tests.
- Dedicated compiled workflows for transactions, cards, loans and statements.
- A formal license, contribution policy or supported production security boundary.

For security implications, see [Safety and security](safety-and-security.md). For test gaps, see [Testing](testing.md).
