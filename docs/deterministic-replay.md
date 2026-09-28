# Deterministic replay

## Contract

Replay executes an existing `CapabilityArtifact`. It does not ask Mistral or an LLM to decide the next action. Its fixed action plan is deterministic with respect to the artifact and supplied inputs, but browser success can still vary with application data/state, UI markup, timing, browser/runtime behavior and network availability.

| | Discovery | Replay |
|---|---|---|
| LLM decisions | Allowed, one structured action request at a time | Prohibited in the normal execution path |
| New UI exploration | Yes, within the supported observation/action loop | No; artifact defines the sequence |
| Existing artifact | Not required | Required |
| Parameters | Discovery may identify the member ID | Validated against artifact declarations, with member-ID digit check |
| Actions | Chosen by model, policy-checked, then executed | Prescribed by stored artifact, policy-checked, then executed |
| Verification | `complete` checks the known goal fields | Step checkpoints, known business outcomes, declared outputs and final condition |
| Publication | Possible only after successful compile and replay validation | Does not publish or modify artifact |

## Execution stages

1. Router loads the selected capability/version and validates it for publication. Explicit replay requests identify a capability ID and semantic version. For an ordinary goal, lookup currently selects the latest compatible artifact by exact capability ID and target app.
2. Replay checks for missing/unknown inputs; values declared as strings must be strings, and `member_id` must contain digits. The artifact is schema-validated again.
3. A fresh `PlaywrightWebSurface` connects to the configured target homepage. Startup errors are classified before any action step.
4. For each ordered step, the engine observes the current page, detects supported blocking dialogs, resolves a locator from primary/fallback/text strategies, and checks the safety policy before executing the action.
5. Supported actions are `navigate`, `fill`, `select`, `click`, `extract`, and `assert` (the schema allows `assert`, but the engine has no standalone assertion branch). Step checkpoints currently evaluate URL fragments.
6. After an action, replay captures a screenshot best effort, observes the page for member-not-found, ambiguous savings and no-active-savings conditions, then evaluates step checkpoints.
7. At the end, it extracts declared output selectors, validates currency format/member ID where configured, and checks the artifact-level terminal condition.
8. The router persists run and step details. When saved as a compiled discovery artifact, a successful clean replay is required before publication.

## Locator and timing behavior

The surface tries locators in order and chooses the first existing visible match. A click/fill waits up to five seconds for a visible locator. Navigation waits for `networkidle`; the surface adds a 0.5-second delay after clicks, and replay adds a 0.3-second pause between steps. These are fixed waits, not a general retry policy. There is no automatic application-drift repair or model-assisted fallback.

Screenshot capture failure is logged and stored as an evidence error where available; it does not by itself fail the browser action. A selector/action failure or failed checkpoint returns failure. A missing screenshot file is surfaced as unavailable by evidence APIs/UI.

## Business outcomes

The engine recognizes several known target-page messages and returns `BUSINESS_OUTCOME` rather than infrastructure failure:

- `MEMBER_NOT_FOUND` when the page text says the member does not exist;
- `AMBIGUOUS_SAVINGS_ACCOUNT` when multiple active savings accounts are present;
- `SAVINGS_ACCOUNT_NOT_FOUND` when no active savings account is linked.

These checks are text-dependent and specific to the local simulator. A production integration would need an explicit domain result contract, not brittle text matching.

## Failure when the application changes

An absent or changed locator, a route transition mismatch, invalid output, or an unsatisfied success condition fails the run. Replay records the failed step or final error and screenshots when capture succeeds. It does not silently call Mistral to repair the artifact. An operator can inspect the run/evidence, then a developer can review the artifact and update or rediscover the capability as a distinct workflow.

The backend browser integration test verifies one successful local replay with an LLM client that throws if invoked. Browser startup can fail before that assertion path reaches any page action; on Windows, Playwright's child-process IPC may require additional process permissions.

Related: [error taxonomy](error-handling.md), [capability artifacts](capability-artifacts.md).
