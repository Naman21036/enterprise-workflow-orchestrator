# Safety and security

## Security posture

APEX Automation is a local development/demo application with fail-closed simulator-specific action rules, authenticated API routes and tenant-scoped records. It is not a production banking control or a general-purpose browser sandbox. All banking data shipped/seeded by this repository is synthetic. Run only against the local simulator and bind the services to local addresses.

## Current controls

| Control | Current behavior | Limitation |
|---|---|---|
| Target application | Workflow request target is the literal `APEX Federal`; discovery navigates to configured `TARGET_APP_URL`. | The target string is not tenant identity or authentication. |
| URL policy | `SafetyPolicy.validate_url` allows credential-free HTTP/HTTPS URLs on the configured host list and simulator local hostnames, permits ports 80, 443, and 3001 only, and rejects malformed/encoded traversal, ambiguous paths and unapproved route prefixes. Browser context routing checks every top-level request/redirect before it reaches the network. | Local host acceptance is intentional for the simulator; this does not defend against DNS rebinding or certify other deployment origins. |
| Action risk | Discovery, artifact replay, browser surface and handoff paths validate before navigation/click/fill/select/key input. Destructive/submit/financial-like controls and risky keyboard actions fail closed, regardless of `SAFETY_ALLOW_RISKY_ACTIONS`. `member_id` search and ordinary fill/select are permitted as non-submitting inputs. | Classification remains heuristic and does not understand application semantics. Only the exact simulator confirmation selector has a dedicated approval-gated execution method. |
| Prompt handling | Discovery prompt tells model that observed page content is untrusted and prohibits out-of-app navigation/write actions. Returned action has a strict Pydantic schema and is policy checked before execution. | Prompt instruction alone does not neutralize prompt injection. A schema-valid action may still be unsafe if policy misses it. |
| Handoff actions | API limits action types, requires action identity, binds exact simulator confirmation to run/session/operator/action ID, revalidates active route, and atomically claims actions/resumes using database session versions. | Browser pages remain process-local; restart requires deterministic reconstruction. The in-memory login throttle should be replaced by a shared edge limiter in multi-worker deployments. |
| Redaction | Helpers mask common secrets, IDs, account/member fields, currency, contact values and UI text in some persisted/logged structures. | Redaction is pattern/key based and incomplete. Screenshots may contain values; prompt content is sent to Mistral. |
| CORS | FastAPI allows the two local frontend origins. | CORS is browser behavior, not authentication or authorization. |
| Identity and secrets | HS256 JWTs require a 32+ character key, issuer/audience/time checks, PBKDF2 password hashes, HttpOnly SameSite cookies, and active operator-row lookup on each request. `.env` is ignored by Git. | Key storage/rotation and host filesystem/process access remain deployment responsibilities. |

## Assets and threats

| Asset | Threat | Existing mitigation | Residual risk |
|---|---|---|---|
| Mistral API key | Accidental logging/commit or unauthorized use | Read from settings; key not included in structured request logs; `.env` ignored and `.env.example` contains a placeholder | Local `.env` and process access are not protected by this app; rotate a leaked key. |
| Synthetic banking records and workflow outputs | Exposure through API or evidence access | Authenticated routes filter run/evidence data by tenant; simulator masks email, phone and account numbers in its member API; several persistence paths redact values | The target simulator is separately exposed on its local demo port; it is not protected by the orchestration API's identity model. Screenshots may contain UI content. |
| Browser actions | Prompt injection, unsafe click, unintended state change | Local target, schema validation, heuristic policy, prompt instruction against writes | Policy is not a sandbox; misclassification or unsafe selector can still execute. |
| Capability artifacts | Tampered or stale action plan | Pydantic validation, immutable version check, clean replay before publication | No signature/approval gate; filesystem/DB writer can alter artifacts; drift remains possible. |
| Handoff session | Unauthorized operator action by run-ID guessing or backend restart | JWT and role checks, tenant filtering, durable action claims and version checks; restart marks live browser sessions recovery-required | Browser surfaces remain in memory; recovery reconstructs only eligible deterministic actions and does not restore the old session. |
| Evidence files | Unauthorized image/trace access or local disk loss | Authenticated file route checks path containment and owning run tenant | Storage is local and may disappear; screenshots can contain raw UI data. |

## Data-flow and external provider

During discovery, the current URL, title, interactive element attributes/text and a bounded body-text snippet are included in the prompt sent to `https://api.mistral.ai/v1/chat/completions`. The application uses `MISTRAL_API_KEY` for authorization and configured `MISTRAL_MODEL`; prompts and completions are not written to normal provider metadata events, but page data itself is transmitted to the provider. Use only synthetic, non-sensitive content unless an independently approved data-handling policy permits otherwise.

Screenshots are stored under `EVIDENCE_DIR` and served via `/evidence`. The browser UI does not make those endpoints private. Restrict network binding and filesystem access; do not expose the development service to untrusted networks.

## API authorization and isolation

API routes other than health/root use authenticated principals and role checks. Runs, recordings, handoffs and evidence are filtered by tenant; cross-tenant IDs resolve as not found. The target simulator itself is a local demo application and must be protected separately if deployed beyond the workstation. CORS is not an authorization boundary. Evidence is served by an authenticated file route with path containment and run-tenant checks.

## Remaining deployment controls

Before a non-demo deployment, independently verify key management/rotation, shared login throttling, audit retention/integrity, a strict network egress boundary, hostile-page isolation, evidence retention, backup/restore, and security integration testing. Current action policy covers the APEX simulator allowlist; it is not a general computer-use sandbox. Restart recovery rebuilds only action plans whose retry classifications are safe and requires an unchanged artifact fingerprint.

Related: [discovery prompt boundary](discovery-and-recording.md), [error handling](error-handling.md), [human intervention](human-intervention.md).
