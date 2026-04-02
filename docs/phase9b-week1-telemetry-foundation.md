# Phase 9B Week 1: Telemetry Foundation

## Status

- Phase: 9B Week 1
- Scope: Foundation only (query layer + contracts + docs)
- Out of scope: UI implementation details and Phase 9A optional metrics persistence stretch (issue #4)

## 1. Scope and Contracts Freeze

### 1.1 Non-goals

- No changes to MCP success payload contracts.
- No changes to ToolError payload contracts.
- No provider-specific cloud deployment work in Week 1.
- Optional metrics persistence stretch tracked separately in issue #4.

### 1.2 Telemetry source map

- Structured stderr observability logs emitted from `src/mars_agent/mcp/server.py`.
- In-process metrics state stored in `_TOOL_METRICS` in `src/mars_agent/mcp/server.py`.
- Phase 9A persisted runtime state in `src/mars_agent/mcp/persistence.py`:
  - completed idempotency requests
  - in-flight requests
  - plan correlation map
  - simulation correlation map

### 1.3 Telemetry schema v1

Telemetry query responses are versioned with:

- `schema_version`: `1.0`

Required event fields for Week 1 query layer:

- `recorded_at`
- `event`
- `tool`
- `request_id`
- `correlation_id`
- `mission_id`
- `outcome`
- `latency_ms`
- `auth_enabled`
- `error_code`

### 1.4 Redaction and security rules

- Sensitive keys are redacted in telemetry records before query exposure.
- Redaction applies to key names containing:
  - `authorization`
  - `token`
  - `auth_token`
  - `password`
  - `secret`

### 1.5 Performance budgets (Week 1 baseline)

- Default query window: latest in-memory records only (bounded by ring buffer).
- Default pagination: `page=1`, `page_size=50`.
- Hard requirement: deterministic ordering for list and chain queries.
- Target budget for Week 1 local runs:
  - p95 query latency under 100 ms for up to 5,000 in-memory events.

## 2. Operator workflow definitions

### 2.1 Operator questions

- What is running now?
- What failed recently, and why?
- What is the correlation chain for a request flow?
- Was a response replayed or freshly executed?
- Is runtime health degraded?

### 2.2 Query/view mapping

- Overview view -> `telemetry_overview()`
- Invocation details view -> `telemetry_invocation_detail(request_id)`
- Correlation chain view -> `telemetry_correlation_chain(correlation_id)`
- Event stream/filter view -> `telemetry_list_events(...)`

### 2.3 Minimum required fields per view

- `tool`
- `request_id`
- `correlation_id`
- `outcome`
- `error_code`
- `latency_ms`
- `recorded_at`

## 3. Query layer foundation delivered

Implemented in:

- `src/mars_agent/mcp/telemetry.py`
- `src/mars_agent/mcp/server.py`

Capabilities:

- Deterministic ordering and pagination.
- Filtering by tool, outcome, and error code.
- Invocation detail lookup.
- Correlation chain retrieval.
- Health/count overview summary.

## 4. Tests and guardrails delivered

Implemented in:

- `tests/unit/test_mcp_telemetry.py`

Coverage:

- Schema-contract assertions for query output fields.
- Redaction tests for token/authorization-like fields.
- Deterministic ordering and pagination behavior.
- Invocation and correlation chain query behavior.
- Overview health/counts behavior.

## 5. Week 1 exit criteria traceability

- Telemetry schema v1 approved and versioned: ✅
- Operator workflows mapped to implemented query views: ✅
- Query layer returns usable data with filtering/pagination: ✅
- Contract and redaction regression tests green: ✅ (see test file)
- Runbook v0 published: ✅ (`docs/phase9b-operator-runbook-v0.md`)
