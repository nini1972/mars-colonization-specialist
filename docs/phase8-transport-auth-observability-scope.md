# Phase 8 Scope: MCP Transport, Auth, and Observability

## Goal

Move from protocol-shaped in-process adapter calls to production-ready MCP service exposure with secure access controls and operational telemetry.

## Workstreams

### 1) Transport layer and server runtime

- Introduce a thin MCP server runtime wrapper around the existing `MarsMCPAdapter` tool surface.
- Support deterministic request handling with explicit request IDs and structured error responses.
- Preserve Phase 7 tool names and argument contracts to avoid client breakage.

Acceptance criteria:

- All Phase 7 tools (`mars.plan`, `mars.simulate`, `mars.governance`, `mars.benchmark`) are available through server transport.
- Contract tests validate parity between direct adapter calls and transport-exposed calls.

### 2) Authentication and authorization

- Add token-based authentication for MCP tool invocation.
- Add policy checks for tool-level authorization (least-privilege by tool name).
- Introduce secure configuration loading for credentials and token validation settings.

Acceptance criteria:

- Unauthorized requests receive deterministic access-denied responses.
- Authorized requests continue to produce outputs matching Phase 7 behavior.

### 3) Observability and diagnostics

- Add structured logs with mission ID, tool name, latency, and outcome status.
- Add lightweight metrics counters/timers for success/failure/latency per tool.
- Add correlation IDs spanning plan->simulate->governance/benchmark flow.

Acceptance criteria:

- Tool invocations emit traceable logs and basic metrics.
- Failure paths are diagnosable from logs without reproducing locally.

### 4) Reliability hardening

- Add request schema validation boundaries at transport edge.
- Add timeout and bounded execution controls for long-running operations.
- Add idempotency strategy for duplicate invocation IDs where practical.

Acceptance criteria:

- Invalid payloads fail fast with stable error shape.
- Timeouts and retries do not corrupt in-memory run linkage.

## Test strategy

- Unit tests for auth/policy checks and request validation.
- Contract tests for transport parity vs. in-process adapter output.
- End-to-end smoke test covering `plan` -> `simulate` -> `governance` -> `benchmark`.
- Regression tests for deterministic behavior under fixed seeds.

## Out of scope

- External multi-tenant persistence backend.
- UI/dashboard implementation for telemetry visualization.
- Cloud deployment automation.

## Exit criteria

- Full quality gates green (`ruff`, `mypy`, `pytest`).
- Hosted CI green.
- Phase 8 docs include operator runbook for local start, auth config, and troubleshooting.