# Development and local setup

## Prerequisites

- Python 3.12+ (declared in `pyproject.toml`).
- Node.js/npm for the React/Vite console. No Node version is pinned in the repo; current docs/build were checked on Node 22.23.2 and npm 10.9.8.
- A Playwright Chromium build for browser replay/discovery.
- A Mistral key for discovery only.

The repository is a local multi-process demonstration. Docker Compose is not a supported setup: required Dockerfiles are absent and PostgreSQL driver `asyncpg` is not in project requirements.

## Fresh checkout on Windows

From the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m playwright install chromium
Copy-Item .env.example .env
```

If PowerShell blocks activating the environment, invoke `.\.venv\Scripts\python.exe` directly instead of changing machine execution policy. Put the Mistral key only in local `.env`/environment. `DATABASE_URL` and `EVIDENCE_DIR` are relative to process working directory by default, so start all services from the repository root.

Install frontend packages from the lockfile:

```powershell
Push-Location frontend
npm ci
Pop-Location
```

Seed a new empty SQLite database:

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_database
```

The script creates missing tables and adds 600 synthetic members (1000–1599) plus related banking rows. It refuses ID collisions. Only use the two reset flags when replacing the explicitly tagged synthetic batch in this demo database:

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_database --members 600 --start-id 1000 --reset-demo-data --confirm-demo-reset
```

## Start three local processes

Use separate shells, current directory at repo root:

```powershell
.\.venv\Scripts\python.exe target-app\app.py
```

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

```powershell
Push-Location frontend
npm run dev
```

Open the console at `http://127.0.0.1:3000`; target app at `http://127.0.0.1:3001`; Swagger UI at `http://127.0.0.1:8000/docs`.

The backend applies versioned additive migrations at startup and requires a 32+ character JWT signing key plus first-admin bootstrap username and password hash when authentication is enabled. Follow [Authentication and recovery](authentication-and-recovery.md) before the first API start. It marks persisted live-browser handoffs as requiring recovery; it does not restore browser cookies or page memory. On Windows, run Uvicorn without `--reload`; a reload supervisor can interfere with Playwright's subprocess/IPC lifecycle. Some environments may block the Playwright driver pipe even when Chromium is installed; see [Troubleshooting](troubleshooting.md).

## Supported Unix-like equivalent

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m playwright install chromium
cp .env.example .env
python -m scripts.seed_database
```

Start the target with `.venv/bin/python target-app/app.py`, backend with `.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`, and frontend from `frontend/` with `npm run dev`.

## First checks and examples

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready | ConvertTo-Json -Depth 5
Invoke-RestMethod http://127.0.0.1:8000/api/v1/capabilities
```

Health readiness confirms a DB query, target-app HTTP response and Chromium installation. It does not probe Mistral or test browser launch rights.

For a deterministic example, submit a `POST /api/v1/workflows/run` request for member 1002 with `force_mode: "REPLAY"`; this runs the saved `member_savings_lookup` artifact without Mistral. For discovery, configure key and submit the same goal with `force_mode: "DISCOVERY"`; this may create a failed recording when provider quota/auth/network fails. Both examples are in the [README](../README.md#run-a-workflow).

## Development conventions and scope

- Keep `.env`, SQLite DBs and runtime evidence untracked; `.gitignore` excludes them.
- Change source behavior only with an accompanying test where feasible; run backend tests and `npm run build`.
- Treat all target banking values as synthetic. Default seed account balances are snapshots, not transaction-ledger calculations.
- Keep ports on local/trusted interfaces. API and evidence routes require authenticated, tenant-scoped principals; the target simulator is a separate local demo service and is not covered by API identity controls.
- There is no formal formatter/linter configuration, contribution guide, release automation, license file, or CI workflow in this checkout.

The standalone `scripts/run_replay.py` reads the savings artifact JSON mirror directly and does not fall back to SQLite. It was verified in this workspace (member 1002 `SUCCESS`, member 99999 `BUSINESS_OUTCOME`, LLM client patched to forbid calls). If the mirror is absent, use the API/console or run successful discovery first. `scripts/run_final_demo.py` changes simulator flags and creates runs, so it is not a read-only diagnostic command.
