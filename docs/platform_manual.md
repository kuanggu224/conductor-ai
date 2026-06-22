# Platform Manual

## Purpose

Conductor coordinates software project execution through controlled stages, structured multi-agent work, verifiable artifacts, human-control gates, and audit/replay records.

The current manual describes the active backend/API execution lane. It does not redefine Conductor as a backend/API-only product.

## Current Stages

1. Requirement freeze.
2. Backend/API design.
3. Backend/API implementation.
4. API validation and automated tests.
5. Delivery readiness, manifest, report, and replay verification.

## Current Roles

- Requirement designer
- Designer
- Solution designer
- Backend engineer
- Tester

## Current API Validation Evidence

Validation should expose concrete signals:

- Endpoint path.
- HTTP method.
- Status code.
- Request payload.
- Response payload.
- Persistence evidence when a durable store is required.
- Contract-test output.

## Task Center

Task Center coordinates assignment, claim, completion, failure, release, sweep, audit, maintenance, and handoff payloads. Command snippets and JSON payloads are intended for external workers and operators.

## Configuration

Runtime configuration is stored under `conductor/config`. Secrets must stay out of logs, manifests, reports, and committed config examples.

## Platform Console

`frontend/` is the Conductor platform console for management, observability, and human-control workflows. It is not evidence that customer-project business frontend delivery has been restored.

## Paused Capabilities

The current runtime excludes legacy product view rendering, product static asset serving, static web validation, combined browser/API delivery, frontend development roles, and full-stack delivery profiles.

These are current implementation limits. Future delivery surfaces should be restored only through explicit design work and should plug into the existing controller, Task Center, artifact, validation, manifest, replay, and audit systems.
