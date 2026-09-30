# Authentication, tenancy and deterministic recovery

## Initial operator setup

Authentication is enabled by default. Startup fails when the JWT signing key or first administrator is not configured. Generate a password hash with `python scripts/create_password_hash.py`, then set these values in the ignored local `.env` or a secret manager before starting the API:

```dotenv
APEX_AUTH_ENABLED=true
APEX_JWT_SIGNING_KEY=<random secret with at least 32 bytes>
APEX_BOOTSTRAP_ADMIN_USERNAME=admin
APEX_BOOTSTRAP_ADMIN_FULL_NAME=APEX Administrator
APEX_BOOTSTRAP_ADMIN_EMAIL=admin@example.test
APEX_BOOTSTRAP_ADMIN_PASSWORD_HASH=<PBKDF2 hash from create_password_hash.py>
APEX_JWT_ISSUER=apex-automation
APEX_JWT_AUDIENCE=apex-automation-api
APEX_JWT_TTL_MINUTES=60
APEX_AUTH_COOKIE_SECURE=false
```

Use `APEX_AUTH_COOKIE_SECURE=true` behind HTTPS. The first startup creates the default tenant and its admin only if no operator exists. Later starts do not reset passwords or roles. Keep the signing key stable; rotating it invalidates all issued tokens. `APEX_AUTH_ENABLED=false` is a local-development bypass that grants a synthetic admin identity and must not be used for a shared service.

For a fresh local database, generate the signing key with `python -c "import secrets; print(secrets.token_urlsafe(48))"`, generate the password hash with the script above (it prompts without echoing the password), and copy both outputs into the ignored `.env`. Startup creates the administrator once from these settings; it never overwrites an existing operator. A durable `bootstrap_state` marker also prevents the original bootstrap values from creating a replacement account after the initial operator rows are removed. If the database already contains any operator, changing bootstrap variables does not reset that account. Do not commit or paste the generated key or password hash into source control.

The console uses the `HttpOnly`, `SameSite=Strict` `apex_session` cookie and never stores a bearer token in browser storage. API clients may instead send `Authorization: Bearer <JWT>`. JWTs use HS256 and are checked for signature, issuer, audience, subject, issue/not-before/expiry times, and token ID. The active operator row is reloaded for every request so disabled operators lose access immediately.

## Self-service registration

Users can register without an invitation. Open `/?register=1` in the console and provide a full name, username, email, password, and confirmation. The API assigns every new account the `OPERATOR` role in the active `default` tenant. Tenant ID and role are server-controlled and extra client fields are rejected. The default tenant must already exist and be active; public registration never creates tenants or administrators. Passwords must contain at least 12 characters; usernames and emails are normalized and unique case-insensitively at the database level. Duplicate identity failures share a generic response, and registration attempts are rate limited per API process.

The administrator-only `POST /api/v1/auth/invitations` endpoint remains available for invitation management, but registration does not consume or require those invitations.

```http
POST /api/v1/auth/invitations
Authorization: Bearer <admin JWT>
Content-Type: application/json

{"email":"operator@example.test","role":"OPERATOR","expires_in_minutes":1440}
```

Successful registration returns the user to sign-in. Sign in with either username or email and the password; the API sets the HttpOnly session cookie. Page refresh restores the session via `/api/v1/auth/me`. The registration endpoint returns the created operator's non-secret identity only; the user must then sign in.

## Roles and scope

| Role | Access |
|---|---|
| `ADMIN` | All run, recording, capability, safety, evidence and handoff operations; manage operators in its tenant. A default-tenant admin can provision a tenant and its first tenant admin. |
| `OPERATOR` | Create and resume runs, inspect tenant data, and act on handoffs. |
| `VIEWER` | Read tenant runs, recordings, capability metadata, policy, and evidence. |

Run, recording, handoff and evidence queries are tenant-filtered. Unknown and cross-tenant object IDs return not found. Capabilities are globally published workflow definitions and are read-only to operators. Tenant provisioning is available at `POST /api/v1/auth/tenants`; it creates the new tenant and its first admin in one transaction. Operator create/update/disable endpoints are under `/api/v1/auth/operators`; invitation creation is `POST /api/v1/auth/invitations` and requires an administrator from the invited tenant.

Login failures are rate limited per API process and logged without the submitted password. The rate limiter is in-memory, so production deployments with multiple workers should place a shared rate limiter at the edge. Authentication events and admin changes are also written to `audit_events`; audit rows are append-only by application convention, not protected against a database administrator.

## Handoff state and restart behavior

Each deterministic run has a durable checkpoint containing a content hash of the published artifact, stable action IDs, completed/pending action IDs, action status, selector/action metadata, retry classification, required input names, and a sanitized surface route class. It never stores submitted input values, page text, cookies, or browser storage. Per-action checkpoint writes happen before and after each action. The schema migration is `0003_handoff_security`; application startup runs the additive migration runner and records versions in `schema_migrations`.

Handoff session states are `AWAITING_OPERATOR`, `ACTION_IN_PROGRESS`, `RESUMING`, `RECOVERY_REQUIRED`, `EXPIRED`, `COMPLETED`, and `FAILED`. Session updates use tenant/run identity and version compare-and-swap; a second worker cannot claim an action or resume already claimed by another worker. Session expiry is enforced by action/resume endpoints. A restart marks live-process sessions `RECOVERY_REQUIRED`; it does not pretend the in-memory browser is still available.

When resuming a live session, the API verifies the artifact fingerprint and route policy, then continues from the action after the operator-resolved checkpoint. When the browser process was lost, the UI asks for the required inputs again and runs the plan deterministically from the configured target root. Automatic reconstruction is denied if any recorded action is not classified `SAFE_TO_RETRY_AFTER_STATE_RECONSTRUCTION`, if the artifact hash changed, or if inputs do not match the checkpoint. No LLM is involved in replay or recovery. Recovery is not a browser-session restore: cookies, page memory and unpersisted page state do not survive a process restart.

Discovery interruptions retain the live handoff, operator action history, and a redacted checkpoint, but they do not have a published deterministic artifact to continue. The UI disables resume for these runs; an operator can handle the active simulator dialog and then cancel the paused discovery before starting a new run. After a process restart, discovery browser state cannot be restored.

Only the exact active simulator confirmation control can pass the high-risk approval path. The API records the typed safety decision and operator identity, and binds the decision to the specific action ID, run and active handoff session. Unknown selectors, routes and action types remain denied.

## Audit and telemetry

`audit_events` records authentication, operator/tenant changes, safety decisions, operator action lifecycle, handoff recovery and resume outcomes. Payloads are passed through the common redactor. Audit events carry a correlation ID. OpenTelemetry emits workflow, handoff resume duration, authentication event, escalation and safety rejection metrics when `APEX_OTEL_ENABLED=true`; use standard OTLP environment variables to export to a collector. Do not add usernames, member values, raw inputs, selectors containing data, or page text as metric attributes.

## Migration and rollback notes

The migration runner adds `tenant_id`, `owner_id`, and `action_id` to legacy SQLite tables where missing, creates the tenant/operator/audit/checkpoint/handoff tables, seeds the `default` tenant, and records the migration version. Migration `0004_auth_registration` adds nullable full-name/email fields for existing operators, case-insensitive unique indexes, the one-time bootstrap marker, and the invitation table. Existing operator and workflow rows are retained; old operators can continue to sign in by username and may have no email until updated. Back up the database before upgrading. Existing runs are assigned to tenant `default`. Schema changes are additive; this application does not provide a downgrade command. Rollback should restore the pre-upgrade database backup and previous application version.

Related: [Safety and security](safety-and-security.md), [Human intervention](human-intervention.md), [Configuration](configuration.md).
