# Documentation

APEX Automation is a local, synthetic-data engineering demonstration. This guide follows the code as it exists; it distinguishes current behavior from architectural intent and known gaps.

## Start here

- [README](../README.md): project purpose, screenshots, setup and first workflow.
- [Development and setup](development.md): fresh environment commands and local service startup.
- [Configuration](configuration.md): environment variables and defaults.
- [Architecture](architecture.md): components, trust boundaries, sequence diagrams and invariants.
- [Implementation inventory](implementation-inventory.md): verified, partial, missing and inconsistent areas.

## Workflow internals

- [Workflow lifecycle](workflow-lifecycle.md)
- [Discovery and recording](discovery-and-recording.md)
- [Capability artifacts](capability-artifacts.md)
- [Deterministic replay](deterministic-replay.md)
- [Human intervention](human-intervention.md)
- [Evidence and observability](evidence-and-observability.md)

## Operating and extending the project

- [API reference](api-reference.md)
- [Safety and security](safety-and-security.md)
- [Error handling](error-handling.md)
- [Testing](testing.md)
- [Troubleshooting](troubleshooting.md)
- [Decision records](decisions/0001-discovery-and-replay-separation.md)

The FastAPI-generated interactive schema is available at `/docs`, with the JSON document at `/openapi.json` when the local backend is running.
