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
- Internal metrics persistence (Issue #4 optional stretch):
  - Internal tool metrics counters now persist across restart when the SQLite backend is enabled.
  - This remains internal-only and does not change MCP success payload shape.
  - ToolError payload shape and structured stderr log schema remain unchanged.

## Phase 9B Dashboard (Increment 1)

- Server-rendered dashboard stack: FastAPI + Jinja2 templates + HTMX fragments + Alpine.js micro-state.
- UI schema version: `1.0` exposed in page and JSON endpoint responses for operator/debug clarity.
- Main page route: `/dashboard`
- JSON API route: `/api/telemetry/dashboard`
- Fragment routes:
  - `/dashboard/fragments/overview`
  - `/dashboard/fragments/invocations`
  - `/dashboard/fragments/invocation-detail`
  - `/dashboard/fragments/correlation`
  - `/dashboard/fragments/replay`



Here are the two plans in detail:

---

## Plan: Option A — Advanced Telemetry UI (DAG View)

The correlation chain already enforces plan→simulate→governance→benchmark sequences with 5 integrity checks in `_correlation_view_model()`. The current `correlation.html` renders a flat timeline list. This plan upgrades it to an animated SVG DAG with per-node status coloring, gap/cycle detection, and tooltips — no new JS libraries needed.

**Steps**

**Phase 1 — Backend enrichment** *(blocks Phase 2)*
1. Extend `_correlation_view_model()` in dashboard_app.py (~line 269) to add a `nodes` list — one entry per step with status `present | missing | orphaned`
2. Add `missing_node` issue type for mid-chain gaps (currently only `orphaned_child` fires; a governance event with no simulate ancestor isn't distinguished from a gap at position 2)
3. Add explicit cycle detection (backwards timestamp within a committed sequence)

**Phase 2 — Frontend DAG template** *(depends on Phase 1)*
4. Replace `<ul class="timeline">` in correlation.html with an inline SVG DAG: 4 circular nodes + `→` edge arrows, colored by status
5. Per-node tooltips via Alpine.js `x-data` showing event count + latest `recorded_at`
6. Add `.dag-node-present`, `.dag-node-missing`, `.dag-node-orphaned`, `.dag-edge` to dashboard.css

**Phase 3 — Tests** *(parallel with Phase 2)*
7. Add 3 test cases to test_dashboard_app.py: missing-node, orphaned-node, cycle — asserting `nodes` shape and `issues` list

**Relevant files**
- dashboard_app.py — `_correlation_view_model()`, `correlation_fragment()`
- correlation.html — replace timeline with SVG DAG
- dashboard.css — new DAG classes
- test_dashboard_app.py — 3 new tests

**Verification**
1. `python -m pytest test_dashboard_app.py -v` — all existing + 3 new tests pass
2. `ruff + mypy + pytest` full suite green
3. Manual: open `/dashboard` → correlation fragment → SVG renders with node colors

**Decisions**
- No new JS libraries; SVG + existing Alpine.js
- In-scope: visual DAG, gap/orphan/cycle detection, node-level status coloring
- Out-of-scope: animated edge transitions, click-to-drill-down (future increment)

---

## Plan: Option B — Habitat Thermodynamics Specialist

The specialist pattern is structural convention only (no ABC): a `@dataclass(slots=True)` with `.analyze(ModuleRequest) → ModuleResponse`. Add `HabitatThermodynamicsSpecialist` — it models thermal power demand and internal habitat temperature, coupling back into `CouplingChecker` as a fourth power load. Chosen over Orbital Comms / Surface Nav as the tightest fit with the existing power-coupling architecture.

**Steps**

**Phase 1 — Contracts** *(no dependencies)*
1. Add `HABITAT_THERMODYNAMICS = "habitat_thermodynamics"` to `Subsystem` in contracts.py

**Phase 2 — Specialist module** *(depends on Phase 1)*
2. Create `src/mars_agent/specialists/habitat_thermodynamics.py` — `HabitatThermodynamicsSpecialist`:
   - Inputs: `crew_count`, `solar_flux_w_m2`, `habitat_area_m2`, `insulation_r_value`
   - Outputs: `internal_temp_c`, `thermal_power_demand_kw`, `temp_regulation_efficiency`
   - Gate rules: `internal_temp_c` ∈ [18°C, 26°C]; `thermal_power_demand_kw` ≤ budget
   - Phase factors: TRANSIT=0.3, LANDING=1.0, EARLY_OPS=1.0, SCALING=1.1
3. Export from specialists/__init__.py

**Phase 3 — CouplingChecker extension** *(depends on Phase 2)*
4. Add optional `thermal: ModuleResponse | None = None` to `CouplingChecker.evaluate()` in coupling.py
5. New coupling rule: ECLSS + ISRU + thermal demand vs generation → HIGH conflict if over budget

**Phase 4 — Planner wiring** *(depends on Phases 2 & 3)*
6. Add `thermal: HabitatThermodynamicsSpecialist` field to `CentralPlanner` in planner.py
7. Add `_thermal_request()` with phase-dependent inputs (mirrors `_eclss_request()` exactly)
8. Extend `_run_modules()` to return a 4-tuple; pass `thermal=` to `coupling_checker.evaluate()`

**Phase 5 — Tests** *(parallel with Phase 4; depends on Phase 2)*
9. Create `tests/unit/test_habitat_thermodynamics.py`: gate passes nominally, gate rejects on temp > 26°C, cross-module power composition
10. Extend `test_central_planner.py` for the 4-tuple `_run_modules` return

**Relevant files**
- contracts.py — add enum value
- `src/mars_agent/specialists/habitat_thermodynamics.py` — NEW
- __init__.py — add export
- coupling.py — extend `evaluate()` + new rule
- planner.py — new field, `_thermal_request()`, 4-tuple `_run_modules`
- `tests/unit/test_habitat_thermodynamics.py` — NEW
- test_central_planner.py — extend for 4-tuple

**Verification**
1. `python -m pytest tests/unit/test_habitat_thermodynamics.py -v` — 3 new tests pass
2. `python -m pytest test_central_planner.py -v` — no regressions
3. Full suite: `ruff + mypy + pytest` — 89+ tests passed

**Decisions**
- Optional `thermal` param keeps `CouplingChecker` backward compatible
- Orbital Comms + Surface Navigation deferred to Phase 10d/10e
- Negotiator not yet extended for thermal variable (future)

---

**Which would you like to implement first — Option A (DAG UI) or Option B (Habitat Thermodynamics)?** They're independent and could also be done sequentially in one session. 

