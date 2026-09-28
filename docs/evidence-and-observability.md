# Evidence and observability

## Evidence types and ownership

| Evidence | Storage | API/UI source | When available |
|---|---|---|---|
| Discovery metadata/action JSON/checkpoints | `discovery_recordings` JSON columns | `/api/v1/recordings` and detail; Recording Explorer | One recording for a discovery run, including partial/failed attempts. Actions include only confirmed executed actions. |
| Discovery observations/provider/action events | `recording_events` with unique per-recording sequence and JSON payload | Recording detail `events`; Recording Explorer | Incrementally committed by discovery callbacks. |
| Replay step result and screenshot path | `run_steps` foreign-keyed to run | Run detail and Evidence Explorer | When a replay step runs far enough to be persisted. Startup failure can produce no step. |
| PNG screenshots | Files under `EVIDENCE_DIR/discovery`, `replay` or `escalations` | Static `/evidence/*` paths referenced by API payloads | Best effort; capture errors do not always fail the workflow. |
| Discovery trace JSON | `EVIDENCE_DIR/discovery/{run_id}/discovery_trace.json` | Filesystem only (not a dedicated trace endpoint) | Written on successful agent completion; partial failed trace instead lives in event/action records when persistence callbacks were provided. |
| Replay execution JSON | `EVIDENCE_DIR/replay/{run_id}/replay_execution.json` | Filesystem only; run API contains step/result summary | Written only at normal end of replay, not every early return. |
| Artifact mirror | `EVIDENCE_DIR/artifacts/{id}_v{version}.json` | Capability API reads DB first, with fallback to files for listing/get behavior | Written after DB publication commit; mirror write errors leave DB artifact intact. |

`EvidenceModel` exists in the SQLAlchemy schema but current workflow paths primarily use run-step screenshot columns, recording event paths and files rather than creating rows in that table.

## Evidence APIs

- `GET /api/v1/runs` and `GET /api/v1/runs/{run_id}` return run state, outcome classification, steps and whether referenced step screenshots exist.
- `GET /api/v1/recordings` returns recording summaries and actual successful action count.
- `GET /api/v1/recordings/{recording_id}` returns event sequence, payload, screenshot path and `evidence_available`, plus linked replay runs.
- Files are served below `/evidence`. API values are local paths; the React UI rewrites the path to a static URL under that prefix.

The APIs detect missing files with `os.path.isfile`; they do not synthesize placeholder screenshots. The UI displays explicit “file unavailable” or “no screenshots attached” states. A recording can be complete as a database history while screenshots are absent.

## Refresh mechanism

The console uses HTTP polling:

- dashboard health every 30 seconds and overview data every 5 seconds;
- run list every 3 seconds and selected details every 2.5 seconds;
- recording list every 2.5 seconds and selected detail every 2 seconds;
- evidence run/recording list every 3 seconds and selected detail every 2.5 seconds;
- capability registry every 5 seconds;
- handoff queue/detail every 3 seconds.

Selected run detail polling stops at a terminal status. The Recording Explorer also stops selected recording-detail polling at terminal status, but the Evidence Explorer currently continues polling its selected recording detail every 2.5 seconds while the view is mounted. List polling continues while each view is mounted. There is no SSE, WebSocket or server push.

## Logs and metrics

`structlog` is configured for JSON-style console logs at INFO. Logs include safe metadata such as run ID, action type, model, duration, usage counters and error code; source code avoids logging the Mistral key and raw provider body. Some browser adapter errors include selector or exception text, so log access should still be treated as operational data. There is no metrics exporter, tracing backend, retention policy or centralized redaction audit in this repository.

## Privacy and availability

The sanitizers cover common pattern-based values, but do not guarantee all private data is removed. Screenshots can contain raw page contents and DOM observations are sent to Mistral during discovery. The `/evidence` static mount has no authentication or per-run authorization. Use synthetic values, local binding and controlled disk permissions. Evidence retention and cleanup are manual; no pruning policy is implemented.

See [Discovery and recording](discovery-and-recording.md), [API reference](api-reference.md), and [Safety and security](safety-and-security.md).
