# Phase 6 Governance and Release Hardening

## Delivered components

- Hard governance gate enforcing provenance presence and safety acceptance.
- Audit trail exporter producing deterministic JSON review records.
- Benchmark harness with NASA/ESA-aligned reference deltas.
- Release hardening manager for quarterly packs and hotfix bulletins.
- Composed `GovernanceWorkflow` that runs governance review, benchmarking, audit record construction, and release finalization in one deterministic path.
- Deterministic release bundle export containing manifest JSON, bulletin markdown, and audit JSON under a single output directory.
- Operator-facing `mars.release` MCP tool that wraps the composed workflow and can export the deterministic bundle in one call.
- Named benchmark policy metadata on every benchmark run (`profile`, `policy_version`, `policy_source`) instead of anonymous default references.
- Hardened release manifest metadata including `governance_policy_version`, `benchmark_profile`, `benchmark_policy_version`, `rationale_type`, and `artifact_provenance`.
- Config-backed benchmark policy profiles in `configs/knowledge_release_policy.toml`, with `BenchmarkHarness.from_policy_profile()` and MCP-level `benchmark_profile` selection on both `mars.benchmark` and `mars.release`.
- Operator-facing `mars.benchmark.profiles` discovery tool that lists the configured default profile plus every available benchmark profile, version, source, and covered metric set.
- Visual Mission Ops dashboard panel for the same profile catalog, exposed as `/dashboard/fragments/benchmark-profiles` and rendered as an operator-readable profile grid.
- Mission Ops operator launch forms that dispatch selected benchmark profiles directly into `mars.benchmark` and `mars.release`, prefill the latest successful `plan_id` / `simulation_id` pair when runtime state is available, and expose bundle-path highlights inline in the dashboard response.

## Governance guarantees

- No release finalization when governance checks fail.
- No release finalization when benchmark deltas exceed tolerances.
- Audit output captures decisions, evidence, scenario outcomes, and benchmark deltas.

## Notes

- Benchmark references are configurable and include source provenance metadata.
- The default benchmark policy is now explicit and reviewable as `nasa-esa-mission-review@2026.03`, and that identity is persisted into benchmark reports, bulletins, manifests, and `mars.release` responses.
- Operators can now select a configured benchmark lane explicitly, rather than only accepting the default policy, by passing `benchmark_profile` to `mars.benchmark` or `mars.release`.
- Operators can discover those profile names directly through `mars.benchmark.profiles`, instead of relying on documentation or out-of-band knowledge.
- The dashboard now mirrors that discovery path visually, so operators can inspect default vs non-default benchmark lanes without leaving the Mission Ops UI.
- The same dashboard panel now acts as an execution surface: operators can submit benchmark runs and release bundle requests with a selected profile, and the inline result fragment shows the returned request ID plus the full runtime payload for audit or follow-up triage.
- Release bulletins are generated as reproducible markdown summaries.
- When `GovernanceWorkflow.finalize_quarterly()` or `finalize_hotfix()` receives an `audit_path`, it exports the audit JSON before release hardening checks raise, so blocked releases still leave behind review evidence.
- `GovernanceWorkflow.export_release_bundle()` writes release artifacts using filenames derived from `{version}-{release_type}`, preserving deterministic artifact naming for operator review and CI archiving.
- `mars.release` accepts a shared payload for quarterly and hotfix release lanes (`release_type`, `version`, `changed_doc_ids`, `summary`) and optionally persists manifest, bulletin, and audit artifacts under `output_dir`.
- Release manifests now capture both policy versions and deterministic provenance hints, so archived bundles can be traced back to the governance policy file and benchmark source bundle that gated the release.
