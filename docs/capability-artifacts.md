# Capability artifacts

## Purpose

A capability artifact is the replay engine's typed, versioned action plan. It is distinct from the recording that supplied the source trace and from each later replay run. Artifacts are validated by `CapabilityArtifact` in `backend/app/artifacts/schema.py` before publication and again before replay.

## Compilation path

`ArtifactCompiler.compile_trace()` transforms executed `DiscoveryStepResult` values into ordered `ReplayStep` records. It skips model-only decisions (`ui_action_executed=False`) and terminal `complete`/`escalate` directives. It retains selectors, action type, static values or a recognized parameter binding, basic fallback locator definitions and generated checkpoint rules. The output fields and final condition are selected from goal keywords (`profile` versus savings lookup), not inferred generally from arbitrary business workflows.

For the current member workflow, an entered member ID may become a required string parameter with value expression `${inputs.member_id}`. Non-member form/select values stay concrete. Replay later replaces that parameter reference with the validated caller input.

## Schema and validation

The Pydantic artifact declares:

- `schema_version`, `capability_id`, semantic `version`, name/description, target application and surface type;
- optional source recording ID;
- unique named parameters and outputs;
- one or more replay steps with contiguous, one-based step numbers;
- target strategy (primary selector, fallback selectors and optional text/ARIA hints);
- per-step checkpoints and a required artifact-level success condition;
- safety and creation metadata.

The schema restricts action types to `navigate`, `click`, `fill`, `select`, `extract`, and `assert`; checkpoint types to element visible, URL contains, text present or title contains. It rejects undeclared references, duplicate parameter/output names, unsupported target applications, unknown fields and unordered/non-contiguous steps.

Illustrative excerpt with placeholder metadata; it matches the schema shape but is not a copied published artifact:

```json
{
  "schema_version": "1.0.0",
  "capability_id": "member_savings_lookup",
  "version": "1.0.0",
  "name": "Member savings lookup",
  "description": "Read a member's current savings snapshot",
  "target_application": "APEX Federal",
  "surface_type": "web",
  "source_recording_id": "rec_<recording-id>",
  "parameters": [
    {"name": "member_id", "param_type": "string", "description": "Target member ID", "required": true, "default_value": null}
  ],
  "outputs": [
    {"name": "savings_balance", "shape": "currency", "selector": "#savings-balance-val", "attribute": null, "description": "Current balance snapshot"}
  ],
  "steps": [
    {
      "step_number": 1,
      "action_type": "fill",
      "target": {"primary_selector": "#member-id-input", "fallback_selectors": [], "text_fallback": null, "aria_fallback": null},
      "value_expression": "${inputs.member_id}",
      "parameter_ref": "member_id",
      "checkpoints": [],
      "description": "Enter the requested member ID"
    }
  ],
  "success_condition": {"rule_type": "element_visible", "target": "#savings-balance-val", "description": "Verify savings balance is visible"},
  "safety_metadata": {"risk_level": "SAFE", "allowed_routes": ["/", "/member/*"]},
  "creation_metadata": {"compiled_at": "<timestamp>", "source_recording_id": "rec_<recording-id>"}
}
```

The complete artifact can be inspected through `GET /api/v1/capabilities/{capability_id}?version=...`.

## Versioning and storage

`ArtifactStorage.next_version()` scans database versions and local JSON mirrors and increments the patch number (e.g. `1.0.0` to `1.0.1`). Capability metadata and version JSON are stored in `capabilities` and `capability_versions`. A version is immutable: re-saving different JSON at the same capability/version raises an error.

Publication writes the DB artifact in the same final transaction as successful run and `PUBLISHED` recording state. After commit, storage writes a JSON mirror to `EVIDENCE_DIR/artifacts/{capability_id}_v{version}.json` via a temporary file and atomic replace. If that mirror write fails, the DB record remains authoritative. A JSON mirror can supply capabilities in a fresh DB only where listing fallback finds that artifact; it is not the primary storage backend.

There is no manual approval/review stage in the current artifact pipeline. `PUBLISHED` means compile/schema validation and clean replay succeeded; it does not mean a human approved business correctness or that future UI drift is covered.

## Current limitations

- Inference is built around `member_id` and savings/profile output selectors. It is not a universal recorder-to-workflow compiler.
- `TargetStrategy.aria_fallback` is represented but the replay engine currently builds candidates from primary CSS selector, configured fallback selectors and text fallback; it does not use the ARIA field.
- Some action/checkpoint schema options are broader than compiler behavior. For example, `assert` is accepted but replay does not implement a standalone assertion operation.
- Artifacts are not signed. DB/filesystem write permissions form the effective trust boundary.
- The target app UI and selectors can change; a previously published artifact may fail replay and require maintenance or rediscovery.

Next: [deterministic replay](deterministic-replay.md).
