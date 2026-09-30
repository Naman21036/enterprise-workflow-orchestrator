# Testing

## Test architecture

`pyproject.toml` configures pytest with `pytest-asyncio` auto mode and `backend/tests` as the test root.

| Group | Files and intent | Current coverage limits |
|---|---|---|
| Artifact schema | `unit/test_artifact_schema.py`: valid publication, unknown fields/actions and contiguous steps. | No older-version compatibility/migration fixture or fuzzing. |
| Seed model | `unit/test_banking_seed.py`: deterministic 600-member fixture, varied cases and sample-member values. | Does not exercise concurrent DB seeding or application UI for every data edge. |
| Idempotency | `unit/test_idempotency.py`: same-key reuse and different-fingerprint conflict. | Not a concurrent HTTP race/DB backend matrix test; current code path tested at router/unit and one local API scenario. |
| Mistral client | `unit/test_mistral_client.py`: missing key, valid schema response, 429 retry, auth non-retry, malformed/schema response and timeout classification. | Uses mocked HTTP responses; no live provider guarantee. |
| Replay initialization | `unit/test_replay_initialization.py`: Windows Playwright permission error is classified before actions. | Does not test all runtime/browser versions. |
| Router | `unit/test_router.py`: member extraction, missing member ID and actual-action count semantics. | Does not cover full end-to-end discovery/publication path. |
| Safety | `unit/test_safety.py`: host validation, risk classification and selected redaction patterns. | Heuristics are not adversarially complete; screenshot OCR/secret scan absent. |
| Authentication and registration | `unit/test_auth_and_checkpoint.py`, `unit/test_registration.py`: JWT integrity/expiry, bootstrap idempotence, login by username/email, self-service registration, validation, duplicate identity handling, throttling and tenant/role restrictions. | Tests use SQLite and an in-process ASGI client; no multi-process rate-limit backend or PostgreSQL test service is configured. |
| Schema migration | `unit/test_migration_runner.py`: additive upgrade from a legacy SQLite operator/run schema, indexes and repeatable startup. | No PostgreSQL migration execution is verified in this checkout. |
| LLM-free replay integration | `integration/test_critical_no_llm_replay.py`: patches LLM factory with a client that raises, then replays member 1002 against local app. | Requires target service, installed Chromium and process permission; only a narrow successful capability path. |

At the original audit there were 19 unit tests and one integration test (20 total). The current suite also includes safety-boundary, handoff-guard, artifact-storage, replay-boundary, observability, authentication, registration and migration coverage. There is no frontend unit test runner, Playwright browser E2E suite for the React app, or CI configuration detected.

## Commands

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\unit -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest backend\tests -q -p no:cacheprovider
Push-Location frontend
npm run build
Pop-Location
```

The full backend test includes a real local browser launch. On Windows it may require permission for Playwright's Python driver subprocess to create an IPC pipe; a regular sandboxed invocation in this environment produced WinError 5, while the same suite with process permissions passed.

The frontend `build` command runs `tsc` followed by Vite production bundling. It is a typecheck/build, not a UI interaction test.

## Audit result

On 2026-09-29, after the documentation audit and prior implementation changes:

- `python -m pytest backend/tests -q -p no:cacheprovider`: **20 passed** when run with Windows process permissions needed by Playwright.
- `npm run build`: **passed**, Vite transformed 1,477 modules and emitted the production assets.
- `python -m scripts.run_replay`: **passed** against the local simulator; member 1002 returned `SUCCESS` in four steps, member 99999 returned `BUSINESS_OUTCOME` / `MEMBER_NOT_FOUND`, and the script replaced the LLM client with a forbidden-call stub.
- A live read-only API readiness check returned DB healthy, target online and Chromium installed. The Mistral field reported configured, not verified reachable.
- A controlled live discovery call returned HTTP 429 after bounded client retries. It left a failed recording with before observation, model-decision and failure events, two available evidence files, and zero completed UI actions. Same-key retry returned the prior run; conflicting request returned HTTP 409.
- Local screenshots in `docs/screenshots/` were captured with Playwright against the running console after `All systems operational` appeared.

These are local-environment results, not a claim that external Mistral availability or every workflow passes.

## Latest safety, replay and telemetry verification

On 2026-09-30, after the safety/HITL/telemetry changes:

- `python -m compileall -q backend/app`: **passed**.
- `python -m pytest backend/tests -q`: **37 passed**. This includes the real-browser LLM-free replay integration and the new guardrail/storage/handoff/observability tests. The real browser test needs local Playwright process permissions on Windows.
- `npm run build`: **passed** (`tsc` and Vite, 1,477 modules).
- A new synthetic **Mistral discovery** reached the provider after the OpenTelemetry context-manager bug was fixed. The provider returned HTTP 429 after the configured bounded retry; the attempt is persisted as `FAILED` / `MISTRAL_RATE_LIMITED`, with zero recorded browser actions and no published artifact.
- A distinct `Deterministic Replay` run then loaded persisted `member_savings_lookup` v1.0.0 and completed successfully for synthetic member 1003. The run row is separate from the failed discovery, returns the expected output field names, and performs no LLM decision calls. This verifies cross-run stored-artifact replay for changed input, but it does not prove a new Mistral-created artifact because discovery was rate-limited.
- A real simulator dialog produced a `BLOCKED` replay with a live page. The operator's exact `#confirm-dialog-btn` click was accepted once, and resume re-observed the same page and recorded `SUCCESS` for member 1002. The resolved run ID is `wf_55770ed0`; its handoff screenshot is under `evidence/escalations/`. A prior manual attempt to type after confirming was correctly rejected because confirmation had already navigated the page; the final validation used the expected confirm-then-resume lifecycle.
- With `APEX_OTEL_ENABLED=true`, console output contained discovery/model-decision/replay spans and workflow/safety metrics. Telemetry used synthetic identifiers and filtered fields.
- LangSmith configuration resolved as enabled/key-present without disclosing the credential, but a live `Client.list_runs` check was rejected with `LangSmithAuthError`. LangSmith trace upload is therefore **not confirmed** in this environment; the configured credential or workspace authorization must be corrected before claiming cloud trace delivery.

These are observed local results. Do not interpret LangSmith's configured status as successful remote authentication or assume Mistral availability from its key-presence check.

## Authentication and registration verification

The previous full backend run passed **44 tests** before self-service registration replaced invitation-required signup. Re-run the backend suite and frontend production build when changing authentication. No React browser E2E runner is configured, so automated verification does not claim a visual form interaction test.

## High-value tests not yet present

1. Concurrent idempotency-key submissions over the production DB engine, with request-body hash edge cases.
2. Recording callback persistence across cancellation, DB failure and partial screenshot write; event sequence/race behavior.
3. Full Mistral discovery-to-compile-to-clean-replay publication with mocked provider decisions and browser fixture.
4. UI drift, missing/fallback selectors, locator ambiguity and browser termination mid-run.
5. Human handoff action validation, resume success/business outcome, failed checkpoint and backend restart/session loss.
6. Sensitive data in observation payloads, error traces and screenshots; static evidence authorization.
7. Artifact compatibility across schema versions and concurrent publication/version allocation.
8. Frontend view behavior for active polling, terminal polling stop, API errors and evidence-missing display.

## Reproducing actual commands

Fresh installation can be followed with `pip install -e ".[dev]"`, `python -m playwright install chromium`, `npm ci`, seeding and the three local service commands in [Development](development.md). These exact operations were not re-run from a clean clone during this audit; current environment versions and app/build/tests were verified. The seed help syntax can be checked safely with:

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_database --help
```

Do not run seed with reset flags against a non-demo database.
