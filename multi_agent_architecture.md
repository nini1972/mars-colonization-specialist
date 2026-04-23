# Multi-Agent Architecture

## Current Architecture

The system currently consists of four domain specialists and five orchestrators.

### Domain specialists

- `ECLSSSpecialist` — life support (`O₂`, water, pressure, power demand)
- `PowerSpecialist` — solar generation, battery, dust degradation
- `ISRUSpecialist` — in-situ resource production and water extraction
- `HabitatThermodynamicsSpecialist` — HVAC and thermal regulation

### Orchestrators

- `CentralPlanner` — coordinates specialists and drives the replan loop
- `MultiAgentNegotiator` — LLM-assisted or deterministic conflict resolution
- `CouplingChecker` — detects cross-domain power, ISRU, and ECLSS conflicts
- `MissionPhaseStateMachine` — manages mission phase transitions
- `GovernanceGate` — enforces safety and provenance hard gates

Communication originally started as entirely synchronous direct function calls. The negotiation stack now has deterministic internal routing, per-participant mailboxes, and bounded convergence rounds, but it still collapses into planner-supervised in-process execution rather than an open-ended peer-to-peer runtime.

## Key Gaps and Improvement Areas

| # | Gap | Impact |
| - | --- | ------ |
| 1 | Sequential specialist execution — ECLSS, ISRU, Power, and Thermal originally ran one after another with no dependency-aware parallelism | Wasted latency |
| 2 | Dumb deterministic fallback — fixed 20% ISRU reduction per retry, ignoring specialist preferences | Sub-optimal plans; may over-reduce |
| 3 | Specialists could not self-describe inputs or trade-off knobs | Negotiator had to hardcode all knobs |
| 4 | Knowledge was not wired into coordination | Evidence did not inform negotiation |
| 5 | No cross-request learning | Repeated mission profiles repeated the same mistakes |
| 6 | No agent registry | Specialists were hardcoded on `CentralPlanner` |
| 7 | No specialist health or failure isolation | One specialist failure could fail the whole plan |
| 8 | No agent monitoring in the dashboard | Hard to see which specialist was slow or failing |

## Candidate Improvement Tracks

### Track A — Parallel execution

Run the four specialists concurrently inside the replan loop where dependencies allow it. This was the lowest-risk latency win.

### Track B — Smarter negotiation fallback

Replace the fixed 20%-increment fallback with a conflict-aware strategy that chooses the most targeted knob for the active conflict set.

### Track C — Specialist capability protocol

Add a `capabilities()` method to each specialist so the negotiator can query supported inputs, outputs, and preferred compromise knobs instead of deciding top-down.

### Track D — Persistent negotiation memory

Persist resolved conflict patterns to SQLite alongside idempotency state so repeated mission profiles can reuse proven negotiation outcomes.

### Track E — Agent health panel

Add a per-specialist metrics fragment showing call count, average latency, and last outcome by reusing the existing telemetry infrastructure.

## Delivery History

The initial exploration proposed Tracks A-E. Track A was selected first because the dependency graph was already clear: ECLSS, ISRU, and Thermal could run in parallel, while Power had to wait for their outputs to compute `critical_load_kw`. That change shipped with a `ThreadPoolExecutor` and preserved the synchronous public planner contract.

### Latest Alignment Update (Phase 10c Step 3)

- Async planner soak automation is implemented and documented.
- Local harness: `scripts/soak_planner.py`
- PowerShell wrapper: `scripts/soak-planner.ps1`
- Deterministic comparison envelope: `--mode both --requests 240 --concurrency 24 --timeout-seconds 20`
- Artifact output contract: `data/processed/planner-soak-local.json` (local) and `data/processed/planner-soak-ci.json` (CI)
- CI trend job is in place in `.github/workflows/ci.yml` as `planner-soak-trend`.
- Artifact upload name: `planner-soak-ci`

Operational intent: keep sync and async planner performance comparable over time and support staged rollout decisions for `MARS_MCP_PLANNER_ASYNC=true` with artifact-backed trend evidence.

### Latest Alignment Update (Phase 11 Step 1)

- Deterministic negotiation protocol groundwork is implemented internally.
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
  - one of `proposal_submitted`, `fallback_applied`, or `memory_replayed`
  - `proposal_accepted`
  - `session_closed`
- Specialists are not yet peer-to-peer participants at this step. The planner still hosts the session and the negotiator still produces the trade-off decision.

### Latest Alignment Update (Phase 11 Step 2)

- Specialist-authored proposals are active inside the deterministic session layer.
- Each specialist can emit deterministic `TradeoffProposal` objects for relevant conflict IDs:
  - `ECLSS` -> `crew_reduction`
  - `ISRU` -> `isru_reduction_fraction`
  - `Power` -> `dust_degradation_adjustment`
  - `HabitatThermodynamics` -> `thermal_power_budget_reduction`
- `CentralPlanner` now gathers proposals from impacted specialists, records them as `proposal_submitted` transcript entries, and passes them into both:
  - `MultiAgentNegotiator._build_messages()` for LLM-assisted negotiation
  - `_conflict_aware_fallback()` for deterministic non-LLM resolution
- Proposal authorship moved away from planner-owned static logic while preserving the existing planner and MCP contracts.

### Latest Alignment Update (Phase 11 Step 3)

- Negotiation transcript observability is implemented on internal telemetry and dashboard surfaces.
- `CentralPlanner` publishes completed negotiation sessions via an observer hook without changing plan results or MCP payloads.
- MCP telemetry stores recent negotiation sessions separately from tool invocation events.
- The dashboard exposes a `Negotiation Sessions` fragment that shows:
  - recent session IDs, rounds, and mission phase
  - decision source (`llm`, `fallback`, or `memory`)
  - specialist-authored proposals
  - ordered transcript messages for the session
- This completed the observability precondition for bounded agent-to-agent message handling.

### Latest Alignment Update (Phase 11 Step 4)

- Bounded specialist-to-specialist message handling is active inside the deterministic session scheduler.
- Specialists do not run arbitrary conversations, but they now consume peer-authored proposals through `review_peer_proposals()` and emit deterministic peer review outcomes in one bounded review round.
- `CentralPlanner` records `proposal_reviewed` transcript messages after the initial `proposal_submitted` wave and before the final fallback or LLM decision.
- Peer review is threaded into both decision paths:
  - `MultiAgentNegotiator._build_messages()` receives peer review context for LLM-assisted negotiation.
  - `_conflict_aware_fallback()` only credits proposal-derived fallback deltas that were acknowledged by peer review when reviews are present.
- This means specialist-to-specialist message consumption is no longer just observable; it now influences the deterministic resolution path.

### Latest Alignment Update (Phase 11 Step 5)

- A bounded counter-proposal round is active inside the deterministic session scheduler.
- `TradeoffReviewDisposition.COUNTER` no longer stops at annotation. When a peer review supplies a suggested delta, `CentralPlanner` materializes it into a bounded second-round counter-proposal.
- `CentralPlanner` now records `counter_proposal_submitted` transcript messages after the first review wave and before the final fallback or LLM decision.
- Counter-proposals are threaded into both decision paths:
  - `MultiAgentNegotiator._build_messages()` receives an explicit counter-proposal section in the user prompt.
  - `_conflict_aware_fallback()` incorporates bounded counter-proposal deltas alongside acknowledged first-round proposals.
- Negotiation telemetry and the dashboard now expose counter-proposals as a separate bounded artifact, preserving observability of the second round.
- The system remains intentionally bounded:
  - the planner still schedules and closes the session
  - counter-proposals do not trigger unbounded conversation loops
  - the final result still collapses into the existing planner and MCP contracts
- A likely next step is selective re-review or convergence rules only when counter-proposals materially diverge from first-round proposals.

### Latest Alignment Update (Phase 11 Step 6)

- The planner-side proposal/review/counter collection helpers have been retired from the active negotiation path.
- Internal message delivery now flows through deterministic runtime primitives:
  - `NegotiationEnvelope`
  - `NegotiationMailbox`
  - `NegotiationRouter`
  - `NegotiationRuntime`
- `CentralPlanner` still opens and closes the session, but specialist-authored proposals, peer review routing, counter-proposal routing, and transcript recording now run through the dedicated in-process runtime layer instead of planner-owned collection loops.
- This moved Step 6 from planner-mediated staging to a genuine internal routed runtime without changing `mars.plan`, MCP tool payloads, or dashboard contracts.

### Latest Alignment Update (Phase 11 Step 7)

- The negotiation runtime now performs bounded multi-round convergence instead of stopping after the first counter-proposal wave.
- When a peer review emits a `COUNTER` with a suggested delta, the runtime materializes a counter-proposal, converts it into a revised proposal wave, and routes that revised proposal back through peer review.
- Convergence is deterministic and bounded:
  - the runtime stops when no new counter-proposals are emitted
  - repeated revised proposal waves are not re-expanded indefinitely
  - a small fixed review-round cap prevents unbounded loops even if future specialists add richer review logic
- Result handling is unchanged externally:
  - `MultiAgentNegotiator` still receives proposal/review/counter context through the existing parameters
  - `_conflict_aware_fallback()` still operates on the same public data shapes
  - the planner and MCP contracts remain stable while transcripts now show the extra converged proposal/review wave when applicable
