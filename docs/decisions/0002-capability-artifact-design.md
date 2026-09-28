# ADR 0002: Store validated, versioned capability artifacts

- **Status:** Current architectural decision, documented 2026-09-29 (not a claim about original approval date)
- **Scope:** Capability schema, compilation and persistence

## Context

A discovery trace contains observations, model proposals, actual action results and evidence references. Replay needs a smaller executable contract with declared inputs/outputs and checkpoints; it should not treat a raw conversation as an action plan.

## Decision

Compile executed discovery actions into a strict Pydantic `CapabilityArtifact`. The schema defines semantic version, target, parameters, outputs, ordered steps, locator strategy, checkpoints, final success condition and metadata. Validate required references and contiguous ordered steps before publication. Store capability metadata and immutable version JSON in SQLAlchemy tables, then write an atomic JSON mirror after DB commit. A newly learned artifact must pass a clean-context deterministic replay before publication.

## Consequences

- The artifact is inspectable, versioned and independent of the Mistral conversation.
- DB is authoritative for the normal runtime; JSON mirrors help local inspection/fallback.
- Invalid/empty traces and failed clean replays do not publish.
- `next_version` increments the patch number; there is no schema migration/compatibility framework for artifacts.
- Current compiler generalizes a matching member ID, while other values are literal; artifact schema expressiveness exceeds compiler inference.
- Artifacts are not signed and no human approval gate exists.

## Implementation evidence

- Schema: `backend/app/artifacts/schema.py`.
- Compiler: `backend/app/artifacts/compiler.py`.
- Storage: `backend/app/artifacts/storage.py`.
- Publication transaction and replay gate: `backend/app/orchestration/router.py`.

## Follow-up

Add compatibility tests before changing schema versions. Add domain review/signing only as a deliberate security and operational design, not by implying `PUBLISHED` already means human-approved.
