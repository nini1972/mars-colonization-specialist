Title

Phase 9A: Add SQLite runtime persistence with backend contract and degraded-mode handling
Summary

Implements runtime-only persistence for MCP transport state with SQLite-first support while preserving existing Phase 8 success and ToolError contracts.
Adds strict backend selection contract and startup schema version guard.
Adds degraded-mode behavior for init failure, mid-run unavailability, and corruption fallback policy.
Adds deterministic ordering for idempotency eviction and replay behavior.
Documents the new runtime persistence contract and operational env vars.
Change summary by file

persistence.py
New SQLite persistence backend for runtime state.
Defines schema_version = 1 in metadata and enforces startup version guard.
Persists and restores:
completed idempotency requests
in-flight requests
plan correlation map
simulation correlation map
Adds typed persistence error classes for unavailable vs corruption scenarios.
Uses deterministic ordering in reads/writes.
server.py
Adds backend contract env vars:
MARS_MCP_PERSISTENCE_BACKEND (memory|sqlite)
MARS_MCP_PERSISTENCE_SQLITE_PATH
MARS_MCP_PERSISTENCE_FALLBACK_ON_CORRUPTION
Defaults missing backend value to memory; invalid values raise startup error.
Initializes persistence backend at startup and restores runtime snapshot when SQLite is active.
Persists snapshot on idempotency state transitions.
Implements degraded-mode policy:
SQLite init failure falls back to memory
SQLite unavailable mid-run fails fast as invalid_request
SQLite corruption fails fast unless explicit fallback is enabled
Adds deterministic idempotency eviction tie-break using completed_at_monotonic then request_id.
test_mcp_persistence.py
New persistence unit tests:
round-trip snapshot persistence
schema_version guard rejection
corruption detection on invalid DB file
test_mcp_server_transport.py
Adds transport/runtime behavior tests for:
deterministic eviction tie-break
backend config default and invalid-value validation
replay across restart using SQLite backend
mid-run SQLite unavailability returning invalid_request
corruption fallback behavior when explicitly enabled
README.md
Adds Phase 9A runtime persistence section:
backend selection contract
SQLite path config
schema_version metadata/version guard
degraded-mode policy
deterministic ordering notes
Validation commands and key results

..venv\Scripts\python -m ruff check src tests
Passed
..venv\Scripts\python -m mypy src tests
Passed (no issues found in 67 source files)
..venv\Scripts\python -m pytest test_mcp_server_transport.py test_mcp_persistence.py
Passed (28 passed)
..venv\Scripts\python -m pytest
Passed (62 passed)
Codacy per-file analysis run after each edited file
Executed for:
persistence.py
server.py
test_mcp_persistence.py
test_mcp_server_transport.py
README.md (no supported analyzers for markdown-only scan)
No blocking issues from Codacy analyzers used in this workflow.
Residual risks and deferred items

Optional internal metrics persistence stretch was intentionally not implemented in this commit.
Hosted CI status is not executed from here; local gates are green and ready for push/PR checks.
If you want, I can also draft a shorter PR description variant optimized for GitHub’s default template.

### Still to do: 
Perfect. Here is a separate, paste-ready companion issue for the optional stretch.

Title
Phase 9A Optional Stretch: Internal Metrics Persistence (Non-Blocking)

Issue body
Summary
Track the optional Phase 9A stretch to persist lightweight internal metrics counters, without changing any external MCP contracts. This issue is intentionally non-blocking and separate from Phase 9B.

Context
Phase 9A core runtime persistence is complete. This follow-up is only for internal operational continuity of metrics across process restarts.

Goal
Persist internal runtime metrics counters safely while preserving all existing public behavior.

In scope

Persist lightweight internal counters used for runtime observability
Restore counters on process startup
Keep deterministic behavior for metrics read and write
Add tests for persistence and restart continuity
Document behavior and guardrails
Out of scope

Any change to MCP success payload shape
Any change to ToolError payload shape
Any change to stderr log schema
Dashboard and operator workflow implementation (covered by Phase 9B)
Hard safety constraints

No change to existing observability contracts
Internal-only usage
No stderr log shape changes
Implementation tasks

Define persisted metrics snapshot model and storage mapping
Add runtime load and save hooks for internal metrics counters
Add failure handling aligned with existing degraded-mode policy
Add regression tests proving no public contract drift
Update documentation for internal-only metrics persistence behavior
Acceptance criteria

Metrics counters survive process restart when persistence backend is available
Public MCP success payloads remain byte-for-byte contract-compatible
ToolError payload contract remains unchanged
Structured stderr log keys and shape remain unchanged
Existing transport, auth, and observability regression tests remain green
New targeted tests for metrics persistence and restart behavior pass in CI
Documentation clearly states this feature is internal-only and non-blocking
Validation plan

Run lint, type-check, and full test suite
Run targeted tests for metrics snapshot persistence and restore
Run regression tests validating no payload or log-shape contract changes
Confirm hosted CI required checks are green
Priority and planning

Priority: Low
Blocking status: Non-blocking
Recommended milestone: After Phase 9B kickoff or as spare-capacity work
If you want, I can also provide a shorter version formatted for quick issue creation in the GitHub web UI.

