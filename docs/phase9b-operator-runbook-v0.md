# Phase 9B Operator Runbook v0

## Purpose

Provide baseline triage workflow for MCP runtime observability during Phase 9B Week 1.

## Triage flow

1. Check health summary.
- Use telemetry overview to verify total failures vs successes and latest event time.

2. Identify failed invocations.
- Filter telemetry events by `outcome=failure`.
- Capture `request_id`, `correlation_id`, `tool`, and `error_code`.

3. Inspect invocation details.
- Query invocation detail by `request_id`.
- Confirm latency, mission context, and failure code.

4. Follow correlation chain.
- Query correlation chain by `correlation_id`.
- Validate order and tool progression through the chain.

5. Determine replay/degraded implications.
- Check logs for replay indicators and repeated request IDs.
- Check runtime health and persistence degradation signals.

## Known limits (Week 1)

- Data retention is in-memory ring buffer only.
- Pagination defaults to `page=1`, `page_size=50`.
- Query results are deterministic by timestamp/request/event ordering.
- No dashboard UI shipped in Week 1; query layer is foundation for UI.

## Security and redaction

- Authorization/token/password/secret key-like fields are redacted from query records.
- Do not expose raw request metadata outside approved telemetry schema.

## Escalation notes

- `invalid_request`: inspect transport validation and idempotency conflict paths.
- `deadline_exceeded`: inspect timeout config and tool execution duration.
- `unauthenticated` / `forbidden`: inspect auth token and permission mapping.
- Persistence degradation: verify backend contract and fallback policy from Phase 9A.

## Phase 10c Async Planner Soak

Use local docker-compose first, then CI with identical parameters.

### Execution

1. Start from a clean runtime state.
- `./scripts/soak-planner.ps1 -Mode both -Requests 240 -Concurrency 24 -ResetRuntimeState`

2. Run backend-specific validation with persisted artifact.
- `./scripts/soak-planner.ps1 -Mode both -Requests 240 -Concurrency 24 -PersistenceBackend sqlite -SqlitePath .\.mars_mcp_runtime.soak.sqlite3 -OutputPath .\data\processed\planner-soak-local.json -ResetRuntimeState`

3. Repeat in CI with the same `Requests` and `Concurrency` values for comparable trend lines.

### What gets measured

- Throughput (`throughput_rps`)
- Latency (`avg`, `p50`, `p95`, `p99`, `max`)
- Failure count and `error_rate`
- Degraded-plan count and `degraded_rate`
- Runtime-mode deltas (`sync_calls`, `async_calls`)

### Go/no-go guidance

- Async mode should keep `error_rate` at or below sync baseline.
- Async mode should not regress p95/p99 latency beyond agreed tolerance.
- No idempotency contract drift: request handling remains stable under repeated load.
- No persistence degradation or corruption signals when running with sqlite backend.

### Rollout policy

- Use staged rollout for `MARS_MCP_PLANNER_ASYNC=true` after soak passes.
- Keep rollback path ready by restoring `MARS_MCP_PLANNER_ASYNC` to unset/false.
