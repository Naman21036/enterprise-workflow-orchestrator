# Troubleshooting

## `WinError 5` while starting Playwright

**Symptom:** run fails before the first browser action with `PLAYWRIGHT_DRIVER_PERMISSION_DENIED`, `BROWSER_PERMISSION_DENIED`, or `BROWSER_INITIALIZATION_FAILED`; logs show `playwright_driver_start` or Chromium launch.

**Cause:** Chromium being installed does not prove the Python process may start Playwright's driver or create its Windows IPC pipe. This repository has seen `PermissionError: [WinError 5] Access is denied` during child-process pipe creation.

**Remedy:** verify the active `.venv` and Chromium installation, run the backend without Uvicorn `--reload`, and use a process environment allowed to start the Playwright driver/IPC. Re-run the single local replay integration test and inspect its exact failure operation. Do not treat `/health/ready`'s Chromium `installed` field as a launch probe; it explicitly reports launch permission `not_probed`.

## Mistral returns HTTP 429

The client retries a bounded number of times (default one retry; max is clamped to three), honors capped `Retry-After`, then raises `MISTRAL_RATE_LIMITED`. The failed discovery is persisted; it does not create an artifact or execute a browser action based on a fabricated fallback. Check Mistral account entitlement/quota/model availability outside this app, then retry later with a new or same request policy as appropriate. Health only checks that a non-placeholder key is configured.

## Mistral authentication or configuration failure

- `MISTRAL_CONFIGURATION_REQUIRED`: set a non-placeholder `MISTRAL_API_KEY` in repo-root `.env` or process environment; restart backend.
- `MISTRAL_AUTHENTICATION_FAILED`: key was rejected (401/403); verify key is valid and not revoked. Auth errors are not retried.
- Do not print the key in diagnostic output. Discovery only needs it; saved replay does not.

## Target app offline or wrong database

Verify `http://127.0.0.1:3001` loads and the target, API and seeder share the same `DATABASE_URL` and working directory. A relative SQLite URL resolves from the process current directory; starting a service from another directory can create/use a different DB with empty members or missing capabilities. Health checks target HTTP and database independently.

## Seed reports existing member IDs

The seeder intentionally refuses collisions. For a fresh DB, run the default seed once. To replace only the tagged synthetic demo batch, use both `--reset-demo-data` and `--confirm-demo-reset`. The reset is scoped to rows marked with the demo batch and should not be applied to a database containing anything else.

## Replay says no compatible capability

List `GET /api/v1/capabilities` and inspect exact capability ID/version. The router currently recognizes savings-balance and profile goals by keywords. Forced replay never falls back to discovery; remove `force_mode=REPLAY` or deliberately request a separate discovery if the task is supported and provider access is available.

## Replay fails after a UI change

Inspect run steps and screenshots in Workflow runs/Evidence. The replay engine will not ask Mistral to repair selectors. Review artifact locators, target routes, output selectors and final condition; update/recompile a new version only after validating it against the simulator.

## Missing screenshot or evidence file

Screenshot capture is best effort. API `evidence_available=false` means a path was recorded but the file no longer exists; an absent path means the step did not return one or capture failed before a path was stored. Verify `EVIDENCE_DIR` matches the backend working directory and that the process can write there. Evidence APIs intentionally do not fabricate images.

## Discovery returns without recording

Validation errors such as invalid JSON/member ID may be rejected before run creation. For errors after a run claim, check `/api/v1/runs` and recordings separately. A run is not always a discovery recording; replay runs have step rows but no discovery recording. Failed discovery after recording initialization should retain an incremental recording unless the DB itself failed.

## Idempotency returns HTTP 409

An idempotency key is bound to request goal, target, inputs, mode and capability ID. A different request with the same key is rejected. Use a new key for a genuinely new request; reuse the same key only for a retry of the same submission.

## Docker Compose failure

Compose references missing `Dockerfile` and `target-app/Dockerfile`; the PostgreSQL URL uses `asyncpg`, which is not declared by `pyproject.toml`. This repository does not currently have a reproducible container setup. Use the documented local SQLite setup until container support is implemented and validated.

## `scripts.run_replay` says artifact JSON is missing

The script reads only `evidence/artifacts/member_savings_lookup_v1.0.0.json`; the running service may have the artifact only in its SQLite tables. Use the Capabilities page or `POST /api/v1/workflows/replay` / workflow `force_mode=REPLAY`. The API storage layer reads database artifacts first.
