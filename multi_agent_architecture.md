Current Architecture
4 domain specialists (stateless analyzers):

ECLSSSpecialist — life support (O₂, water, pressure, power demand)
PowerSpecialist — solar generation, battery, dust degradation
ISRUSpecialist — in-situ resource production, water extraction
HabitatThermodynamicsSpecialist — HVAC, thermal regulation
5 orchestrators:

CentralPlanner — coordinates specialists sequentially, drives replan loop
MultiAgentNegotiator — LLM or deterministic conflict resolution
CouplingChecker — detects cross-domain power/ISRU/ECLSS conflicts
MissionPhaseStateMachine — manages mission phase transitions
GovernanceGate — safety/provenance hard gate
Communication today: entirely synchronous direct function calls — no messages, no events, no async. The "negotiation" is really a top-down circuit breaker on CentralPlanner, not true peer-to-peer negotiation.

Key Gaps & Improvement Areas
#	Gap	Impact
1	Sequential specialist execution — ECLSS, ISRU, Power, Thermal run one-after-another with no dependency	Wasted latency; they could run in parallel
2	Dumb deterministic fallback — fixed 20% ISRU reduction per retry, ignores specialist preferences	Sub-optimal plans; may over-reduce
3	Specialists can't self-describe — no way to ask "what inputs do you accept?" or "what can you trade off?"	Negotiator must hardcode all knobs
4	Knowledge module not wired into coordination — knowledge/ontology exists but specialists don't use it during conflict resolution	Evidence not informing negotiation
5	No cross-request learning — negotiation history discarded after each mars.plan call	Same mistakes repeated across missions
6	No agent registry — specialists hardcoded as fields on CentralPlanner	Can't add/remove specialists without code changes
7	No specialist health/failure handling — if one specialist throws, the whole plan fails	No graceful degradation
8	No agent monitoring in dashboard — dashboard shows tool invocations but not per-specialist performance	Blind to which specialist is slow or failing
Candidate Improvement Tracks
Track A — Parallel execution (quick win, self-contained)
Run the 4 specialists concurrently with asyncio.gather() inside the replan loop — low risk, significant latency improvement.

Track B — Smarter negotiation fallback (medium effort)
Replace the fixed 20%-increment fallback with a conflict-aware strategy: read which subsystems are involved in each conflict and select the most targeted knob.

Track C — Specialist capability protocol (larger)
Add a capabilities() method to each specialist returning what it can trade off — then the negotiator queries specialists for their preferred compromise instead of deciding top-down.

Track D — Persistent negotiation memory (medium)
Persist resolved conflict patterns to SQLite alongside idempotency state, so repeated mission profiles reuse proven negotiation outcomes.

Track E — Agent health panel in dashboard (UI extension)
Add a per-specialist metrics fragment showing call count, avg latency, and last outcome — using the existing telemetry infrastructure.

Which of these tracks interests you most, or should I explore a different angle entirely?



From the previous session, I had proposed 5 improvement tracks. Quick confirmation on which to start:

Track	Name	Effort
A	Parallel specialist execution	Low
B	Smarter negotiation fallback	Medium
C	Specialist capability protocol	Large
D	Persistent negotiation memory	Medium
E	Agent health panel in dashboard	Medium
Asked a question (track)

Q: Which improvement track should I start with?
A: A — Parallel specialist execution (low risk, asyncio.gather)
Created 5 todos and reviewed planner.py
Now let me check how plan() is called from the MCP tools to understand the async context:

Searched for regex and text in multiple files
The dependency graph is clear: ECLSS + ISRU + Thermal are independent → run in parallel; Power must wait for ECLSS + ISRU to compute critical_load_kw. I'll implement this with concurrent.futures.ThreadPoolExecutor, keeping the sync public API intact (it's already called from inside anyio.to_thread.run_sync).

Edited
planner.py+1-0
Now update _run_modules to fan out the three independent specialists in parallel:

Edited
planner.py+13-6
Now verify the full _run_modules region looks correct:

Read planner.py, lines 276 to 320

Good. Now run the test suite to make sure everything still passes:

Executed test suite and verified results
All 6 pass. Now run the full suite:

Executed tests in unit directory
112 tests pass. Now run Codacy analysis on the modified file:

Ran codacy_cli_analyze codacy/codacy-mcp-server (MCP Server)
Noted pre-existing complexity warning on _thermal_request
Clean — the one Lizard warning on _thermal_request (52 lines) is pre-existing and unrelated to our change.

Completed: Run test suite and verify (3/4)

Track A is done. Here's what changed in planner.py:

_run_modules now runs in two waves:

Wave 1 (parallel): ECLSSSpecialist, ISRUSpecialist, HabitatThermodynamicsSpecialist submit to a ThreadPoolExecutor(max_workers=3) simultaneously — these three are fully independent.
Wave 2 (sequential): PowerSpecialist runs after wave 1, since it needs critical_load_kw computed from the ECLSS + ISRU outputs.
No public API changes — plan() remains synchronous, all 112 tests pass, Codacy clean.

Parallelism profile: On CPython the GIL will still serialize pure-Python math tightly, but the architecture is now correct — any I/O (LLM calls, DB lookups, external sensors) a specialist adds in the future will immediately benefit from true concurrency at no extra cost.

Latest Alignment Update (Phase 10c Step 3)

- Async planner soak automation is now implemented and documented:
	- Local harness: `scripts/soak_planner.py`
	- PowerShell wrapper: `scripts/soak-planner.ps1`
	- Deterministic comparison envelope: `--mode both --requests 240 --concurrency 24 --timeout-seconds 20`
	- Artifact output contract: `data/processed/planner-soak-local.json` (local) and `data/processed/planner-soak-ci.json` (CI)
- CI trend job is now in place:
	- Workflow: `.github/workflows/ci.yml`
	- Job: `planner-soak-trend`
	- Upload artifact name: `planner-soak-ci`

Operational intent: keep sync vs async planner performance and reliability comparable over time, and support staged rollout decisions for `MARS_MCP_PLANNER_ASYNC=true` with artifact-backed trend evidence.

Latest Alignment Update (Phase 11 Step 1)

- Deterministic negotiation protocol groundwork is now implemented internally.
- New in-process protocol primitives live in `src/mars_agent/orchestration/negotiation_protocol.py`:
	- `NegotiationSession`
	- `NegotiationTranscript`
	- `NegotiationMessage`
	- `NegotiationDecision`
- `CentralPlanner` now wraps every replan attempt in a canonical transcript with stable message sequencing and sorted recipients.
- Current message flow is:
	- `session_started`
	- `conflict_detected`
	- `proposal_requested`
	- one of `proposal_submitted` | `fallback_applied` | `memory_replayed`
	- `proposal_accepted`
	- `session_closed`
- This does not yet make the specialists peer-to-peer participants. The planner still hosts the session and the negotiator still produces the tradeoff decision. The change is intentionally internal so `mars.plan` responses, telemetry contracts, idempotency semantics, and MCP payload shapes remain unchanged.
- Phase 11 Step 2 should move specialist tradeoff proposals onto this session model, with deterministic scheduling preserved across sync and async planner paths.

Claude Sonnet 4.6 • 1x