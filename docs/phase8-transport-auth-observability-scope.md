# Phase 8 Scope: MCP Transport, Auth, and Observability

## Goal

Move from protocol-shaped in-process adapter calls to production-ready MCP service exposure with secure access controls and operational telemetry.

## Workstreams

### 1) Transport layer and server runtime ✅

- Introduced FastMCP server runtime wrapping `MarsMCPAdapter` (`src/mars_agent/mcp/server.py`).
- Deterministic request IDs, structured JSON ToolError payload with `schema_version: "1.0"`.
- MCP Error Payload Contract documented in README.

Acceptance criteria:

- ✅ All Phase 7 tools (`mars.plan`, `mars.simulate`, `mars.governance`, `mars.benchmark`) available through server transport.
- ✅ Contract tests validate parity between direct adapter calls and transport-exposed calls, including full 4-tool sequential stdio pipeline parity test (`test_transport_stdio_full_pipeline_parity_matches_adapter`).

### 2) Authentication and authorization ✅

- Env-driven token auth: `MARS_MCP_AUTH_ENABLED`, `MARS_MCP_AUTH_TOKEN`, `MARS_MCP_AUTH_PERMISSIONS`.
- Per-tool authorization with `unauthenticated` / `forbidden` error codes in the standard ToolError payload.
- Bearer token extracted from MCP request context metadata (`authorization` key) or optional `auth_token` tool parameter.
- Auth documented in README under "MCP Auth (Workstream 2)".

Acceptance criteria:

- ✅ Unauthorized requests receive deterministic access-denied responses with `schema_version: "1.0"` payload.
- ✅ Authorized requests produce outputs matching Phase 7 behavior (verified by auth transport tests).

### 3) Observability and diagnostics ✅

- Structured stderr JSON logs for `tool.start`, `tool.success`, and `tool.error` events.
- Stable log fields include: `event`, `tool`, `request_id`, `correlation_id`, `mission_id`, `outcome`, `latency_ms`, `auth_enabled`, `error_code`.
- Flow-aware correlation ID propagation across `plan -> simulate -> governance/benchmark` with in-memory linkage maps.
- Lightweight in-process per-tool metrics: `calls`, `successes`, `failures`, `auth_failures`, `total_latency_ms`.
- Best-effort FastMCP progress notifications and client log notifications during tool execution.

Acceptance criteria:

- ✅ Tool invocations emit traceable logs and basic metrics without changing success or ToolError payload contracts.
- ✅ Failure paths are diagnosable from logs (including auth failures with explicit `error_code`).

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