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

## Next

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



