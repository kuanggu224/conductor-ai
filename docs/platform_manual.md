# Platform Manual

## Purpose

Conductor coordinates backend/API delivery work from requirement clarification to validation and audit.

## Stages

1. Requirement freeze.
2. Backend/API design.
3. Backend/API implementation.
4. API validation and automated tests.
5. Delivery readiness, manifest, report, and replay verification.

## Roles

- Requirement designer
- Designer
- Solution designer
- Backend engineer
- Tester

## API Validation Evidence

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

## Removed Capabilities

The current platform manual intentionally excludes legacy product view rendering, static asset serving, static web validation, and combined browser/API delivery.
