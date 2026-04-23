m turning the architecture gap into an implementation sequence, anchored to the current planner/session code instead of a generic roadmap. I’ll re-check the negotiation protocol, planner session flow, and specialist contracts, then lay out concrete Phase 11 Step 6+ slices with target files, behavior, and validation.

Reviewed 4 files and planned response steps
I converted the current bounded negotiation architecture into a concrete Step 6+ delivery sequence, anchored to the existing planner-owned session flow, specialist contracts, and transcript infrastructure. I’m marking the planning task complete now, then I’ll give you the actionable implementation plan.

No, we should not treat Phase 11 as finished. The current system has a solid bounded negotiation core, but the decisive gap is still that the planner owns collection, sequencing, and closure in planner.py:852, while the protocol in negotiation_protocol.py:1 is still an in-process transcript model rather than an agent-to-agent runtime. The right Step 6+ plan is to move in small slices from planner-owned bounded messaging to router-owned bounded peer messaging without breaking the current planner and MCP contracts.

Target
The concrete completion target for Phase 11 should be:

Specialists exchange negotiation envelopes through a router or mailbox layer rather than through planner-owned collection helpers.
The planner becomes a supervisor that opens the session, enforces bounds, and commits the final result, but does not directly gather every proposal and review.
Multi-round convergence is explicit and deterministic.
Telemetry, replay, and dashboard surfaces continue to work against the new runtime.
I would not jump straight to an external bus. The next milestone should be a true in-process peer message runtime first. External transport can be a later transport adapter.

Step 6
Phase 11 Step 6 should introduce a negotiation router and mailboxes.

Scope:

Add deterministic envelope and mailbox primitives on top of the current protocol layer.
Replace the direct planner collection pattern in planner.py:664 and planner.py:699 with routed message delivery.
Keep current one-round behavior initially so the behavioral change is infrastructural, not algorithmic.
Code surfaces:

negotiation_protocol.py
New file: src/mars_agent/orchestration/negotiation_runtime.py
registry.py
planner.py
Specialist files:
eclss.py
isru.py
power.py
habitat_thermodynamics.py
Design:

Add a NegotiationEnvelope with sender, recipients, kind, payload, round index, and causal parent sequence.
Add NegotiationMailbox per participant plus a NegotiationRouter that delivers envelopes deterministically in sorted order.
Extend the specialist protocol so specialists can handle negotiation envelopes, not just expose top-level proposal and review methods.
Keep compatibility by letting the router internally call existing propose and review behavior first.
Acceptance criteria:

The planner no longer calls direct collection helpers as the primary orchestration primitive.
A test transcript still shows the same first-round semantics, but generated through router delivery rather than planner aggregation.
Public mars.plan and MCP response schemas remain unchanged.
Step 7
Phase 11 Step 7 should add bounded autonomous rounds and convergence rules.

Scope:

Move from one request wave plus one review wave into an actual bounded round loop.
Let specialists respond to incoming peer messages with new outgoing envelopes.
Add deterministic stop conditions.
Code surfaces:

planner.py
New or expanded runtime state in src/mars_agent/orchestration/negotiation_runtime.py
Planner settings in models.py
New settings:

negotiation_max_rounds
negotiation_convergence_epsilon
negotiation_deadlock_policy
negotiation_max_messages_per_round
Behavior:

The planner opens the session and seeds conflict-detected and proposal-requested control messages.
The router executes bounded rounds until one of:
convergence reached
no new envelopes emitted
max rounds hit
deadlock policy triggered
The LLM negotiator in negotiator.py:430 becomes an adjudicator or tie-breaker when bounded peer exchange does not converge cleanly.
Deterministic fallback remains the final safety net.
Acceptance criteria:

At least one unit test demonstrates a two-round negotiation where round 2 changes round 1.
At least one unit test demonstrates deterministic halt on no-progress or max-round exhaustion.
Fallback and memory replay still work with the new round engine.
Step 8
Phase 11 Step 8 should make the planner supervisory only.

This is the point where the architecture becomes meaningfully peer-to-peer inside the process.

Scope:

Remove planner-owned authorship of intermediate specialist exchanges.
Keep planner-authored control messages only for session start, policy bounds, and session close.
Let specialists produce peer-addressed envelopes directly.
Code surfaces:

planner.py:963
negotiation_protocol.py
Specialist handlers in the four specialist files
registry.py
Behavior:

Specialist A sends a proposal to the impacted peers and the planner supervisor.
Specialist B reviews or counters by sending back to Specialist A and optionally copying the planner.
The planner does not synthesize proposal_submitted, proposal_reviewed, or counter_proposal_submitted events on behalf of specialists; it only records routed envelopes.
The planner only steps in for convergence checks, safety bounds, and final resolution.
Acceptance criteria:

Transcript messages in tests clearly show specialist-to-specialist traffic, not only specialist-to-planner traffic.
The planner still closes the session deterministically.
Existing dashboard negotiation transcript views still render correctly.
Step 9
Phase 11 Step 9 should persist live session state, not just final outcomes.

Right now you persist learned outcomes through negotiation memory, but not the active session runtime. For a real message-driven system, that is the next necessary hardening step.

Scope:

Persist live negotiation session state, mailboxes, round state, and transcript snapshots.
Support deterministic resume after interruption.
Distinguish active-session persistence from conflict-outcome memory.
Code surfaces:

New file: src/mars_agent/orchestration/negotiation_session_store.py
planner.py
Potential dashboard and telemetry extensions:
telemetry.py
dashboard_app.py
Acceptance criteria:

A paused session can be rehydrated and resumed deterministically.
Transcript sequence numbers and message ordering remain stable after resume.
Dashboard or telemetry can distinguish completed, resumed, and aborted sessions.
Step 10
Phase 11 Step 10 should be a transport abstraction, not necessarily a distributed deployment yet.

This is optional for the current repo, but it is the right extension point if you eventually want actual cross-process peers.

Scope:

Separate the routing API from the in-process delivery engine.
Add a transport adapter interface so the same runtime can later run over MCP, HTTP, or a queue.
Keep the default implementation local and deterministic.
Code surfaces:

src/mars_agent/orchestration/negotiation_runtime.py
New adapter layer, for example src/mars_agent/orchestration/negotiation_transport.py
Acceptance criteria:

In-process delivery remains the default.
Router logic depends on a transport interface, not directly on local mailbox mutation.
No public planner contract changes.
Implementation Order
If I were sequencing this into actual PR-sized slices, I would do it this way:

Step 6 PR: router and mailboxes, but preserve current one-round behavior.
Step 7 PR: bounded multi-round convergence engine.
Step 8 PR: planner becomes supervisor-only for specialist exchanges.
Step 9 PR: active-session persistence and resume.
Step 10 PR: optional transport abstraction.
That order minimizes regression risk because each slice changes only one dimension:

runtime structure
round semantics
ownership model
durability
deployment model
Validation
Each step should carry focused validation, not just broad regression runs.

Step 6 validation:

New unit tests for envelope routing and deterministic mailbox ordering.
Planner transcript tests proving no contract drift in message sequence.
Existing negotiation dashboard tests should stay green.
Step 7 validation:

New planner tests for convergence, max-round halt, and deadlock handling.
New negotiator prompt tests confirming peer messages and round summaries are represented correctly in negotiator.py:430.
Step 8 validation:

Transcript tests must prove specialist-to-specialist messages exist.
Fallback path tests must prove planner no longer fabricates peer exchanges.
Step 9 validation:

Persistence round-trip tests for active sessions.
Resume determinism tests.
Telemetry and dashboard tests for resumed sessions.
Core recurring checks:

pytest slices for planner, negotiator, negotiation protocol, and dashboard telemetry
ruff
mypy
MCP telemetry tests where session publication shape matters
Definition Of Done
For the peer-to-peer mission, I would define done as:

Specialists exchange bounded envelopes through a router.
The planner supervises but does not manually aggregate proposal and review stages.
There is explicit bounded multi-round convergence.
Live session state is durable and resumable.
Observability still shows the true routed message flow.
The most important judgment call is this: Step 6 should not try to solve distributed deployment. It should only remove planner-owned message aggregation and introduce a real routed negotiation runtime inside the process. That is the smallest step that materially advances the architecture.

If you want, I can turn this into a repo-specific execution checklist for Step 6 only, with exact functions to replace in planner.py and the first test files to add.

’m narrowing this to a Step 6 execution checklist tied to actual functions and test files in this repo, so the output is implementable rather than architectural prose. I’ll confirm the current planner/session helpers and the existing negotiation test surfaces, then map Step 6 into ordered edit tasks and validation commands.

I turned the current Phase 11 architecture into a repo-specific Step 6 execution checklist, tied to the actual planner/session helpers, specialist contracts, and existing unit-test surfaces so you can implement it in PR-sized slices.

Here is the repo-specific execution checklist for Phase 11 Step 6 only: introduce a real in-process negotiation router and mailbox layer, while preserving the current one-round behavior and public planner/MCP contracts.

Step 6 Goal
Replace planner-owned proposal/review aggregation with router-owned message delivery.

Current replacement points:

Proposal collection in planner.py:647
Peer review collection in planner.py:677
Counter-proposal materialization in planner.py:714
Sync session execution in planner.py:956
Async session execution in planner.py:1115
Checklist

Extend the protocol model in negotiation_protocol.py.
Add:
NegotiationEnvelope
NegotiationMailbox
NegotiationParticipantState
NegotiationDeliveryReceipt
optional new NegotiationMessageKind control kinds only if needed for routing, not for business semantics
Constraints:

deterministic ordering only
canonical payload serialization remains stable
no threads, background tasks, or external transport yet
Add a new runtime module.
Create:
src/mars_agent/orchestration/negotiation_runtime.py
Implement:

NegotiationRouter
NegotiationRuntime
seed_initial_messages(session, conflicts, recipients)
deliver_round(...)
drain_mailboxes(...)
build_transcript_messages(...)
Required behavior:

route envelopes by recipient
preserve stable ordering by round, sender, recipient, kind, sequence
keep one bounded delivery cycle for Step 6
expose outputs the planner can still feed into MultiAgentNegotiator
Upgrade specialist interaction from direct collection to message handling.
Start in registry.py.
Add protocol methods:

handle_negotiation_envelope(...) -> tuple[NegotiationEnvelope, ...]
keep propose_tradeoffs() and review_peer_proposals() temporarily as compatibility helpers underneath
Then update the four specialists:

eclss.py
isru.py
power.py
habitat_thermodynamics.py
For Step 6, each specialist only needs to handle:

proposal_requested
peer proposal review input
emit proposal or review envelopes deterministically
Do not add open-ended loops yet.

Refactor planner session execution to use the runtime.
Edit:
planner.py:956
planner.py:1115
Replace this pattern:

planner collects proposals
planner collects reviews
planner synthesizes counter-proposals
With this pattern:

planner opens NegotiationSession
planner instantiates NegotiationRuntime
planner seeds initial control envelopes
runtime delivers a single bounded round
planner reads normalized outputs from runtime
planner still invokes MultiAgentNegotiator or fallback exactly as today
Keep these unchanged in Step 6:

planner remains session owner
planner remains final closer of the session
NegotiationDecision stays the resolution object
mars.plan response shape stays unchanged
Preserve transcript compatibility during migration.
When routing envelopes, continue emitting equivalent transcript entries so existing dashboard and telemetry surfaces stay valid.
Required transcript continuity:

session_started
conflict_detected
proposal_requested
proposal_submitted
proposal_reviewed
counter_proposal_submitted if generated through the runtime
proposal_accepted
session_closed
That means the runtime becomes the producer of intermediate message events, not the planner’s manual loops.

Keep the LLM negotiator unchanged at its boundary.
Do not redesign negotiator.py:430 in Step 6.
Only adapt the inputs it receives:

proposals
reviews
counter proposals
Those should now come from the runtime output rather than planner-owned collectors.

Add the new unit-test surface first.
Primary new test file:
tests/unit/test_negotiation_runtime.py
Add tests for:

deterministic envelope routing
mailbox delivery order
single-round delivery from seeded proposal_requested
runtime output normalization into proposals/reviews/counter-proposals
transcript message stability under routing
Update existing negotiation transcript tests, don’t replace them.
Extend:
test_negotiation_protocol.py
Goal:

same visible transcript semantics
different implementation path underneath
You should still verify the existing ordered kinds, but now assert they were emitted through runtime-driven routing rather than planner-local aggregation assumptions.

Add planner integration tests for the migration boundary.
Extend:
test_central_planner.py
Add tests for:

_run_negotiation_session() using runtime output
_run_negotiation_session_async() using runtime output
planner still returns identical decision shape
fallback path still works when runtime produces no acceptable proposal
memory replay still short-circuits after session open, before extra delivery work
Keep dashboard and telemetry regression checks in scope.
Because Step 6 changes where transcript events come from, run existing negotiation observability tests, especially:
test_dashboard_app.py
test_correlation_fragment_live.py if any negotiation telemetry assumptions overlap
telemetry-related negotiation tests if they exist alongside planner tests
Exact edit order

Add runtime types to negotiation_protocol.py
Create src/mars_agent/orchestration/negotiation_runtime.py
Extend the specialist protocol in registry.py
Implement minimal specialist envelope handlers in the four specialist files
Refactor sync planner path in planner.py:956
Refactor async planner path in planner.py:1115
Add tests/unit/test_negotiation_runtime.py
Update test_negotiation_protocol.py
Update test_central_planner.py
Update docs only after tests pass
Acceptance criteria
Step 6 is done when all of these are true:

Planner no longer directly uses _collect_specialist_proposals(), _collect_peer_reviews(), and _collect_counter_proposals() as the primary negotiation engine.
Specialists respond through a routed envelope mechanism.
Sync and async negotiation session paths still produce deterministic transcript sequences.
MultiAgentNegotiator still receives normalized proposal/review/counter data with no public contract drift.
mars.plan and MCP response schemas remain unchanged.
Dashboard negotiation transcript rendering still works unchanged.
Validation checklist
Run at minimum:

If you want the cleanest implementation path, the next concrete move is:

create negotiation_runtime.py
redirect only _run_negotiation_session() to it first
keep async as a thin wrapper until sync behavior is stable
If you want, I can take this checklist and start implementing Step 6 in the repo now.

