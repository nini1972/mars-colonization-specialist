# Mars Colonization Preparation Specialist

Phases 0-7 baseline for deterministic, testable implementation.

## Quick start (PowerShell)

```powershell
./scripts/setup.ps1
./scripts/check.ps1
```

## Progress

- Phase 0: Project bootstrap and CI gates.
- Phase 1: Knowledge foundation (ontology, ingestion, retrieval, release workflow).
- Phase 2: Core reasoning kernel (symbolic constraints, probabilistic forecasting, validation gate).
- Phase 3: First specialist modules (ECLSS, ISRU, Power) with shared typed I/O contracts.
- Phase 4: Central planner, mission-phase state machine, coupling checks, and replanning loop.
- Phase 5: Simulation IR/compiler, scenario suite, and generate-test-repair validation pipeline.
- Phase 6: Governance hard gate, audit trail export, benchmark deltas, and release hardening.
- Phase 7: MCP adapter package with contract tests ensuring parity with standalone pipelines.
- Phase 8:
  - Workstream 1 ✅ — FastMCP server runtime, structured JSON ToolError payload, schema-versioned error contract.
  - Workstream 2 ✅ — Token auth and per-tool authorization via env-driven policy (`MARS_MCP_AUTH_*`).
  - Parity contract tests ✅ — Full 4-tool stdio pipeline parity verified against adapter output.
  - Workstream 3 ✅ — Structured stderr logs, correlation propagation, progress notifications, and in-process metrics.
  - Workstream 4 ✅ — Transport-edge semantic validation, timeout-bounded execution (`MARS_MCP_TOOL_TIMEOUT_SECONDS`), and request-id idempotent replay/conflict protection.

## Next

- See `docs/phase8-transport-auth-observability-scope.md`.

## MCP Error Payload Contract

Tool failures are returned via MCP-native error semantics (`isError=true`) and include a machine-parsable JSON payload in the error text.

Current error payload keys:

- `schema_version` (current: `1.0`)
- `tool`
- `code`
- `request_id`
- `message`

Auth and authorization specific error codes:

- `unauthenticated`
- `forbidden`
- `invalid_request`
- `deadline_exceeded`

## MCP Auth (Workstream 2)

Environment variables:

- `MARS_MCP_AUTH_ENABLED`: enable auth checks (`true`/`false`, default `false`)
- `MARS_MCP_AUTH_TOKEN`: expected bearer token value when auth is enabled
- `MARS_MCP_AUTH_PERMISSIONS`: comma-separated permission list for the configured token

Permission model:

- `plan` -> `mars.plan`
- `simulate` -> `mars.simulate`
- `governance` -> `mars.governance`
- `benchmark` -> `mars.benchmark`

Token handling:

- Server reads bearer token from request metadata key `authorization` (value like `Bearer <token>`).
- For local/direct invocation paths, tools also accept optional `auth_token` parameter.

## MCP Observability (Workstream 3)

The MCP server now emits structured observability signals while preserving the existing success and ToolError payload contracts.

- Structured stderr logs: JSON log records per invocation for `tool.start`, `tool.success`, and `tool.error`.
- Correlation IDs: flow-aware correlation across `plan -> simulate -> governance/benchmark`, distinct from `request_id`.
- Progress notifications: best-effort MCP progress updates via FastMCP context APIs.
- Client log notifications: best-effort info/error log messages emitted to MCP clients.
- Metrics: lightweight in-process counters/timers (`calls`, `successes`, `failures`, `auth_failures`, `total_latency_ms`) kept internal for now.

## MCP Reliability Hardening (Workstream 4)

- Transport-edge validation: semantic payload checks fail fast with stable `invalid_request` ToolError payloads before execution.
- Timeout control: set `MARS_MCP_TOOL_TIMEOUT_SECONDS` to bound synchronous tool execution and return `deadline_exceeded` without committing timed-out plan/simulation state.
- Idempotency: duplicate `request_id` calls with identical tool arguments replay the cached success payload; conflicting re-use of a completed or in-flight `request_id` returns `invalid_request`.
- Idempotency cache bounds: use `MARS_MCP_IDEMPOTENCY_TTL_SECONDS` (default `900`) and `MARS_MCP_IDEMPOTENCY_MAX_ENTRIES` (default `512`) to limit replay cache retention and memory growth.
- State safety: `mars.plan` and `mars.simulate` only commit IDs into in-memory linkage maps after bounded execution succeeds.

## MCP Runtime Persistence (Phase 9A)

- Backend selection contract: `MARS_MCP_PERSISTENCE_BACKEND=memory|sqlite`.
  - Missing value defaults to `memory`.
  - Invalid values fail startup.
- SQLite storage location: `MARS_MCP_PERSISTENCE_SQLITE_PATH` (default: `./.mars_mcp_runtime.sqlite3`).
- Persistence contract version: SQLite metadata includes `schema_version=1`; startup enforces a version guard.
- Degraded mode policy:
  - SQLite initialization failures fall back to memory.
  - SQLite mid-run unavailability fails fast with `invalid_request`.
  - SQLite corruption fails fast by default and only falls back when `MARS_MCP_PERSISTENCE_FALLBACK_ON_CORRUPTION=true`.
- Deterministic ordering:
  - Idempotency eviction uses stable ordering (`completed_at_monotonic`, then `request_id`).
  - Replay/conflict behavior remains deterministic across restarts.
