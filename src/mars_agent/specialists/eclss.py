"""ECLSS specialist module (air, water, thermal, pressure)."""

from __future__ import annotations

from dataclasses import dataclass, field

from mars_agent.reasoning import (
    ForecastInput,
    ForecastRequest,
    GatePolicy,
    GaussianMonteCarloForecaster,
    GlobalValidationGate,
    LowerBoundConstraint,
    SymbolicRulesEngine,
    UpperBoundConstraint,
)
from mars_agent.reasoning.models import DecisionRecord
from mars_agent.specialists.contracts import (
    ModuleMetric,
    ModuleRequest,
    ModuleResponse,
    SpecialistCapability,
    Subsystem,
    TradeoffProposal,
    TradeoffKnob,
    UncertaintyBounds,
)


@dataclass(slots=True)
class ECLSSSpecialist:
    """Evaluates life-support recommendations for crew habitability."""

    forecaster: GaussianMonteCarloForecaster = field(
        default_factory=GaussianMonteCarloForecaster
    )

    def capabilities(self) -> SpecialistCapability:
        """Return this specialist's self-description for negotiation."""
        return SpecialistCapability(
            subsystem=Subsystem.ECLSS,
            accepts_inputs=(
                "crew_count",
                "o2_demand_per_crew_kg_day",
                "water_demand_per_crew_l_day",
                "recycle_efficiency",
                "thermal_load_kw",
                "o2_partial_pressure_kpa",
            ),
            produces_metrics=(
                "oxygen_generation_kg_day",
                "water_makeup_l_day",
                "eclss_power_demand_kw",
            ),
            tradeoff_knobs=(
                TradeoffKnob(
                    name="crew_reduction",
                    description="Number of crew members to stand down from active duty",
                    min_value=0.0,
                    max_value=5.0,
                    preferred_delta=1.0,
                    unit="crew",
                ),
                TradeoffKnob(
                    name="recycle_efficiency",
                    description=(
                        "Increase water/O₂ recycling efficiency to reduce ECLSS power demand"
                    ),
                    min_value=0.75,
                    max_value=0.98,
                    preferred_delta=0.02,
                    unit="ratio",
                ),
            ),
        )

    def propose_tradeoffs(self, conflict_ids: tuple[str, ...]) -> tuple[TradeoffProposal, ...]:
        relevant = tuple(sorted(cid for cid in conflict_ids if cid.startswith("coupling.power")))
        if not relevant:
            return ()
        return (
            TradeoffProposal(
                subsystem=Subsystem.ECLSS,
                knob_name="crew_reduction",
                suggested_delta=1.0,
                rationale=(
                    "ECLSS recommends standing down one crew-equivalent workload to "
                    "reduce life-support power demand during power-linked conflicts."
                ),
                conflict_ids=relevant,
            ),
        )

    def analyze(self, request: ModuleRequest) -> ModuleResponse:
        if request.subsystem is not Subsystem.ECLSS:
            raise ValueError("ECLSSSpecialist only accepts ECLSS requests")

        crew_count = request.get_input("crew_count").value.mean
        o2_demand = request.get_input("o2_demand_per_crew_kg_day").value.mean
        water_demand = request.get_input("water_demand_per_crew_l_day").value.mean
        recycle_efficiency = request.get_input("recycle_efficiency").value.mean
        thermal_load_kw = request.get_input("thermal_load_kw").value.mean
        o2_partial_pressure_kpa = request.get_input("o2_partial_pressure_kpa").value.mean

        oxygen_generation_kg_day = crew_count * o2_demand * 1.2
        water_makeup_l_day = crew_count * water_demand * max(0.0, 1.0 - recycle_efficiency)

        forecast = self.forecaster.forecast(
            ForecastRequest(
                metric="eclss_power_demand_kw",
                confidence_level=request.desired_confidence,
                inputs=(
                    ForecastInput(
                        name="thermal_load_kw",
                        mean=thermal_load_kw,
                        std_dev=max(0.1, thermal_load_kw * 0.08),
                        minimum=0.0,
                    ),
                    ForecastInput(
                        name="crew_life_support_kw",
                        mean=crew_count * 0.3,
                        std_dev=max(0.05, crew_count * 0.03),
                        minimum=0.0,
                    ),
                ),
            )
        )

        state = {
            "o2_partial_pressure_kpa": o2_partial_pressure_kpa,
            "recycle_efficiency": recycle_efficiency,
        }

        gate = GlobalValidationGate(
            rules_engine=SymbolicRulesEngine(
                constraints=(
                    LowerBoundConstraint(
                        constraint_id="eclss.o2_partial_pressure.min",
                        metric_key="o2_partial_pressure_kpa",
                        minimum=19.5,
                    ),
                    UpperBoundConstraint(
                        constraint_id="eclss.o2_partial_pressure.max",
                        metric_key="o2_partial_pressure_kpa",
                        maximum=23.5,
                    ),
                    LowerBoundConstraint(
                        constraint_id="eclss.recycle_efficiency.min",
                        metric_key="recycle_efficiency",
                        minimum=0.75,
                    ),
                )
            ),
            policy=GatePolicy(min_confidence=0.75, min_evidence_count=1),
        )

        decision = DecisionRecord(
            decision_id="eclss.phase3.baseline",
            subsystem=Subsystem.ECLSS.value,
            recommendation=(
                "Maintain ECLSS reserve margins with 20% oxygen headroom and "
                "high water recycling efficiency."
            ),
            rationale="Protects crew habitability against short-term disturbances.",
            assumptions=(
                "Crew metabolic load is near nominal profile",
                "Nominal hardware uptime is maintained",
            ),
            evidence=request.evidence,
            confidence=forecast.confidence_level,
        )

        gate_result = gate.evaluate(decision=decision, state=state, forecast=forecast)

        return ModuleResponse(
            subsystem=Subsystem.ECLSS,
            decision=decision,
            metrics=(
                ModuleMetric(
                    name="oxygen_generation_kg_day",
                    value=UncertaintyBounds(
                        mean=oxygen_generation_kg_day,
                        lower=oxygen_generation_kg_day * 0.9,
                        upper=oxygen_generation_kg_day * 1.1,
                        unit="kg/day",
                    ),
                ),
                ModuleMetric(
                    name="water_makeup_l_day",
                    value=UncertaintyBounds(
                        mean=water_makeup_l_day,
                        lower=max(0.0, water_makeup_l_day * 0.85),
                        upper=water_makeup_l_day * 1.15,
                        unit="L/day",
                    ),
                ),
                ModuleMetric(
                    name="eclss_power_demand_kw",
                    value=UncertaintyBounds(
                        mean=forecast.expected_value,
                        lower=forecast.lower_bound,
                        upper=forecast.upper_bound,
                        unit="kW",
                    ),
                ),
            ),
            gate=gate_result,
        )
