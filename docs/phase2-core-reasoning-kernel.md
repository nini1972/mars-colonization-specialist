# Phase 2 Core Reasoning Kernel

## Delivered components

- Symbolic hard-constraint rules engine for physics and safety checks.
- Probabilistic forecasting interface and deterministic Monte Carlo baseline.
- Provenance-first decision object with confidence score and evidence links.
- Global validation gate contract that combines hard constraints and governance policy.

## Gate behavior

- Recommendations are rejected when hard constraints fail.
- Recommendations are rejected when evidence is missing.
- Recommendations are rejected when confidence is below policy thresholds.
- Accepted recommendations return no violations and are safe to pass downstream.

## Notes

- The baseline forecaster is intentionally simple and deterministic for repeatable CI tests.
- High-fidelity solvers can be integrated behind the same forecasting interface in later phases.
