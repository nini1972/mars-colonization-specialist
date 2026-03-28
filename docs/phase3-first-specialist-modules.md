# Phase 3 First Specialist Modules

## Delivered components

- Standardized specialist module request/response contracts with uncertainty bounds.
- ECLSS specialist module for oxygen, water, thermal, and pressure checks.
- ISRU specialist module for oxygen/water production and power budget checks.
- Power specialist module for generation, storage coverage, and dust degradation checks.

## Shared contract

- `ModuleRequest`: mission, subsystem, typed inputs, evidence, desired confidence.
- `ModuleResponse`: subsystem, decision object, typed output metrics, gate result.
- `UncertaintyBounds`: mean/lower/upper/unit used consistently for all inputs and outputs.

## Notes

- All three modules use the Phase 2 global validation gate and probabilistic interface.
- Cross-module composition is validated in unit tests without schema translation adapters.
