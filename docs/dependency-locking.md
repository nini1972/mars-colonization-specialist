# Dependency Locking Strategy

## Policy

- Runtime and developer dependencies are declared in pyproject.toml.
- Lock generation is standardized through project scripts.
- Lock updates must be intentional and reviewed.

## Current baseline

- Use editable install with `[dev]` extras for Phase 0.
- Generate lock snapshot with scripts/lock.ps1 for reproducible installs.

## Review rule

- Any dependency change must include updated lock snapshot and CI pass evidence.
