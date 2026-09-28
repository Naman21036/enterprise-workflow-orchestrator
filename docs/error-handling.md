# Error handling and outcomes

## Outcome categories

`execution_outcome_category(status, error_code)` maps stored status and selected codes to `SUCCESS`, `BUSINESS_OUTCOME`, `BLOCKED`, `RUNNING`, `RECOVERABLE_ERROR`, or `HARD_FAILURE`. The function classifies results; it does not schedule retries.

| Category | Meaning |
|---|---|
| `SUCCESS` | Configured completion/verification path passed. |
| `BUSINESS_OUTCOME` | Request executed and returned a domain result such as no member or ambiguous account. |
| `BLOCKED` | Human intervention is required. |
| `RUNNING` | Work is not terminal yet. |
| `RECOVERABLE_ERROR` | Error code is in the code allowlist (provider 429/5xx, timeout/transport, discovery timeout, selected browser/navigation/locator failures). A caller may retry after checking state; no whole-run auto retry occurs. |
| `HARD_FAILURE` | Other failures by default, including policy, invalid action/input, most permission errors, internal errors and current `RUN_CANCELLED` mapping. |

## Common codes

| Code(s) | Detection and meaning | Retry behavior | Resume/evidence behavior |
|---|---|---|---|
| `MEMBER_NOT_FOUND`, `AMBIGUOUS_SAVINGS_ACCOUNT`, `SAVINGS_ACCOUNT_NOT_FOUND` | Replay matches known simulator page text; legitimate business state. | Not an infrastructure retry. Correct the member/account request if appropriate. | Run status `BUSINESS_OUTCOME`; replay step and screenshot are saved when the outcome is observed. |
| `MISTRAL_RATE_LIMITED`, `MISTRAL_PROVIDER_UNAVAILABLE` | HTTP 429 or 5xx after bounded per-request retries. | Client retries within cap, honoring capped `Retry-After`; after exhaustion no whole workflow retry. | Discovery run/recording fail; observation, model error code and step failure are committed if callbacks complete. |
| `MISTRAL_TIMEOUT`, `MISTRAL_TRANSPORT_ERROR` | `httpx` timeout/transport exceptions after configured retry allowance. | Bounded client retry; manual resubmission can create a new run. | Provider decision failure event and recording checkpoint, if persistence callback is available. |
| `MISTRAL_AUTHENTICATION_FAILED`, `MISTRAL_REQUEST_FAILED`, `MISTRAL_CONFIGURATION_REQUIRED` | HTTP 401/403, other non-retryable HTTP error, or missing/placeholder key. | No retry for auth/config errors. | Failed discovery; configured health state does not prove key validity. |
| `MISTRAL_MALFORMED_RESPONSE`, `MISTRAL_SCHEMA_INVALID`, `MISTRAL_RESPONSE_TOO_LARGE` | Provider response is absent/non-JSON, does not fit requested `AgentAction`, or exceeds client response-size check. | No retry currently after a 200 response. | Failed model decision and recording event; no browser action is counted. |
| `LLM_REQUEST_LIMIT`, `MAX_STEPS_REACHED` | Agent reaches configured model-decision or loop bound without verified completion. | Not automatically retried. | Failed recording with reason and the trace/events produced so far. |
| `DISCOVERY_ACTION_FAILED`, `UNSUPPORTED_ACTION`, `GOAL_NOT_VERIFIED` | Action could not execute, unsupported action, or required live UI result was not confirmed. | No automatic retry. UI-specific rediscovery may be required. | Failed trace includes decision/action result; only successful UI actions count. |
| `SAFETY_VIOLATION`, `SAFETY_POLICY_BLOCKED` | URL/action policy rejected a proposed or saved step. | Do not retry unchanged; review target and policy. | Discovery step is recorded as rejected; replay writes a failed step. |
| `HUMAN_INTERVENTION_REQUIRED`, `HITL_REQUIRED` | Confirmation dialog or explicit model escalation. | Not retried automatically. | Run is `BLOCKED`, handoff row created and the live surface retained in memory. |
| `DISCOVERY_TIMEOUT`, `RUN_CANCELLED` | Discovery exceeds outer time limit or receives cancellation. | Timeout may be resubmitted; cancellation is caller/control decision. | Terminal run/recording state committed where possible. Cancellation category currently maps to `HARD_FAILURE`. |
| `BROWSER_INITIALIZATION_FAILED`, `BROWSER_PERMISSION_DENIED`, `PLAYWRIGHT_DRIVER_PERMISSION_DENIED` | Playwright startup/IPC fails before actions. | Fix environment/process permissions before retry. | No browser action steps; startup error is recorded in run response. |
| `OUTPUT_VALIDATION_FAILED`, `REPLAY_EXECUTION_ERROR`, `PERMISSION_DENIED` | Invalid extracted value, replay exception or OS permission denial. | Some replay errors are categorized recoverable only if code allowlisted; no automatic replay retry. | Replay step screenshots/logs are available only if startup/action reaches capture. |
| `IDEMPOTENCY_KEY_INVALID`, `IDEMPOTENCY_KEY_CONFLICT` | Invalid visible-ASCII key or key reused with a different request fingerprint. | Correct key/request; conflict returns HTTP 409. | Invalid key is rejected before run creation. Conflict returns existing association/error; no second run starts. |
| `GOAL_REQUIRED`, `MEMBER_ID_REQUIRED`, `UNSUPPORTED_TARGET`, `INVALID_EXECUTION_MODE` | Router precondition failures. Request model can also return HTTP 422 for invalid body fields. | Correct request. | Some router validations return a `FAILED` result without creating a run row. |

Other generic errors include `DISCOVERY_EXECUTION_ERROR`, `REPLAY_FAILED`, `CAPABILITY_NOT_FOUND`, and errors raised by persistence/compiler. Exact returned strings may vary by the failing layer. Inspect the `error_code`, `status`, `outcome_category`, run details and recording detail together.

## Retry layers

- **Mistral client:** retries 429, 5xx, timeout and HTTP transport errors within configured bounds. Authentication, other HTTP errors, malformed 200 responses and schema mismatch are not retried.
- **Browser surface:** individual click/fill waits for a visible element up to five seconds. This is locator waiting, not a workflow retry strategy. Navigation uses `networkidle`; replay does not ask Mistral to repair a broken selector.
- **Workflow/run:** no automatic whole-run retry queue exists. A client may resubmit; use a stable idempotency key if retrying the identical HTTP request.

## API and UI presentation

Workflow endpoint normally returns a JSON result object even for many execution failures (`status: FAILED`); request-model validation yields HTTP 422, missing explicit replay artifact yields 404, idempotency conflict yields 409, and handoff routes use 404/409/422/403/400 for their checks. The UI reads `detail` from API errors and displays run/recording failure fields. Runs and recordings are persisted incrementally where callback/transaction execution succeeds; not every pre-run validation error creates history.

Related: [workflow lifecycle](workflow-lifecycle.md), [troubleshooting](troubleshooting.md).
