# Reproducibility Policy

## Determinism

- Use fixed random seeds for all baseline tests.
- Document seed values in config files.
- Prohibit time-dependent behavior in deterministic tests.

## Environment consistency

- Python runtime pinned via .python-version.
- Development dependencies pinned by lock process and CI baseline.
- Local check script mirrors CI commands.

## Evidence

- Each phase report must include command outputs for lint, type check, and tests.
