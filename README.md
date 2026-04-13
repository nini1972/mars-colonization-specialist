# Mars Colonization Preparation Specialist

Phases 0-7 baseline for deterministic, testable implementation.

## Quick start (PowerShell)

```powershell
./scripts/setup.ps1
./scripts/check.ps1
```
## Start dashboard

.venv\Scripts\python.exe -m uvicorn mars_agent.dashboard_app:app --reload

If you want to use a different port:

.venv\Scripts\python.exe -m uvicorn mars_agent.dashboard_app:app --reload --port 8080

navigate to:
http://localhost:8000/dashboard


Remark: If you see the "persistence degraded" warning in the logs, it means the dashboard is running with the in-memory persistence backend instead of SQLite. This is expected if you haven't set the `MARS_MCP_PERSISTENCE_BACKEND` environment variable to `sqlite`. The dashboard will still function, but any state will be lost on restart.
Fix: set the env var before starting the dashboard:
$env:MARS_MCP_PERSISTENCE_BACKEND = "sqlite"
.venv\Scripts\python.exe -m uvicorn mars_agent.dashboard_app:app --reload

Or permanently in your shell session:
$env:MARS_MCP_PERSISTENCE_SQLITE_PATH = ".\.mars_mcp_runtime.sqlite3"
$env:MARS_MCP_PERSISTENCE_BACKEND    = "sqlite"

When using Docker/docker-compose, the env var is already defaulted to sqlite in docker-compose.yml and docker-run.ps1 — so the message only appears in the plain uvicorn dev workflow where no env vars are set.

## Start Docker-compose
Option A — docker-compose (recommended, handles build + run together):

# From the repo root — builds image and starts the dashboard
docker compose up --build

# Or detached (background):
docker compose up --build -d

Then navigate to http://localhost:8000/dashboard.
To stop:docker compose down
to stop and remove Sqlite volume:docker compose down -v

Option B — PowerShell scripts (build once, run separately):

# 1. Build the image
./scripts/docker-build.ps1

# 2. Run the container (foreground)
./scripts/docker-run.ps1

# Run detached on a different port:
./scripts/docker-run.ps1 -Port 8080 -Detach

Then navigate to http://localhost:8000/dashboard. To stop the detached container:docker stop mars_dashboard

Then rebuild:
docker compose up --build
# or
./scripts/docker-build.ps1

SQLite persistence is auto-configured in both paths — the container stores the .sqlite3 file in the mars_data named Docker volume at /data/mars_mcp_runtime.sqlite3. The "persistence degraded" warning will not appear when running via Docker.

Prerequisites: Docker Desktop must be running before either command.

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
- Phase 9A ✅ — MCP runtime persistence (memory + SQLite backends), idempotency eviction, metrics persistence, schema version guard.
- Phase 9B ✅ — Operator dashboard (FastAPI + Jinja2 + HTMX + Alpine.js): overview, invocation list, invocation detail, correlation chain, replay panel.
  - Option A ✅ — Advanced Telemetry DAG UI: animated SVG correlation DAG, per-node status coloring (`present | missing | orphaned`), gap/cycle detection, Alpine.js tooltips, responsive CSS, keyboard navigation.
  - Option B ✅ — Habitat Thermodynamics Specialist: `HabitatThermodynamicsSpecialist` models thermal power demand and habitat temperature; coupled into `CouplingChecker` as a fourth power load; planner wired with phase-dependent thermal requests.
- Phase 10a ✅ — LLM-driven multi-agent negotiation (`MultiAgentNegotiator`): Chief Engineer persona negotiates ISRU feedstock reductions via OpenAI Chat Completions; fully opt-in via `MARS_LLM_ORCHESTRATOR_ENABLED` + `OPENAI_API_KEY`; deterministic fallback on any failure path.
- Phase 10b ✅ — Multi-turn negotiation history: `NegotiationRound` dataclass accumulates accepted/rejected rounds; each iteration passes prior rounds to the LLM for context; `_build_messages()` injects a structured history section; mock-client unit tests added (101 tests passing).
  - Option 1 ✅ — Expanded negotiation variables: `NegotiationResult` gains `crew_reduction` (int 0–5) and `dust_degradation_adjustment` (float 0.0–0.1); `NegotiationRound` records both per round; `_build_messages()` advertises three knobs to the LLM; `_handle_replan()` applies changes via `dataclasses.replace()` on `MissionGoal`; `plan()` threads the updated goal forward each replan iteration (104 tests passing).
- Option D ✅ — Cloud-ready infrastructure: `Dockerfile` (single image, non-root `mars` user, `/data` volume mount); `.dockerignore`; `docker-compose.yml` with named `mars_data` volume and env-driven config; `GET /health` liveness + `GET /ready` readiness probes added to `dashboard_app.py`; `python -m mars_agent.dashboard` entry point via new `src/mars_agent/dashboard/__main__.py`; `uvicorn` added as core dependency; `scripts/docker-build.ps1` + `scripts/docker-run.ps1` (113 tests passing).
- Track A ✅ — Parallel specialist execution: ECLSS, ISRU, and HabitatThermodynamics run concurrently via `ThreadPoolExecutor(max_workers=3)`; PowerSpecialist runs after (depends on ECLSS + ISRU outputs); no public API changes (112 tests passing). See [issue #13](https://github.com/nini1972/mars-colonization-specialist/issues/13).
- Track B ✅ — Smarter negotiation fallback: replaced fixed +20% ISRU-only fallback with `_conflict_aware_fallback()` driven by `_CONFLICT_KNOB_TABLE`; per-conflict knobs (ISRU Δ, crew Δ, dust Δ) combined via `max()` across active conflicts; `PlannerSettings.conflict_knob_overrides` allows per-deployment override; 4 new unit tests (116 tests passing). See [issue #13](https://github.com/nini1972/mars-colonization-specialist/issues/13).
- Track D ✅ — Persistent negotiation memory: `NegotiationMemoryStore` fingerprints each conflict scenario (SHA-256 of phase + crew + solar_kw + battery_kwh + dust + hours_without_sun + sorted conflict IDs) and caches accepted outcomes in-memory or SQLite; `CentralPlanner._handle_replan()` checks the cache first — cache hits replay stored deltas with a `[memory hit][llm|fallback]` rationale prefix and increment `hit_count`; `PlannerSettings.negotiation_store_path` enables SQLite persistence; thread-safe with `threading.Lock()`; `is_fallback` distinguishes LLM-accepted vs deterministic-fallback outcomes; schema version guard on DB open; 9 new unit tests (125 tests passing). See [issue #13](https://github.com/nini1972/mars-colonization-specialist/issues/13).
- Track E ✅ — Agent health panel in dashboard: `SpecialistTiming` frozen dataclass captures `latency_ms` and `gate_accepted` per specialist inside `_run_modules()` via `_timed_analyze()` helper; timings flow through `PlanResult.specialist_timings` → server `_record_specialist_metric()` → `_TOOL_METRICS` at key `"specialist.<name>"` (6 float counters: calls, total_latency_ms, gate_pass, gate_fail, last_gate_accepted, last_latency_ms); auto-persisted to existing SQLite `tool_metrics` table; new `/dashboard/fragments/agents` route polls every 30 s and renders a 6-column table (subsystem · calls · avg latency · gate pass · gate fail · last outcome status dot); CSS `.status-dot/.status-pass/.status-fail` added; 8 new tests (3 timing + 5 dashboard panel); 133 tests passing. See [issue #13](https://github.com/nini1972/mars-colonization-specialist/issues/13).
- Track C ✅ — Specialist capability protocol: `TradeoffKnob` frozen dataclass (`name`, `description`, `min_value`, `max_value`, `preferred_delta`, `unit`; validated on `__post_init__`) and `SpecialistCapability` frozen dataclass (`subsystem`, `accepts_inputs`, `produces_metrics`, `tradeoff_knobs`) added to `contracts.py`; all four specialists implement `capabilities()` — ECLSS exposes `crew_reduction` (0–5, Δ=1) and `recycle_efficiency` (0.75–0.98, Δ=0.02); ISRU exposes `isru_reduction_fraction` (0–0.8, Δ=0.05) and `reactor_efficiency` (0.4–0.95, Δ=0.02); Power exposes `dust_degradation_adjustment` (0–0.1, Δ=0.02); HabitatThermodynamics exposes `thermal_power_budget_reduction` (0–20 kW, Δ=2.0); `SpecialistRegistry` (NEW `src/mars_agent/orchestration/registry.py`) is a runtime catalogue keyed by `Subsystem` — `register()` (raises `ValueError` on duplicate), `get()`, `all_capabilities()`, `subsystems()`, `SpecialistRegistry.default()` pre-populates all four (addresses gap #6); `CentralPlanner.registry` field replaces hardcoded specialist fields; `_conflict_aware_fallback()` queries `registry.all_capabilities()` for unknown-conflict ISRU delta; `_handle_replan()` passes `registry.all_capabilities()` to `MultiAgentNegotiator.negotiate()` (addresses gap #3); `MultiAgentNegotiator._build_messages()` appends a "Specialist preferences" section to the LLM system prompt when capabilities are provided; `tests/unit/test_specialist_capabilities.py` (NEW) — 21 test functions (24 test cases with parametrize) covering `TradeoffKnob` validation, per-specialist `capabilities()` correctness, `accepts_inputs` coverage, `SpecialistRegistry` CRUD, planner–registry integration, and negotiator message building; 159 tests passing. See [issue #13](https://github.com/nini1972/mars-colonization-specialist/issues/13).

## Next

- Specialist health/failure handling (gap #7) — graceful degradation when a specialist throws; see [issue #13](https://github.com/nini1972/mars-colonization-specialist/issues/13).
- Phase 10b — Add `AsyncOpenAI` async negotiation support (open for future phase).
- See `docs/phase9b-operator-runbook-v0.md` for dashboard operation and `docs/phase8-transport-auth-observability-scope.md` for MCP transport details.

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

## Correlation DAG UI (Option A)

- Correlation fragment renders an inline SVG DAG with four nodes: `plan → simulate → governance → benchmark`.
- Node status: `present` (green), `missing` (amber dashed), `orphaned` (red).
- Gap detection: mid-chain missing node fires `missing_node` issue type.
- Cycle detection: backwards timestamp within a committed sequence fires `cycle_detected`.
- Per-node tooltips via Alpine.js showing event count and latest `recorded_at`.
- Keyboard navigation: nodes are focusable; tooltip opens on focus as well as hover.
- Responsive layout: SVG scales fluidly on narrow viewports.

## LLM Multi-Agent Negotiation (Phase 10)

### Phase 10a — Increment 1

- `MultiAgentNegotiator` class in `src/mars_agent/orchestration/negotiator.py`.
- Persona: Chief Engineer Orchestrator negotiates ISRU feedstock reductions via OpenAI Chat Completions.
- Response schema: `NegotiationResult` Pydantic model — `accepted`, `isru_reduction_fraction` (0.0–0.8), `rationale`.
- Fully opt-in: `MARS_LLM_ORCHESTRATOR_ENABLED=true` + `OPENAI_API_KEY` required; inert by default.
- Deterministic fallback (step-based reduction) on any failure path.
- `json_object` response format for reliable LLM output parsing.

### Phase 10b — Multi-turn history

- `NegotiationRound` frozen dataclass: `round_num`, `reduction_fraction`, `accepted`, `rationale`.
- Each call to `negotiate()` accepts `history: tuple[NegotiationRound, ...]`; prior rounds are injected into the LLM user message as a structured "Prior negotiation rounds" section.
- `CentralPlanner.plan()` accumulates `negotiation_history` and passes it forward each replan iteration.
- Unit tests exercise the LLM path via `MultiAgentNegotiator.__new__` + mock OpenAI client injection (no live API key required).



### Multi-agent architecture

> Full analysis and improvement backlog: [issue #13](https://github.com/nini1972/mars-colonization-specialist/issues/13)

**Current architecture:** 4 domain specialists (ECLSS, Power, ISRU, HabitatThermodynamics) + 5 orchestrators (CentralPlanner, MultiAgentNegotiator, CouplingChecker, MissionPhaseStateMachine, GovernanceGate). Communication is entirely synchronous direct function calls.

**Improvement tracks:**

| Track | Description | Status |
|-------|-------------|--------|
| A | Parallel specialist execution — ECLSS, ISRU, Thermal run concurrently via `ThreadPoolExecutor`; Power waits on their outputs | ✅ Done (112 tests passing) |
| B | Smarter negotiation fallback — conflict-aware knob selection instead of fixed 20% ISRU reduction | ✅ Done (116 tests passing) |
| C | Specialist capability protocol — `capabilities()` method on each specialist so the negotiator can query trade-off preferences; `SpecialistRegistry` for dynamic add/remove | ✅ Done (159 tests passing) |
| D | Persistent negotiation memory — resolved conflict patterns persisted to SQLite for cross-mission reuse | ✅ Done (125 tests passing) |
| E | Agent health panel in dashboard — per-specialist call count, avg latency, and last outcome fragment | ✅ Done (133 tests passing) |

