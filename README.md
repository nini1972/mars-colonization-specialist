# Mars Colonization Preparation Specialist

Phases 0-7 baseline for deterministic, testable implementation.

## Quick Start (PowerShell)

```powershell
./scripts/setup.ps1
./scripts/check.ps1
```

## Start Dashboard

```powershell
.venv\Scripts\python.exe -m uvicorn mars_agent.dashboard_app:app --reload
```

If you want to use a different port:

```powershell
.venv\Scripts\python.exe -m uvicorn mars_agent.dashboard_app:app --reload --port 8080
```

Navigate to [http://localhost:8000/dashboard](http://localhost:8000/dashboard).

Remark: If you see the "persistence degraded" warning in the logs, it means the dashboard is running with the in-memory persistence backend instead of SQLite. This is expected if you haven't set the `MARS_MCP_PERSISTENCE_BACKEND` environment variable to `sqlite`. The dashboard will still function, but any state will be lost on restart.

Fix: set the env var before starting the dashboard:

```powershell
$env:MARS_MCP_PERSISTENCE_BACKEND = "sqlite"
.venv\Scripts\python.exe -m uvicorn mars_agent.dashboard_app:app --reload
```

Or permanently in your shell session:

```powershell
$env:MARS_MCP_PERSISTENCE_SQLITE_PATH = ".\.mars_mcp_runtime.sqlite3"
$env:MARS_MCP_PERSISTENCE_BACKEND    = "sqlite"
```

When using Docker/docker-compose, the env var is already defaulted to sqlite in docker-compose.yml and docker-run.ps1 — so the message only appears in the plain uvicorn dev workflow where no env vars are set.

Dev-only failure injection (for dashboard degraded-path testing):

```powershell
$env:MARS_DASHBOARD_SOAK_REPORT_PATH = ".\data\processed\planner-soak-ci.json"
$env:MARS_DEV_FAIL_SPECIALIST = "eclss"   # eclss | isru | power | habitat_thermodynamics
docker compose up --build
```

Then invoke `mars.plan` through the running MCP endpoint, for example via `your_script.py`, and confirm the dashboard Agent Health panel shows a fault (`status-warn`) and increased fault count.

```powershell
.\.venv\Scripts\python.exe .\your_script.py
```

To disable injection:

```powershell
Remove-Item Env:MARS_DEV_FAIL_SPECIALIST
```

## Start Docker Compose

Option A — `docker-compose` (recommended, handles build and run together):

Build and start from the repo root:

```powershell
docker compose up --build
```

Or detached:

```powershell
docker compose up --build -d
```

Then navigate to [http://localhost:8000/dashboard](http://localhost:8000/dashboard).

To stop:

```powershell
docker compose down
```

To stop and remove the SQLite volume:

```powershell
docker compose down -v
```

Option B — PowerShell scripts (build once, run separately):

1. Build the image:

```powershell
./scripts/docker-build.ps1
```

1. Run the container in the foreground:

```powershell
./scripts/docker-run.ps1
```

1. Run detached on a different port:

```powershell
./scripts/docker-run.ps1 -Port 8080 -Detach
```

Then navigate to [http://localhost:8000/dashboard](http://localhost:8000/dashboard).

To stop the detached container:

```powershell
docker stop mars_dashboard
```

Then rebuild:

```powershell
docker compose up --build
```

Or:

```powershell
./scripts/docker-build.ps1
```

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
- Track D ✅ — Persistent negotiation memory: `NegotiationMemoryStore` fingerprints each conflict scenario (SHA-256 of phase + crew + solar_kw + battery_kwh + dust + hours_without_sun + sorted conflict IDs + normalized knowledge context) and caches accepted outcomes in-memory or SQLite; `CentralPlanner._handle_replan()` checks the cache first — cache hits replay stored deltas with a `[memory hit][llm|fallback]` rationale prefix and increment `hit_count`; `PlannerSettings.negotiation_store_path` enables SQLite persistence; thread-safe with `threading.Lock()`; `is_fallback` distinguishes LLM-accepted vs deterministic-fallback outcomes; schema version guard on DB open; knowledge provenance (`knowledge_fingerprint`) is persisted per outcome. See [issue #13](https://github.com/nini1972/mars-colonization-specialist/issues/13).
- Track E ✅ — Agent health panel in dashboard: `SpecialistTiming` frozen dataclass captures `latency_ms` and `gate_accepted` per specialist inside `_run_modules()` via `_timed_analyze()` helper; timings flow through `PlanResult.specialist_timings` → server `_record_specialist_metric()` → `_TOOL_METRICS` at key `"specialist.<name>"` (6 float counters: calls, total_latency_ms, gate_pass, gate_fail, last_gate_accepted, last_latency_ms); auto-persisted to existing SQLite `tool_metrics` table; new `/dashboard/fragments/agents` route polls every 30 s and renders a 6-column table (subsystem · calls · avg latency · gate pass · gate fail · last outcome status dot); CSS `.status-dot/.status-pass/.status-fail` added; 8 new tests (3 timing + 5 dashboard panel); 133 tests passing. See [issue #13](https://github.com/nini1972/mars-colonization-specialist/issues/13).
- Track C ✅ — Specialist capability protocol: `TradeoffKnob` frozen dataclass (`name`, `description`, `min_value`, `max_value`, `preferred_delta`, `unit`; validated on `__post_init__`) and `SpecialistCapability` frozen dataclass (`subsystem`, `accepts_inputs`, `produces_metrics`, `tradeoff_knobs`) added to `contracts.py`; all four specialists implement `capabilities()` — ECLSS exposes `crew_reduction` (0–5, Δ=1) and `recycle_efficiency` (0.75–0.98, Δ=0.02); ISRU exposes `isru_reduction_fraction` (0–0.8, Δ=0.05) and `reactor_efficiency` (0.4–0.95, Δ=0.02); Power exposes `dust_degradation_adjustment` (0–0.1, Δ=0.02); HabitatThermodynamics exposes `thermal_power_budget_reduction` (0–20 kW, Δ=2.0); `SpecialistRegistry` (NEW `src/mars_agent/orchestration/registry.py`) is a runtime catalogue keyed by `Subsystem` — `register()` (raises `ValueError` on duplicate), `get()`, `all_capabilities()`, `subsystems()`, `SpecialistRegistry.default()` pre-populates all four (addresses gap #6); `CentralPlanner.registry` field replaces hardcoded specialist fields; `_conflict_aware_fallback()` queries `registry.all_capabilities()` for unknown-conflict ISRU delta; `_handle_replan()` passes `registry.all_capabilities()` to `MultiAgentNegotiator.negotiate()` (addresses gap #3); `MultiAgentNegotiator._build_messages()` appends a "Specialist preferences" section to the LLM system prompt when capabilities are provided; `tests/unit/test_specialist_capabilities.py` (NEW) — 21 test functions (24 test cases with parametrize) covering `TradeoffKnob` validation, per-specialist `capabilities()` correctness, `accepts_inputs` coverage, `SpecialistRegistry` CRUD, planner–registry integration, and negotiator message building; 159 tests passing. See [issue #13](https://github.com/nini1972/mars-colonization-specialist/issues/13).
- Track F ✅ — Specialist health and graceful degradation: per-specialist exceptions are isolated inside `CentralPlanner._run_modules()` while ECLSS, ISRU, and HabitatThermodynamics still fan out in parallel; Power is skipped with a structured reason when ECLSS or ISRU fails upstream. `SpecialistTiming` now records `failed` and `failure_reason`; `PlanResult` gains `degraded`, returns only successful subsystem responses, skips coupling evaluation, and keeps `next_phase` at `goal.current_phase` on degraded plans. `mars.plan` now surfaces `degraded` and `specialist_faults`; specialist telemetry adds `fault_count` and `last_failed`; the Agent Health dashboard adds a Faults column plus an amber fault status. 11 new unit tests (7 planner + 4 dashboard); 170 tests passing. See [issue #13](https://github.com/nini1972/mars-colonization-specialist/issues/13).
- Track G ✅ — Knowledge-informed coordination: planner now builds a bounded `KnowledgeContext` from mission evidence, retrieval hits (`RetrievalIndex`), and ontology hints (`OntologyStore`) and threads it through conflict detection, negotiation, fallback, and negotiation memory. `CouplingChecker.evaluate()` attaches knowledge metadata (`evidence_doc_ids`, `knowledge_signals`) to emitted conflicts; `MultiAgentNegotiator._build_messages()` includes structured knowledge sections in the system prompt; `_conflict_aware_fallback()` scales conflict knob deltas using trust-weighted knowledge context while retaining safety clamps; conflict fingerprints now incorporate normalized knowledge context to avoid stale cache replays.
- Phase 6 Step 1 ✅ — Composed governance workflow: `GovernanceWorkflow` now runs the hard governance gate, benchmark harness, and audit trail builder together for a given plan/simulation pair, and can finalize quarterly or hotfix releases through `ReleaseHardeningManager`. Failed release finalization still emits the deterministic audit JSON when an audit path is provided, so blocked releases remain reviewable.
- Phase 6 Step 2 ✅ — Release bundle export: `GovernanceWorkflow.export_release_bundle()` now persists a deterministic release pack for quarterly or hotfix finalization, writing manifest JSON, bulletin markdown, and audit JSON together under predictable filenames derived from version and release type.
- Phase 6 Step 3 ✅ — Operator-facing release entry point: MCP adapter/server now expose `mars.release`, which composes governance review, benchmark checks, release finalization, and optional bundle export behind one tool call. Quarterly and hotfix releases share the same payload (`release_type`, `version`, `changed_doc_ids`, `summary`), and blocked releases now return a stable invalid-request error that also points operators at the exported audit path when `output_dir` is provided.
- Phase 6 Step 4 ✅ — Release metadata and benchmark policy hardening: benchmark runs now carry an explicit named policy (`nasa-esa-mission-review@2026.03`) plus provenance source metadata; quarterly and hotfix manifests now stamp `governance_policy_version`, `benchmark_profile`, `benchmark_policy_version`, `rationale_type`, and deterministic `artifact_provenance`; release bulletins and exported manifest JSON surface the same metadata so `mars.release` bundles can be reviewed and archived against an explicit policy baseline.
- Phase 10c Step 3 ✅ — Async planner soak automation: deterministic soak harness added (`scripts/soak_planner.py`) with PowerShell runner (`scripts/soak-planner.ps1`) for sync/async comparison under identical parameters; new CI job `planner-soak-trend` in `.github/workflows/ci.yml` runs `--mode both --requests 240 --concurrency 24` and uploads `data/processed/planner-soak-ci.json` as artifact `planner-soak-ci` for trend comparison across runs.
- Phase 11 Step 1 ✅ — Deterministic negotiation protocol groundwork: internal `NegotiationSession` / `NegotiationTranscript` / `NegotiationMessage` primitives added in `src/mars_agent/orchestration/negotiation_protocol.py`; `CentralPlanner` now wraps each replan attempt in a canonical message transcript (`session_started`, `conflict_detected`, `proposal_requested`, `proposal_submitted|fallback_applied|memory_replayed`, `proposal_accepted`, `session_closed`) while keeping `mars.plan` payloads and MCP contracts unchanged. This is the first step toward peer-to-peer, message-driven negotiation; specialists are not yet exchanging messages directly.
- Phase 11 Step 2 ✅ — Specialist-authored negotiation proposals: each specialist now implements deterministic `propose_tradeoffs()` logic and can emit `TradeoffProposal` objects for active conflict IDs. `CentralPlanner` collects these proposals from impacted specialists, records them as `proposal_submitted` transcript events, passes them into `MultiAgentNegotiator` prompt construction, and uses them to seed deterministic fallback knob selection. This moves proposal authorship away from the planner while preserving existing `mars.plan` and MCP response shapes.
- Phase 11 Step 3 ✅ — Negotiation observability surfaces: `CentralPlanner` now publishes completed negotiation sessions through an internal observer hook; MCP telemetry stores recent negotiation sessions separately from tool events; the dashboard adds a `Negotiation Sessions` fragment that renders recent transcript-driven sessions, specialist proposals, decision source (`llm | fallback | memory`), and ordered transcript messages. This remains dashboard/internal-telemetry only and does not change public MCP tool payloads.
- Phase 11 Step 4 ✅ — Bounded specialist-to-specialist message handling: specialists now consume peer-authored proposals via deterministic `review_peer_proposals()` logic, emit peer review notes in a single bounded round, and record `proposal_reviewed` transcript events before the session closes. `CentralPlanner` threads those reviews into both `MultiAgentNegotiator` prompt construction and deterministic fallback selection, so peer review now affects negotiation context without changing `mars.plan` or MCP tool response schemas.
- Phase 11 Step 5 ✅ — Bounded counter-proposal round: `COUNTER` peer reviews with a suggested delta are now materialized into explicit second-round counter-proposals, recorded as `counter_proposal_submitted` transcript events, threaded into `MultiAgentNegotiator` prompt construction, and included in deterministic fallback weighting. Negotiation telemetry/dashboard payloads now expose those bounded counter-proposals while `mars.plan` and MCP response schemas remain unchanged.

## Next

- Continue the Phase 11 rollout by deciding whether the bounded counter-proposal round justifies a deterministic third step such as selective re-review or multi-round convergence thresholds.
- Continue telemetry soak for `MARS_MCP_PLANNER_ASYNC=true` under production-like load before making it a default.
- See `docs/phase9b-operator-runbook-v0.md` for dashboard operation and `docs/phase8-transport-auth-observability-scope.md` for MCP transport details.

## Async planner soak (local first)

Run deterministic soak locally, then repeat in CI with the same parameters.

```powershell
./scripts/soak-planner.ps1 -Mode both -Requests 240 -Concurrency 24 -ResetRuntimeState
```

To include a persisted report artifact:

```powershell
./scripts/soak-planner.ps1 -Mode both -Requests 240 -Concurrency 24 -PersistenceBackend sqlite -SqlitePath .\.mars_mcp_runtime.soak.sqlite3 -OutputPath .\data\processed\planner-soak-local.json -ResetRuntimeState
```

The script runs the same deterministic scenario set in sync and async planner modes (`MARS_MCP_PLANNER_ASYNC`) and prints p50/p95/p99 latency, throughput, failure rate, degraded-plan rate, and runtime counter deltas.

CI runs the same soak parameter envelope in `.github/workflows/ci.yml` (job: `planner-soak-trend`) and uploads `planner-soak-ci` to support historical trend inspection.

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
- `mars.telemetry.overview` also includes `plan_runtime` counters (`sync_calls`, `async_calls`) for `mars.plan` runtime-mode soak tracking.

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
- Overview panel includes `Plan Sync Calls`, `Plan Async Calls`, and `Plan Runtime Ratio` (`sync% / async%`) derived from `plan_runtime` counters.
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

### Phase 10c — Async negotiation rollout

- Step 1 ✅ — Optional async negotiation path added behind `MARS_LLM_ORCHESTRATOR_ASYNC=true`.
- Default behavior remains unchanged (sync negotiation) unless the async flag is explicitly enabled.
- Async runtime errors fall back to sync negotiation path, preserving deterministic planner fallback behavior.
- Async dispatch and fallback behavior is covered in `tests/unit/test_negotiator.py`.
- Step 2 ✅ — Optional async planner path in MCP runtime is now available behind `MARS_MCP_PLANNER_ASYNC=true`.
- With the flag enabled, `mars.plan` dispatches to `CentralPlanner.plan_async()` and keeps timeout/idempotency/error contracts unchanged.
- Step 3 ✅ — OpenAI dual-path negotiation calls are now enabled in `MultiAgentNegotiator`:
  - Primary path: OpenAI Responses API (`client.responses.create`) for sync and async modes.
  - Fallback path: OpenAI Chat Completions (`client.chat.completions.create`) when Responses API is unavailable, returns empty output, or errors.
  - Runtime toggle: `MARS_LLM_OPENAI_USE_RESPONSES=true|false` (default `true`) to force legacy Chat Completions when needed.
  - Migration reference: [OpenAI guide: Migrate to Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)

### Multi-agent architecture

> Full analysis and improvement backlog: [issue #13](https://github.com/nini1972/mars-colonization-specialist/issues/13)

**Current architecture:** 4 domain specialists (ECLSS, Power, ISRU, HabitatThermodynamics) + 5 orchestrators (CentralPlanner, MultiAgentNegotiator, CouplingChecker, MissionPhaseStateMachine, GovernanceGate). Communication is entirely synchronous direct function calls.

**Improvement tracks:**

| Track | Description | Status |
| ----- | ----------- | ------ |
| A | Parallel specialist execution — ECLSS, ISRU, Thermal run concurrently via `ThreadPoolExecutor`; Power waits on their outputs | ✅ Done (112 tests passing) |
| B | Smarter negotiation fallback — conflict-aware knob selection instead of fixed 20% ISRU reduction | ✅ Done (116 tests passing) |
| C | Specialist capability protocol — `capabilities()` method on each specialist so the negotiator can query trade-off preferences; `SpecialistRegistry` for dynamic add/remove | ✅ Done (159 tests passing) |
| D | Persistent negotiation memory — resolved conflict patterns persisted to SQLite for cross-mission reuse | ✅ Done (125 tests passing) |
| E | Agent health panel in dashboard — per-specialist call count, avg latency, and last outcome fragment | ✅ Done (133 tests passing) |
| F | Specialist health and graceful degradation — isolate per-specialist failures, emit degraded plans, and surface fault telemetry in MCP and dashboard | ✅ Done (170 tests passing) |
