# Human intervention

## Triggers

Both discovery and replay inspect observed `page_text_summary` for the simulator's `unexpected-dialog-modal` marker or the text `Confirm Action`. Discovery can also receive an explicit `escalate` action from Mistral. The router marks the run `BLOCKED`, creates an `HandoffRecordModel` in `AWAITING_HUMAN`, stores a screenshot path when available and retains the active surface in `SessionManager` memory.

## Operator workflow

1. The console lists tenant-owned runs in `BLOCKED` state and loads the durable handoff session and checkpoint metadata.
2. `GET /api/v1/runs/{run_id}/handoff` returns the handoff state, checkpoint/session versions, required input names, expiry, and whether this process still has the live browser.
3. `POST /api/v1/runs/{run_id}/handoff/action` accepts bounded click, type, key, and screenshot requests with an action ID. A database compare-and-swap moves the session to `ACTION_IN_PROGRESS` before browser input; repeated action IDs return the recorded result and competing worker requests cannot claim the same session action. The exact active simulator confirmation receives a typed, action-bound approval decision; other unknown actions fail closed.
4. `POST /api/v1/runs/{run_id}/resume` compares the supplied session version, claims the session with a database version check, verifies the artifact fingerprint, and continues the deterministic plan after the resolved action. The API revalidates the route and checks the artifact's success condition before reporting a terminal result.
5. Completed outcomes update run, checkpoint, and session state and close the browser surface. If another unexpected confirmation appears, the run returns to `BLOCKED` with the new current step.

## Session lifetime and restart recovery

Browser pages remain in a process-local dictionary. Handoff metadata and execution checkpoints are durable in the database. At startup, an unexpired waiting or interrupted session becomes `RECOVERY_REQUIRED`; an expired session becomes `EXPIRED`. This distinction does not imply that browser cookies/page memory can be restored.

For `RECOVERY_REQUIRED`, the operator re-enters required input values, which are never stored in checkpoint rows. The API checks the saved artifact fingerprint and verifies every action in the plan is marked `SAFE_TO_RETRY_AFTER_STATE_RECONSTRUCTION`, then deterministically replays the plan from the configured target root. Unsafe/in-progress high-risk plans are denied automatic recovery. This path makes no LLM calls. Session expiry is checked at each action and resume request.

## Auditing and security limits

Authentication/login results, tenant/operator management, safety decisions, action start/finish, restart recovery, and resume outcomes are recorded in `audit_events`, with a correlation ID and redacted payload. Checkpoint rows retain stable action IDs and sanitized state metadata, not raw page text, screenshots, tokens, or input values. OpenTelemetry can export resume outcome/duration, auth event, escalation and safety rejection metrics.

The per-run process lock is complemented by database session-version compare-and-swap for multi-worker action/resume claims. The live browser itself is not shared between workers; a request handled by a worker without that process-local surface returns a recovery-required conflict. Login throttling is process-local and should be moved to a shared edge limiter for a multi-worker public deployment.

See [the sequence diagram in Architecture](architecture.md#operator-handoff-and-resume) and [Safety](safety-and-security.md).
