# ADR 0001: Separate discovery from deterministic replay

- **Status:** Current architectural decision, documented 2026-09-29 (not a claim about original approval date)
- **Scope:** Local APEX Automation implementation

## Context

Repeatedly asking a model to decide every UI action makes execution slower, incurs provider usage, and produces a less stable/auditable action sequence. The repository also needs an exploratory path for a workflow with no saved capability.

## Decision

Keep a discovery path and a replay path as explicit modes. Discovery may call Mistral to choose actions from current browser observations. When discovery succeeds, compile a typed capability artifact and validate it by running in a clean browser context. Matching requests can later execute the saved artifact with no model decision call. `force_mode` supports explicit `DISCOVERY`/`REPLAY` routing; absent an override, the router attempts a narrow capability lookup first.

## Consequences

- Replay uses a prescribed action sequence, validated parameters, locators and checkpoints, which is easier to inspect and repeat.
- Discovery is still sensitive to provider availability, prompt/page content, model output, UI state and safety-policy quality.
- An artifact can drift as the target UI changes; replay fails rather than asking an LLM to improvise.
- Clean-context replay checks compilation against the current simulator but does not prove every parameter or future application state.
- No general recovery mode currently invokes an LLM during replay.

## Implementation evidence

- Router: `backend/app/orchestration/router.py`.
- Discovery client/agent: `backend/app/discovery/agent.py`, `backend/app/llm/mistral.py`.
- Replay engine: `backend/app/replay/engine.py`.
- LLM-free integration assertion: `backend/tests/integration/test_critical_no_llm_replay.py`.

## Follow-up

Expand router matching and capability coverage only with explicit tests. Preserve the invariant that model-assisted recovery is an explicitly separate mode if introduced.
