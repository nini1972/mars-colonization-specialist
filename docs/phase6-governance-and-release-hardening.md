# Phase 6 Governance and Release Hardening

## Delivered components

- Hard governance gate enforcing provenance presence and safety acceptance.
- Audit trail exporter producing deterministic JSON review records.
- Benchmark harness with NASA/ESA-aligned reference deltas.
- Release hardening manager for quarterly packs and hotfix bulletins.
- Composed `GovernanceWorkflow` that runs governance review, benchmarking, audit record construction, and release finalization in one deterministic path.
- Deterministic release bundle export containing manifest JSON, bulletin markdown, and audit JSON under a single output directory.
- Operator-facing `mars.release` MCP tool that wraps the composed workflow and can export the deterministic bundle in one call.

## Governance guarantees

- No release finalization when governance checks fail.
- No release finalization when benchmark deltas exceed tolerances.
- Audit output captures decisions, evidence, scenario outcomes, and benchmark deltas.

## Notes

- Benchmark references are configurable and include source provenance metadata.
- Release bulletins are generated as reproducible markdown summaries.
- When `GovernanceWorkflow.finalize_quarterly()` or `finalize_hotfix()` receives an `audit_path`, it exports the audit JSON before release hardening checks raise, so blocked releases still leave behind review evidence.
- `GovernanceWorkflow.export_release_bundle()` writes release artifacts using filenames derived from `{version}-{release_type}`, preserving deterministic artifact naming for operator review and CI archiving.
- `mars.release` accepts a shared payload for quarterly and hotfix release lanes (`release_type`, `version`, `changed_doc_ids`, `summary`) and optionally persists manifest, bulletin, and audit artifacts under `output_dir`.
