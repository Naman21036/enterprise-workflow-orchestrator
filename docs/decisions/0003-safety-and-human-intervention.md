# ADR 0003: Gate actions and preserve blocked browser sessions for operators

- **Status:** Current architectural decision, documented 2026-09-29 (not a claim about original approval date)
- **Scope:** Local simulator action guard and handoff path

## Context

An automation loop should not blindly accept every model action. A simulator confirmation modal may represent a state change that the local automation must not silently accept. At the same time, an operator needs enough live context to inspect or resolve a blocked demonstration run.

## Decision

Check proposed discovery and artifact replay actions against a local URL/action policy before execution. Block keywords classified as risky/irreversible by default. When a known confirmation dialog or explicit escalation blocks a run, persist an `AWAITING_HUMAN` record, retain the Playwright surface in process memory, expose a small set of operator action types, and require a backend page-state check before marking the run complete. Mark persisted sessions lost after a process restart.

## Consequences

- A blocked run can be inspected and acted on in the same process/browser context.
- Resume verifies known member-page or member-not-found state; it is not a general workflow continuation engine.
- Process restart loses browser continuity; persisted evidence does not restore it.
- The URL and risk rules are keyword/host heuristics, not a secure sandbox. The dedicated simulator confirmation selector is a special allowed operator action.
- No authentication or operator RBAC exists. Run IDs and evidence URLs are not protected by a user identity boundary.
- The application is appropriate only for controlled local synthetic data, not real banking operations.

## Implementation evidence

- Policy: `backend/app/safety/policy.py`.
- Handoff API: `backend/app/api/v1/endpoints/handoff.py`.
- In-memory context manager: `backend/app/escalation/manager.py`.
- Restart handling: FastAPI lifespan in `backend/app/main.py`.

## Follow-up

Before any broader deployment, design authenticated authorization, a robust action allowlist, private evidence access, durable session semantics, audit retention and security tests. Do not treat this ADR as evidence those controls already exist.
