# Configuration

Backend settings are defined in `backend/app/core/config.py` using Pydantic Settings. The service reads `.env` from its current working directory and then process environment variables. `.env.example` documents only a subset of available settings. Keep the working directory consistent because relative DB/evidence paths resolve from it.

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `TARGET_APP_URL` | `http://localhost:3001` | Target simulator homepage used by browser surfaces. |
| `MISTRAL_API_KEY` | `your_mistral_api_key_here` | Mistral credential, required for new discovery only. Use `MISTRAL_API_KEY=your_mistral_api_key` as a placeholder in examples, never a real committed key. |
| `MISTRAL_MODEL` | `mistral-small-latest` | Model name sent in chat-completions payload. |
| `DATABASE_URL` | `sqlite+aiosqlite:///./orchestration.db` | SQLAlchemy async database URL. SQLite is the installed local path. PostgreSQL needs a driver/deployment setup not present in current project dependencies. |
| `BACKEND_HOST` | `127.0.0.1` | Host used by the Python module entry point. |
| `BACKEND_PORT` | `8000` | Port used by the Python module entry point. A command-line Uvicorn `--host/--port` overrides it. |
| `MAX_CONCURRENT_RUNS` | `2` | In-process semaphore limit. |
| `DISCOVERY_MAX_STEPS` | `15` | Agent decision/action-loop step limit. |
| `MAX_LLM_REQUESTS_PER_RUN` | `15` | Agent decision-request cap. Provider retries inside one client call are separate HTTP attempts. |
| `MAX_DISCOVERY_DURATION_SECONDS` | `180` | Outer discovery workflow timeout. |
| `DISCOVERY_OBSERVATION_MAX_ELEMENTS` | `60` | Maximum interactive elements passed through bounded discovery observation. |
| `MISTRAL_TIMEOUT_SECONDS` | `30.0` | Per-request HTTP timeout, with connect timeout capped at 10 seconds. |
| `MISTRAL_MAX_RETRIES` | `1` | Retry count; effective value is clamped from 0 to 3. Applies to 429/5xx and HTTP timeout/transport errors. |
| `MISTRAL_RETRY_BACKOFF_SECONDS` | `1.0` | Base exponential delay for retries without provider retry timing. |
| `MISTRAL_RETRY_MAX_BACKOFF_SECONDS` | `5.0` | Maximum delay, including capped `Retry-After`. |
| `MISTRAL_MAX_RESPONSE_BYTES` | `16384` | Reject responses whose downloaded content exceeds this threshold. `max_tokens` in the request is also 512. |
| `SAFETY_ALLOWED_DOMAINS` | `localhost,127.0.0.1` | Comma-separated policy host list. Current policy also hard-codes local host acceptance; see security caveats. |
| `SAFETY_ALLOW_RISKY_ACTIONS` | `false` | Declared setting, but **not wired into `default_safety_policy`**: the policy is constructed with its own `allow_risky_actions=False` default. Setting this environment variable currently does not enable risky actions. Do not rely on it as a control; keep risky actions blocked. |
| `EVIDENCE_DIR` | `./evidence` | Filesystem root for screenshots, trace/replay JSON and artifact mirrors; also statically mounted at `/evidence`. |

Frontend Vite setting:

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_PROXY_TARGET` | `http://localhost:8000` | Vite dev-server proxy target for `/api` and `/evidence`; load from frontend Vite environment. |

Target app listens on `127.0.0.1:3001` in its direct script entry point. Vite's dev server listens on 3000 from `vite.config.ts`. These ports are not shared backend settings.

## Mistral configuration and verification

Set a real key only in the local `.env` or a secret environment variable; do not print, screenshot, commit or paste the key into an issue. Example template:

```dotenv
MISTRAL_API_KEY=your_mistral_api_key
MISTRAL_MODEL=mistral-small-latest
```

`GET /api/v1/health/ready` says whether the key is nonempty/non-placeholder, but does not make a Mistral request. A controlled `force_mode=DISCOVERY` workflow is the end-to-end connectivity/authorization check. It can incur provider usage and may return rate-limit/auth errors; use synthetic page data and inspect the failed recording. Missing key is classified `MISTRAL_CONFIGURATION_REQUIRED`. Authentication errors are not automatically retried. Existing capability replay does not invoke the Mistral client.

## Database and migration behavior

SQLite foreign-key enforcement is enabled on application-created SQLite connections. App startup runs SQLAlchemy `Base.metadata.create_all`, which creates absent tables; it does not alter an existing table to match new model columns. `backend/migrations/0002_workflow_idempotency.sql` exists as additive SQL but there is no migration command/runner. Do not infer automatic schema upgrades from the file's presence.

## Secret handling

`.env` is ignored and `.env.example` is tracked with a placeholder. Health responses reveal only whether the value appears configured plus the model name. Do not put a real credential in shell transcripts, README, screenshots, JSON artifacts or issue output. See [Safety and security](safety-and-security.md).
