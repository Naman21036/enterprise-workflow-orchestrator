# Discovery and recording

## Terms

These objects have different lifetimes and meanings:

1. **Discovery run:** one execution of the Mistral-guided agent against the local web app. It is represented by a run row with `execution_mode="Discovery"`.
2. **Discovery trace:** the in-memory ordered `DiscoveryStepResult` values returned by the agent. On a successful agent loop, a sanitized JSON trace is written under `evidence/discovery/{run_id}/discovery_trace.json`.
3. **Persisted recording:** a database `DiscoveryRecordingModel` associated with one run. It carries lifecycle status, successful action JSON, checkpoints, artifact linkage, linked replay IDs, and ordered `RecordingEventModel` rows.
4. **Capability artifact:** a separately validated, versioned plan compiled from successful executed trace actions. A recording can fail or be blocked and never produce an artifact.
5. **Replay execution:** a later execution of a stored artifact by the replay engine, with its own run and step rows. It is not another discovery recording.

## Observation and prompt construction

`PlaywrightWebSurface.observe()` reads current URL/title, interactive DOM elements, and up to 800 characters of body text. It selects visible buttons, inputs, selects, textareas, links, elements with roles/IDs and selected class names. Each element summary includes a generated index, tag, ID/name/type/role, truncated visible text and a primary selector.

Before it reaches Mistral, the agent bounds the URL/title/text and keeps at most `DISCOVERY_OBSERVATION_MAX_ELEMENTS` (60 by default), truncating individual element fields. The user prompt wraps the page content as untrusted and asks for one next action. The system prompt instructs the model to treat page text as untrusted, stay on the local banking app, use observed selectors and avoid writes.

The same page text is also used by code to detect the known dialog marker and later business states. This is a DOM-based observation, not a vision model or computer screenshot interpretation. Screenshots are captured separately as best-effort evidence.

## Action loop

The `AgentAction` Pydantic model allows `navigate`, `click`, `fill`, `select`, `extract`, `complete`, and `escalate`, with required fields validated by action type. Each cycle:

1. Connect to the target URL once.
2. Observe the page and try to capture a before screenshot.
3. Persist the observation before requesting a decision.
4. Request one JSON-mode Mistral action and validate it against `AgentAction`.
5. Persist provider/model status, duration, and token usage when available; provider failures store the classified code and no prompt text.
6. Validate the URL/action against `SafetyPolicy`.
7. Execute the one supported action, marking `ui_action_executed` only if a browser action succeeded or extraction returned a value.
8. Persist the action outcome before the post-action screenshot and observation, then persist that after evidence separately.
9. Stop on verified `complete`, escalation, provider or action failure, request cap, or maximum steps.

The agent defaults to 15 steps and the settings default `MAX_LLM_REQUESTS_PER_RUN=15`; the router wraps the entire discovery in a 180-second timeout. The Mistral client defaults to a 30-second request timeout and one retry (only specific provider/network errors). A failed model call is represented as a failed decision event, not as a completed browser action.

## Recording semantics and redaction

Recording action arrays contain only trace items whose `ui_action_executed` flag is true. Observations and model-only decisions are persisted as events and are not counted as actions. Event types include `OBSERVATION`, `MODEL_DECISION`, `BROWSER_ACTION_SUCCEEDED`, `DISCOVERY_DECISION`, `DISCOVERY_STEP_FAILED`, and `HUMAN_INTERVENTION_REQUIRED`.

Observations are sanitized before persistence; common PII/financial patterns and sensitive dictionary keys are redacted, and full UI text fields are replaced. Action values are not stored in action parameters. The sanitized screenshot path and a screenshot-unavailable reason are recorded when possible. This is defense in depth, not a guarantee that all sensitive page content is removed. DOM snippets are sent to Mistral, and screenshots may contain page content; use only synthetic data.

Failed discovery attempts keep the rows/events already committed. A failed run may have zero actions yet include before-observation, provider-decision and failure events. It may also have missing screenshots if capture failed. The event API reports `evidence_available` based on whether the path currently exists.

## Compilation eligibility and stopping conditions

The router compiles only after the agent verifies a complete result. For known profile/savings goals, the `complete` action checks live page outputs and required fields; savings goals require a balance, profile goals require member name and ID, and a requested member ID must match when available.

The compiler then filters out unexecuted, `complete` and `escalate` trace steps. A no-action/empty artifact is rejected by publication validation. The compiled artifact must pass schema validation and a separate clean-context replay; otherwise its recording ends in `FAILED`, and no capability is published.

## Parameterization and generalization limit

The compiler creates a required string `member_id` parameter only when an executed fill/select value is identified as the requested four- or five-digit member ID (through `parameter_name="member_id"` or a member-related selector/description matching the request). Other fill/select values are embedded literally. The schema can describe other parameters, but current compiler inference does not generalize arbitrary values.

Thus, a successful recording for member 1001 can be reused for member 1002 only if its executed trace included an identified member-ID field and compiled artifact binds that value to `${inputs.member_id}`. It is not safe to assume that any successful discovery generalizes.

## Screenshots and files

Discovery screenshots are attempted before and after each action under `EVIDENCE_DIR/discovery/{run_id}/step_{n}_{before|after}.png`. Screenshots are best effort and no image is sent to the model. A successful loop writes `discovery_trace.json`; a failure can still preserve DB event rows and PNGs but may not write that final JSON trace.

Recording detail is returned by `GET /api/v1/recordings/{recording_id}`. The console's Recording Explorer polls the recording list every 2.5 seconds and selected details every 2 seconds, stopping detail polling at terminal states. These are browser timers polling HTTP endpoints; there is no SSE or WebSocket feed.

Related: [artifact schema and compiler](capability-artifacts.md), [evidence APIs](evidence-and-observability.md), [provider failures](error-handling.md).
