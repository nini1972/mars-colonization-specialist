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
