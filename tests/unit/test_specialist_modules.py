from datetime import date

from mars_agent.knowledge.models import TrustTier
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.specialists import (
    ECLSSSpecialist,
    ISRUSpecialist,
    ModuleInput,
    ModuleRequest,
    PowerSpecialist,
    Subsystem,
    UncertaintyBounds,
)


def _evidence() -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            source_id="nasa-std",
            doc_id="nasa-phase3-001",
            title="Phase 3 baseline assumptions",
            url="https://example.org/nasa-phase3-001",
            published_on=date(2026, 1, 5),
            tier=TrustTier.AGENCY_STANDARD,
            relevance_score=0.91,
        ),
    )


def test_eclss_module_passes_domain_sanity_checks() -> None:
    specialist = ECLSSSpecialist()
    request = ModuleRequest(
        mission="mars-base-alpha",
        subsystem=Subsystem.ECLSS,
        evidence=_evidence(),
        inputs=(
            ModuleInput("crew_count", UncertaintyBounds(12.0, 11.0, 13.0, "crew")),
            ModuleInput(
                "o2_demand_per_crew_kg_day",
                UncertaintyBounds(0.84, 0.8, 0.9, "kg/day"),
            ),
            ModuleInput(
                "water_demand_per_crew_l_day",
                UncertaintyBounds(18.0, 16.0, 20.0, "L/day"),
            ),
            ModuleInput("recycle_efficiency", UncertaintyBounds(0.86, 0.82, 0.9, "ratio")),
            ModuleInput("thermal_load_kw", UncertaintyBounds(72.0, 65.0, 79.0, "kW")),
            ModuleInput("o2_partial_pressure_kpa", UncertaintyBounds(20.8, 20.0, 21.6, "kPa")),
        ),
    )

    result = specialist.analyze(request)

    assert result.gate.accepted
    assert result.get_metric("oxygen_generation_kg_day").value.mean > 0.0
    assert result.get_metric("water_makeup_l_day").value.mean >= 0.0


def test_isru_module_rejects_power_budget_violation() -> None:
    specialist = ISRUSpecialist()
    request = ModuleRequest(
        mission="mars-base-alpha",
        subsystem=Subsystem.ISRU,
        evidence=_evidence(),
        inputs=(
            ModuleInput("regolith_feed_kg_day", UncertaintyBounds(140.0, 130.0, 150.0, "kg/day")),
            ModuleInput("ice_grade_fraction", UncertaintyBounds(0.3, 0.25, 0.35, "ratio")),
            ModuleInput("reactor_efficiency", UncertaintyBounds(0.78, 0.72, 0.82, "ratio")),
            ModuleInput(
                "electrolysis_kwh_per_kg_o2", UncertaintyBounds(48.0, 44.0, 52.0, "kWh/kg")
            ),
            ModuleInput("available_power_kw", UncertaintyBounds(6.0, 5.5, 6.5, "kW")),
        ),
    )

    result = specialist.analyze(request)

    assert not result.gate.accepted
    assert any(item.constraint_id == "isru.power_budget.max" for item in result.gate.violations)


def test_cross_module_outputs_compose_without_schema_translation() -> None:
    eclss = ECLSSSpecialist()
    isru = ISRUSpecialist()
    power = PowerSpecialist()

    eclss_result = eclss.analyze(
        ModuleRequest(
            mission="mars-base-alpha",
            subsystem=Subsystem.ECLSS,
            evidence=_evidence(),
            inputs=(
                ModuleInput("crew_count", UncertaintyBounds(10.0, 9.0, 11.0, "crew")),
                ModuleInput(
                    "o2_demand_per_crew_kg_day",
                    UncertaintyBounds(0.84, 0.8, 0.9, "kg/day"),
                ),
                ModuleInput(
                    "water_demand_per_crew_l_day",
                    UncertaintyBounds(17.0, 16.0, 19.0, "L/day"),
                ),
                ModuleInput("recycle_efficiency", UncertaintyBounds(0.88, 0.84, 0.9, "ratio")),
                ModuleInput("thermal_load_kw", UncertaintyBounds(60.0, 56.0, 66.0, "kW")),
                ModuleInput("o2_partial_pressure_kpa", UncertaintyBounds(20.5, 19.8, 21.3, "kPa")),
            ),
        )
    )

    isru_result = isru.analyze(
        ModuleRequest(
            mission="mars-base-alpha",
            subsystem=Subsystem.ISRU,
            evidence=_evidence(),
            inputs=(
                ModuleInput("regolith_feed_kg_day", UncertaintyBounds(70.0, 65.0, 75.0, "kg/day")),
                ModuleInput("ice_grade_fraction", UncertaintyBounds(0.24, 0.2, 0.28, "ratio")),
                ModuleInput("reactor_efficiency", UncertaintyBounds(0.74, 0.68, 0.8, "ratio")),
                ModuleInput(
                    "electrolysis_kwh_per_kg_o2", UncertaintyBounds(44.0, 41.0, 48.0, "kWh/kg")
                ),
                ModuleInput("available_power_kw", UncertaintyBounds(40.0, 36.0, 44.0, "kW")),
            ),
        )
    )

    critical_load_kw = (
        eclss_result.get_metric("eclss_power_demand_kw").value.mean
        + isru_result.get_metric("isru_power_demand_kw").value.mean
    )

    power_result = power.analyze(
        ModuleRequest(
            mission="mars-base-alpha",
            subsystem=Subsystem.POWER,
            evidence=_evidence(),
            inputs=(
                ModuleInput("solar_generation_kw", UncertaintyBounds(140.0, 120.0, 160.0, "kW")),
                ModuleInput(
                    "battery_capacity_kwh",
                    UncertaintyBounds(2600.0, 2400.0, 2800.0, "kWh"),
                ),
                ModuleInput(
                    "critical_load_kw",
                    UncertaintyBounds(
                        critical_load_kw,
                        critical_load_kw * 0.9,
                        critical_load_kw * 1.1,
                        "kW",
                    ),
                ),
                ModuleInput(
                    "dust_degradation_fraction",
                    UncertaintyBounds(0.22, 0.18, 0.28, "ratio"),
                ),
                ModuleInput("hours_without_sun", UncertaintyBounds(20.0, 18.0, 22.0, "h")),
            ),
        )
    )

    assert eclss_result.gate.accepted
    assert isru_result.gate.accepted
    assert power_result.gate.accepted
    assert power_result.get_metric("load_margin_kw").value.mean > 0.0
