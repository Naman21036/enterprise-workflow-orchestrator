# Human intervention

## Triggers

Both discovery and replay inspect observed `page_text_summary` for the simulator's `unexpected-dialog-modal` marker or the text `Confirm Action`. Discovery can also receive an explicit `escalate` action from Mistral. The router marks the run `BLOCKED`, creates an `HandoffRecordModel` in `AWAITING_HUMAN`, stores a screenshot path when available and retains the active surface in `SessionManager` memory.

## Operator workflow

1. The console's Human intervention view lists runs whose run status is `BLOCKED` or `AWAITING_HUMAN`, or opens a selected run ID.
2. `GET /api/v1/runs/{run_id}/handoff` returns the persisted handoff plus whether the process still has a live surface.
3. `POST /api/v1/runs/{run_id}/handoff/action` accepts `click`, `type`, `press_key`, and `take_screenshot`. Request fields are bounded. The console currently exposes click-selector, Enter, and screenshot buttons; it does not expose the API's type action.
4. `POST /api/v1/runs/{run_id}/resume` checks the **same current browser page** for `#member-name-val`. If found, it returns member name and available balance/ID. If member-not-found text is observed, it returns a business outcome. Otherwise the request returns 409 and keeps the session active.
5. On a verified final result, run and handoff rows are updated and session resources close.

Resume is a terminal-state verification step. It does not rewind, rerun the discovery loop or continue an arbitrary persisted action list.

## Session lifetime and abandonment

Browser pages are held in an in-memory dictionary keyed by run ID. There is no distributed session store or lease/expiration worker. Explicit close occurs after successful resume; normal process shutdown/runtime cleanup closes process-owned resources. At the next backend startup, persisted `AWAITING_HUMAN` records are marked `SESSION_LOST`, with corresponding blocked runs failed as `SESSION_LOST`.

An operator action request after session loss fails because no live browser session exists. The UI disables actions/resume when the API reports `session_active=false`. Existing screenshots and DB handoff history may remain, but they do not restore the context.

## Auditing and security limits

Successful operator actions are appended to in-memory handoff state and the handoff record JSON. Common sensitive `text`/member/account fields are redacted in persisted action parameters. Errors can still carry diagnostic strings, and the API has no authentication or operator-role enforcement. Treat run IDs and the local endpoint as sensitive, keep the service private, and use only synthetic data.

See [the sequence diagram in Architecture](architecture.md#operator-handoff-and-resume) and [Safety](safety-and-security.md).
