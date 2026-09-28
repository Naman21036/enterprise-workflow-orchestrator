# API reference

The authoritative live schema is FastAPI's `/openapi.json` and interactive `/docs`. All paths below are served by `backend/app/main.py` with prefix `/api/v1`, except `/`, target-simulator paths and `/evidence`.

## Authentication and content type

No API endpoint implements authentication or authorization. JSON POST endpoints use `Content-Type: application/json`. Keep these services on local/trusted interfaces. The browser's same-origin development proxy routes `/api` and `/evidence` to the backend.

## Workflow endpoints

### `POST /api/v1/workflows/run`

Starts a router-selected workflow or forces `DISCOVERY`/`REPLAY`. State-changing: creates run, steps and optionally a discovery recording; discovery can create/publish a capability. Optional header `Idempotency-Key`: 8–255 visible ASCII characters.

```json
{
  "goal": "Find member 1002 and retrieve their savings balance.",
  "target_app": "APEX Federal",
  "input_parameters": {"member_id": "1002"},
  "force_mode": "REPLAY"
}
```

`goal` is 1–1000 characters and cannot be blank. `target_app` is the literal `APEX Federal`. `input_parameters` is a JSON object with up to 32 keys; if present, `member_id` must be four or five digits. `force_mode` is optional `DISCOVERY` or `REPLAY`. The router also requires a member ID in its supported 4–5 digit format, so unrelated goals currently fail preconditions.

Response fields vary by branch; normally include `run_id`, `goal`, `execution_mode`, `status`, `outcome_category`, duration, `outputs`, `step_logs`, `error_code`, `error`, and `llm_decision_calls`. A duplicate idempotency request can include `duplicate_request: true` and returns the prior run summary. Idempotency key reused with a different request returns HTTP 409 with code `IDEMPOTENCY_KEY_CONFLICT`. Request schema errors return 422. Many workflow execution/precondition failures are JSON status results rather than non-2xx HTTP responses.

PowerShell example:

```powershell
$body = @{
  goal = "Find member 1002 and retrieve their savings balance."
  target_app = "APEX Federal"
  input_parameters = @{ member_id = "1002" }
  force_mode = "REPLAY"
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/workflows/run `
  -Method Post -ContentType "application/json" -Body $body
```

### `POST /api/v1/workflows/replay`

Runs an explicitly named saved capability/version with `parameters`. State-changing: creates a run and replay step/evidence rows. Header `Idempotency-Key` behaves as above.

```json
{
  "capability_id": "member_savings_lookup",
  "version": "1.0.0",
  "parameters": {"member_id": "1002"}
}
```

`capability_id` accepts 1–80 letters/digits/underscore/hyphen; version is `major.minor.patch`; parameter map has 1–32 entries and validates a supplied member ID. Missing artifact returns 404, request errors 422, idempotency conflict 409. Successful/failed execution fields follow the run endpoint. This path does not require Mistral.

## Run history

### `GET /api/v1/runs?limit=20`

Returns recent run summaries ordered newest first. Read-only. `limit` defaults to 20; endpoint does not currently clamp a maximum. Fields include run ID, sanitized goal, target, execution mode, status/category, capability/version, timestamps, duration, error code/message and stored result summary.

### `GET /api/v1/runs/{run_id}`

Returns a run plus ordered step data, recording ID when present and linked replay run IDs. Read-only. Step fields include number, action, target, status, duration, error, screenshot path, `evidence_available`, and stored observation JSON. Returns 404 when the run does not exist.

## Capability registry

### `GET /api/v1/capabilities`

Lists DB capabilities and supported artifact-file fallbacks. Read-only; returns ID, name, description, target, active flag, latest version and created time.

### `GET /api/v1/capabilities/{capability_id}?version=1.0.0`

Returns the Pydantic artifact JSON for the requested version or latest version when omitted. Read-only. Returns 404 if absent. See [Capability artifacts](capability-artifacts.md).

Example:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/capabilities/member_savings_lookup?version=1.0.0"
```

## Recording and evidence metadata

### `GET /api/v1/recordings?limit=50`

Returns recent discovery recording summaries, clamping limit to 1–100. Read-only. Includes status, timestamps, successful action count, action summary, checkpoints, failure classification and artifact link.

### `GET /api/v1/recordings/{recording_id}`

Returns recording summary plus ordered events, event ID/run ID/sequence, sanitized payload, evidence path/availability, and linked replay run status. Read-only. Returns 404 if absent.

### `GET /evidence/{path}`

Static file response from `EVIDENCE_DIR`. Read-only, but **not authenticated**. Restrict network access; path existence does not guarantee access is authorized.

## Human intervention

### `GET /api/v1/runs/{run_id}/handoff`

Returns latest persisted handoff or live in-process handoff state: status, reason, step, screenshot path, operator action summary and `session_active`. Returns 404 when neither exists. Read-only.

### `POST /api/v1/runs/{run_id}/handoff/action`

State-changing operator action in a preserved browser session. Request:

```json
{"action_type":"click","params":{"selector":"#confirm-dialog-btn"}}
```

Allowed kinds are `click`, `type`, `press_key`, `take_screenshot`; params are a small object. Selector is limited to 200 chars, text to 1000 chars, and keyboard key to Enter/Escape/Tab/ArrowDown/ArrowUp. Type requires a selector. HTTP errors include 404/409 for absent/non-active handoff, 422 validation failure, 403 policy denial and 400 action failure. Click on the dedicated simulator confirm selector is a special-case allowed action.

### `POST /api/v1/runs/{run_id}/resume`

State-changing final verification against the same in-memory page. Returns final `SUCCESS` with available member outputs or `BUSINESS_OUTCOME` for known member-not-found state. Expired session returns 400; unsatisfied checkpoint returns 409 and leaves the live session open.

See [Human intervention](human-intervention.md) for lifecycle limits.

## Health and policy

| Method/path | Purpose and response |
|---|---|
| `GET /` | Service name and links to docs/health. |
| `GET /api/v1/health/live` | Process liveness only. |
| `GET /api/v1/health/ready` | DB `SELECT 1`, target HTTP reachability, Chromium installation, Mistral configuration indicator. It does not probe browser launch or provider API. |
| `GET /api/v1/health` | Same readiness details with system/version/status fields. |
| `GET /api/v1/safety/policy` | Current allowlist/risk-level/redaction description. It does not enforce a user authorization decision. |

All are read-only. Health returns configured state, not credential validity.

## Target simulator API (separate FastAPI process)

These endpoints are not part of orchestration `/api/v1` OpenAPI:

- `GET /api/members/{member_id}`: read a synthetic member and related sample data; masks email, phone and account number; 404 for missing member, 422 for non-digit identifier.
- `GET /api/simulators`: read simulator flags.
- `POST /api/simulators`: mutate simulator `delay` and/or `dialog` flags. There is no auth; use only for local demos.

`GET /` renders the synthetic banking UI and `GET /member/{member_id}` renders member details.

Related: [Configuration](configuration.md), [Evidence](evidence-and-observability.md), [OpenAPI](http://127.0.0.1:8000/docs).
