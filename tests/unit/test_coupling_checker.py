from datetime import date

from mars_agent.knowledge.models import TrustTier
from mars_agent.orchestration import CouplingChecker, KnowledgeContext
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.specialists import (
    ModuleInput,
    ModuleRequest,
    ModuleResponse,
    PowerSpecialist,
    Subsystem,
    UncertaintyBounds,
)
from mars_agent.specialists.eclss import ECLSSSpecialist
from mars_agent.specialists.isru import ISRUSpecialist


def _evidence() -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            source_id="nasa-std",
            doc_id="nasa-coupling-001",
            title="Coupling checker evidence",
            url="https://example.org/nasa-coupling-001",
            published_on=date(2026, 2, 11),
            tier=TrustTier.AGENCY_STANDARD,
            relevance_score=0.9,
        ),
    )


def _build_eclss() -> ModuleResponse:
    return ECLSSSpecialist().analyze(
        ModuleRequest(
            mission="mars-base-alpha",
            subsystem=Subsystem.ECLSS,
            evidence=_evidence(),
            inputs=(
                ModuleInput("crew_count", UncertaintyBounds(20.0, 19.0, 21.0, "crew")),
                ModuleInput(
                    "o2_demand_per_crew_kg_day",
                    UncertaintyBounds(0.84, 0.8, 0.9, "kg/day"),
                ),
                ModuleInput(
                    "water_demand_per_crew_l_day",
                    UncertaintyBounds(19.0, 17.0, 21.0, "L/day"),
                ),
                ModuleInput("recycle_efficiency", UncertaintyBounds(0.86, 0.82, 0.9, "ratio")),
                ModuleInput("thermal_load_kw", UncertaintyBounds(140.0, 130.0, 150.0, "kW")),
                ModuleInput("o2_partial_pressure_kpa", UncertaintyBounds(20.8, 20.1, 21.4, "kPa")),
            ),
        )
    )


def _build_isru() -> ModuleResponse:
    return ISRUSpecialist().analyze(
        ModuleRequest(
            mission="mars-base-alpha",
            subsystem=Subsystem.ISRU,
            evidence=_evidence(),
            inputs=(
                ModuleInput(
                    "regolith_feed_kg_day",
                    UncertaintyBounds(120.0, 110.0, 130.0, "kg/day"),
                ),
                ModuleInput("ice_grade_fraction", UncertaintyBounds(0.26, 0.22, 0.3, "ratio")),
                ModuleInput("reactor_efficiency", UncertaintyBounds(0.75, 0.7, 0.8, "ratio")),
                ModuleInput(
                    "electrolysis_kwh_per_kg_o2",
                    UncertaintyBounds(47.0, 43.0, 51.0, "kWh/kg"),
                ),
                ModuleInput("available_power_kw", UncertaintyBounds(20.0, 18.0, 22.0, "kW")),
            ),
        )
    )


def _build_power(critical_load: float) -> ModuleResponse:
    return PowerSpecialist().analyze(
        ModuleRequest(
            mission="mars-base-alpha",
            subsystem=Subsystem.POWER,
            evidence=_evidence(),
            inputs=(
                ModuleInput("solar_generation_kw", UncertaintyBounds(25.0, 22.0, 28.0, "kW")),
                ModuleInput("battery_capacity_kwh", UncertaintyBounds(400.0, 380.0, 420.0, "kWh")),
                ModuleInput(
                    "critical_load_kw",
                    UncertaintyBounds(
                        critical_load,
                        critical_load * 0.95,
                        critical_load * 1.05,
                        "kW",
                    ),
                ),
                ModuleInput(
                    "dust_degradation_fraction",
                    UncertaintyBounds(0.35, 0.3, 0.4, "ratio"),
                ),
                ModuleInput("hours_without_sun", UncertaintyBounds(24.0, 22.0, 26.0, "h")),
            ),
        )
    )


def test_coupling_checker_surfaces_ranked_conflicts() -> None:
    eclss = _build_eclss()
    isru = _build_isru()

    critical_load = (
        eclss.get_metric("eclss_power_demand_kw").value.mean
        + isru.get_metric("isru_power_demand_kw").value.mean
    )

    power = _build_power(critical_load)

    conflicts = CouplingChecker().evaluate(
        eclss=eclss,
        isru=isru,
        power=power,
        knowledge_context=KnowledgeContext(
            top_evidence=_evidence(),
            ontology_hints=("power:depends_on:battery",),
            trust_weight=1.1,
        ),
    )

    assert conflicts
    for conflict in conflicts:
        assert conflict.mitigations
        assert conflict.evidence_doc_ids
        assert conflict.knowledge_signals
        ranks = [item.rank for item in conflict.mitigations]
        assert ranks == sorted(ranks)
