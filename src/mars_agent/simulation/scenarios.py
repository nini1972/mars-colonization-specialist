"""Scenario suite for Phase 5 simulation runs."""

from __future__ import annotations

from mars_agent.simulation.ir import ScenarioSpec


def default_scenarios() -> tuple[ScenarioSpec, ...]:
    """Return required Phase 5 scenarios for validation and repair loops."""

    return (
        ScenarioSpec(
            name="nominal",
            description="Nominal operations under expected environmental conditions.",
            modifiers={},
        ),
        ScenarioSpec(
            name="dust_storm",
            description="Dust storm degrades solar generation and storage resilience.",
            modifiers={
                "effective_generation_kw": 0.65,
                "storage_cover_hours": 0.9,
            },
        ),
        ScenarioSpec(
            name="equipment_failure",
            description="ISRU equipment degradation increases processing load.",
            modifiers={
                "isru_power_demand_kw": 1.25,
                "effective_generation_kw": 0.92,
            },
        ),
        ScenarioSpec(
            name="resupply_delay",
            description="Resupply delay increases life-support overhead.",
            modifiers={
                "eclss_power_demand_kw": 1.15,
                "storage_cover_hours": 0.85,
            },
        ),
        ScenarioSpec(
            name="medical_emergency",
            description="Medical emergency spikes habitat energy requirements.",
            modifiers={
                "eclss_power_demand_kw": 1.2,
            },
        ),
    )
