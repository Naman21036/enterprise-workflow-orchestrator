# Safety and security

## Security posture

APEX Automation is a local development/demo application with a heuristic action guardrail. It is not a production banking control, secure tenant boundary, or authenticated service. All banking data shipped/seeded by this repository is synthetic. Run only against the local simulator and bind the services to local addresses.

## Current controls

| Control | Current behavior | Limitation |
|---|---|---|
| Target application | Workflow request target is the literal `APEX Federal`; discovery navigates to configured `TARGET_APP_URL`. | The target string is not tenant identity or authentication. |
| URL policy | `SafetyPolicy.validate_url` allows HTTP/HTTPS without URL credentials, checks configured domains and allows `/` or configured routes (default `/`, `/member/`). | The implementation always accepts `localhost` and `127.0.0.*` branches regardless of custom list, does not constrain port, and does not defend against DNS rebinding. Validate actual deployment origin yourself. |
| Action risk | Keyword heuristics inspect selector/value for terms such as delete, transfer, wire, submit, confirm, save and close. Risky/irreversible actions are blocked by default. The policy response type includes `REVERSIBLE`, but the current classifier does not assign that level. | This is string matching, not semantic understanding or complete action classification. DOM labels/selector names can evade or falsely trigger it. `SAFETY_ALLOW_RISKY_ACTIONS` is declared but not passed to the default policy object, so changing it currently has no effect. |
| Prompt handling | Discovery prompt tells model that observed page content is untrusted and prohibits out-of-app navigation/write actions. Returned action has a strict Pydantic schema and is policy checked before execution. | Prompt instruction alone does not neutralize prompt injection. A schema-valid action may still be unsafe if policy misses it. |
| Handoff actions | API limits action types, selector/text lengths and keyboard keys. Clicks are checked except the dedicated `#confirm-dialog-btn` path for a blocked simulator dialog. | No operator authentication/roles exist; session identity is by run ID. |
| Redaction | Helpers mask common secrets, IDs, account/member fields, currency, contact values and UI text in some persisted/logged structures. | Redaction is pattern/key based and incomplete. Screenshots may contain values; prompt content is sent to Mistral. |
| CORS | FastAPI allows the two local frontend origins. | CORS is browser behavior, not authentication or authorization. |
| Secrets | `.env` is ignored by Git; health returns configured/not configured, not the key. | Host environment, local filesystem and process logs remain sensitive. Do not commit credentials. |

## Assets and threats

| Asset | Threat | Existing mitigation | Residual risk |
|---|---|---|---|
| Mistral API key | Accidental logging/commit or unauthorized use | Read from settings; key not included in structured request logs; `.env` ignored and `.env.example` contains a placeholder | Local `.env` and process access are not protected by this app; rotate a leaked key. |
| Synthetic banking records and workflow outputs | Exposure through API or static file access | Simulator masks email, phone and account numbers in its member API; several backend persistence paths redact values | Service routes have no auth; member API also returns addresses, balances and recent synthetic transactions. Static evidence may contain UI content. |
| Browser actions | Prompt injection, unsafe click, unintended state change | Local target, schema validation, heuristic policy, prompt instruction against writes | Policy is not a sandbox; misclassification or unsafe selector can still execute. |
| Capability artifacts | Tampered or stale action plan | Pydantic validation, immutable version check, clean replay before publication | No signature/approval gate; filesystem/DB writer can alter artifacts; drift remains possible. |
| Handoff session | Unauthorized operator action by run-ID guessing or backend restart | In-memory surface lookup and action parameter validation; restart marks persisted waits as lost | No authentication/RBAC; same-process run IDs are bearer-like identifiers; session durability is absent. |
| Evidence files | Unauthorized image/trace access or local disk loss | Static server only mounts configured evidence directory; path availability is checked | `/evidence` has no per-user authorization; storage is local and may disappear. |

## Data-flow and external provider

During discovery, the current URL, title, interactive element attributes/text and a bounded body-text snippet are included in the prompt sent to `https://api.mistral.ai/v1/chat/completions`. The application uses `MISTRAL_API_KEY` for authorization and configured `MISTRAL_MODEL`; prompts and completions are not written to normal provider metadata events, but page data itself is transmitted to the provider. Use only synthetic, non-sensitive content unless an independently approved data-handling policy permits otherwise.

Screenshots are stored under `EVIDENCE_DIR` and served via `/evidence`. The browser UI does not make those endpoints private. Restrict network binding and filesystem access; do not expose the development service to untrusted networks.

## API authorization and isolation

The backend includes no authentication, authorization middleware, tenant IDs, or per-tenant query filters. The target simulator and simulator-control endpoint also lack authentication. CORS settings do not prevent direct HTTP clients from calling routes. The local-only bind and synthetic data are deployment assumptions, not enforced production controls.

## Production hardening required

Before any non-demo deployment, design and verify: authenticated identity and role checks; tenant/data isolation; private evidence storage with authorization; a strict origin/port/egress policy; non-bypassable action allowlists and explicit approval for any writes; defense against hostile page content; secret management and rotation; audit-log integrity/retention; artifact signing/review; rate limiting; CSRF/network protection where applicable; and security-focused integration testing. This repository does not provide those controls.

Related: [discovery prompt boundary](discovery-and-recording.md), [error handling](error-handling.md), [human intervention](human-intervention.md).
