"""Unit tests for the HabitatThermodynamicsSpecialist and thermal coupling."""

from datetime import date

from mars_agent.knowledge.models import TrustTier
from mars_agent.orchestration import CouplingChecker
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.specialists import (
    HabitatThermodynamicsSpecialist,
    ModuleInput,
    ModuleRequest,
    Subsystem,
    UncertaintyBounds,
)
from mars_agent.specialists.eclss import ECLSSSpecialist
from mars_agent.specialists.isru import ISRUSpecialist
from mars_agent.specialists.power import PowerSpecialist


def _evidence() -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            source_id="nasa-std",
            doc_id="nasa-thermal-001",
            title="Habitat thermodynamics baseline evidence",
            url="https://example.org/nasa-thermal-001",
            published_on=date(2026, 3, 1),
            tier=TrustTier.AGENCY_STANDARD,
            relevance_score=0.92,
        ),
    )


def _thermal_request(
    crew: float,
    solar_flux: float,
    area: float,
    r_value: float,
    budget_kw: float,
) -> ModuleRequest:
    return ModuleRequest(
        mission="mars-thermal-test",
        subsystem=Subsystem.HABITAT_THERMODYNAMICS,
        evidence=_evidence(),
        desired_confidence=0.9,
        inputs=(
            ModuleInput("crew_count", UncertaintyBounds(crew, crew, crew, "crew")),
            ModuleInput(
                "solar_flux_w_m2",
                UncertaintyBounds(solar_flux, solar_flux * 0.9, solar_flux * 1.1, "W/m²"),
            ),
            ModuleInput("habitat_area_m2", UncertaintyBounds(area, area, area, "m²")),
            ModuleInput(
                "insulation_r_value",
                UncertaintyBounds(r_value, r_value, r_value, "m²·K/W"),
            ),
            ModuleInput(
                "thermal_power_budget_kw",
                UncertaintyBounds(budget_kw, budget_kw * 0.9, budget_kw * 1.1, "kW"),
            ),
        ),
    )


def _coupling_eclss_request(evidence: tuple[EvidenceReference, ...]) -> ModuleRequest:
    return ModuleRequest(
        mission="mars-thermal-coupling",
        subsystem=Subsystem.ECLSS,
        evidence=evidence,
        inputs=(
            ModuleInput("crew_count", UncertaintyBounds(4.0, 4.0, 4.0, "crew")),
            ModuleInput("o2_demand_per_crew_kg_day", UncertaintyBounds(0.84, 0.8, 0.9, "kg/day")),
            ModuleInput(
                "water_demand_per_crew_l_day",
                UncertaintyBounds(18.0, 16.0, 20.0, "L/day"),
            ),
            ModuleInput("recycle_efficiency", UncertaintyBounds(0.87, 0.82, 0.9, "ratio")),
            ModuleInput("thermal_load_kw", UncertaintyBounds(5.0, 4.5, 5.5, "kW")),
            ModuleInput("o2_partial_pressure_kpa", UncertaintyBounds(20.8, 20.0, 21.5, "kPa")),
        ),
    )


def _coupling_isru_request(evidence: tuple[EvidenceReference, ...]) -> ModuleRequest:
    return ModuleRequest(
        mission="mars-thermal-coupling",
        subsystem=Subsystem.ISRU,
        evidence=evidence,
        inputs=(
            ModuleInput("regolith_feed_kg_day", UncertaintyBounds(10.0, 9.0, 11.0, "kg/day")),
            ModuleInput("ice_grade_fraction", UncertaintyBounds(0.24, 0.20, 0.28, "ratio")),
            ModuleInput("reactor_efficiency", UncertaintyBounds(0.74, 0.68, 0.80, "ratio")),
            ModuleInput(
                "electrolysis_kwh_per_kg_o2",
                UncertaintyBounds(44.0, 40.0, 48.0, "kWh/kg"),
            ),
            ModuleInput("available_power_kw", UncertaintyBounds(20.0, 18.0, 22.0, "kW")),
        ),
    )


def _coupling_power_request(evidence: tuple[EvidenceReference, ...]) -> ModuleRequest:
    return ModuleRequest(
        mission="mars-thermal-coupling",
        subsystem=Subsystem.POWER,
        evidence=evidence,
        inputs=(
            ModuleInput("solar_generation_kw", UncertaintyBounds(20.0, 18.0, 22.0, "kW")),
            ModuleInput("battery_capacity_kwh", UncertaintyBounds(400.0, 380.0, 420.0, "kWh")),
            ModuleInput("critical_load_kw", UncertaintyBounds(12.0, 11.0, 13.0, "kW")),
            ModuleInput("dust_degradation_fraction", UncertaintyBounds(0.0, 0.0, 0.0, "ratio")),
            ModuleInput("hours_without_sun", UncertaintyBounds(10.0, 9.0, 11.0, "h")),
        ),
    )


def test_gate_passes_nominal_underheat_conditions() -> None:
    """HVAC heats undercooled habitat to setpoint; gate accepts within-budget demand."""
    # crew=4, solar_flux=400, area=100, r=1.5
    # t_passive ≈ -41.7 °C  →  underheat branch  →  internal_temp = 22 °C
    # demand ≈ 5.0 kW  <  budget 20.0 kW  →  gate accepts
    specialist = HabitatThermodynamicsSpecialist()
    result = specialist.analyze(_thermal_request(4.0, 400.0, 100.0, 1.5, 20.0))

    assert result.gate.accepted
    internal_temp = result.get_metric("internal_temp_c").value.mean
    demand_kw = result.get_metric("thermal_power_demand_kw").value.mean
    efficiency = result.get_metric("temp_regulation_efficiency").value.mean
    assert 18.0 <= internal_temp <= 26.0
    assert demand_kw >= 0.0
    assert 0.0 <= efficiency <= 1.0


def test_gate_rejects_overtemperature() -> None:
    """High solar gain pushes passive temperature above comfort ceiling; gate rejects."""
    # crew=2, solar_flux=800, area=200, r=5.0
    # t_passive ≈ 34 °C  →  overheat branch  →  internal_temp = 34 °C  >  26 °C limit
    specialist = HabitatThermodynamicsSpecialist()
    result = specialist.analyze(_thermal_request(2.0, 800.0, 200.0, 5.0, 5.0))

    assert not result.gate.accepted
    internal_temp = result.get_metric("internal_temp_c").value.mean
    assert internal_temp > 26.0
    violation_ids = {v.constraint_id for v in result.gate.violations}
    assert "habitat_thermal.temp.max" in violation_ids


def test_thermal_coupling_shortfall_identified() -> None:
    """Adding thermal demand to eclipse-constrained power budget triggers HIGH conflict."""
    evidence = _evidence()

    eclss = ECLSSSpecialist().analyze(_coupling_eclss_request(evidence))
    isru = ISRUSpecialist().analyze(_coupling_isru_request(evidence))
    power = PowerSpecialist().analyze(_coupling_power_request(evidence))

    # crew=2, solar_flux=50, area=80, r=0.5 → t_passive ≈ -58 °C → demand_kw ≈ 15 kW
    # combined (eclss≈6.2 + isru≈6.5 + thermal≈15) ≈ 28 > effective≈20 kW → thermal shortfall
    thermal = HabitatThermodynamicsSpecialist().analyze(
        _thermal_request(2.0, 50.0, 80.0, 0.5, 25.0)
    )

    conflicts = CouplingChecker().evaluate(eclss=eclss, isru=isru, power=power, thermal=thermal)

    conflict_ids = {c.conflict_id for c in conflicts}
    assert "coupling.power_balance.thermal_shortfall" in conflict_ids

    thermal_conflict = next(
        c for c in conflicts if c.conflict_id == "coupling.power_balance.thermal_shortfall"
    )
    assert thermal_conflict.mitigations
    assert Subsystem.HABITAT_THERMODYNAMICS in thermal_conflict.impacted_subsystems
