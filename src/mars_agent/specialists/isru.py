"""ISRU specialist module (oxygen/water production and power demand)."""

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
    Subsystem,
    UncertaintyBounds,
)


@dataclass(slots=True)
class ISRUSpecialist:
    """Evaluates in-situ resource utilization production recommendations."""

    forecaster: GaussianMonteCarloForecaster = field(
        default_factory=lambda: GaussianMonteCarloForecaster(seed=99)
    )

    def analyze(self, request: ModuleRequest) -> ModuleResponse:
        if request.subsystem is not Subsystem.ISRU:
            raise ValueError("ISRUSpecialist only accepts ISRU requests")

        regolith_feed_kg_day = request.get_input("regolith_feed_kg_day").value.mean
        ice_grade_fraction = request.get_input("ice_grade_fraction").value.mean
        reactor_efficiency = request.get_input("reactor_efficiency").value.mean
        electrolysis_kwh_per_kg_o2 = request.get_input("electrolysis_kwh_per_kg_o2").value.mean
        available_power_kw = request.get_input("available_power_kw").value.mean

        oxygen_production_kg_day = (
            regolith_feed_kg_day * ice_grade_fraction * 0.45 * reactor_efficiency
        )
        water_extraction_kg_day = (
            regolith_feed_kg_day * ice_grade_fraction * 0.35 * reactor_efficiency
        )

        forecast = self.forecaster.forecast(
            ForecastRequest(
                metric="isru_power_demand_kw",
                confidence_level=request.desired_confidence,
                inputs=(
                    ForecastInput(
                        name="electrolysis_load_kw",
                        mean=(oxygen_production_kg_day * electrolysis_kwh_per_kg_o2) / 24.0,
                        std_dev=max(0.1, oxygen_production_kg_day * 0.05),
                        minimum=0.0,
                    ),
                    ForecastInput(
                        name="balance_of_plant_kw",
                        mean=5.0,
                        std_dev=0.8,
                        minimum=2.0,
                    ),
                ),
            )
        )

        state = {
            "reactor_efficiency": reactor_efficiency,
            "isru_power_demand_kw": forecast.expected_value,
            "available_power_kw": available_power_kw,
            "oxygen_production_kg_day": oxygen_production_kg_day,
        }

        gate = GlobalValidationGate(
            rules_engine=SymbolicRulesEngine(
                constraints=(
                    LowerBoundConstraint(
                        constraint_id="isru.reactor_efficiency.min",
                        metric_key="reactor_efficiency",
                        minimum=0.4,
                    ),
                    UpperBoundConstraint(
                        constraint_id="isru.reactor_efficiency.max",
                        metric_key="reactor_efficiency",
                        maximum=0.95,
                    ),
                    UpperBoundConstraint(
                        constraint_id="isru.power_budget.max",
                        metric_key="isru_power_demand_kw",
                        maximum=available_power_kw,
                    ),
                    LowerBoundConstraint(
                        constraint_id="isru.oxygen_output.min",
                        metric_key="oxygen_production_kg_day",
                        minimum=3.0,
                    ),
                )
            ),
            policy=GatePolicy(min_confidence=0.75, min_evidence_count=1),
        )

        decision = DecisionRecord(
            decision_id="isru.phase3.baseline",
            subsystem=Subsystem.ISRU.value,
            recommendation=(
                "Run ISRU at conservative extraction throughput to preserve power margin "
                "during uncertain feedstock quality."
            ),
            rationale="Balances oxygen output targets with stable energy consumption.",
            assumptions=(
                "Ice grade variability remains within modeled uncertainty",
                "Electrolyzer degradation remains below maintenance thresholds",
            ),
            evidence=request.evidence,
            confidence=forecast.confidence_level,
        )

        gate_result = gate.evaluate(decision=decision, state=state, forecast=forecast)

        return ModuleResponse(
            subsystem=Subsystem.ISRU,
            decision=decision,
            metrics=(
                ModuleMetric(
                    name="oxygen_production_kg_day",
                    value=UncertaintyBounds(
                        mean=oxygen_production_kg_day,
                        lower=oxygen_production_kg_day * 0.88,
                        upper=oxygen_production_kg_day * 1.12,
                        unit="kg/day",
                    ),
                ),
                ModuleMetric(
                    name="water_extraction_kg_day",
                    value=UncertaintyBounds(
                        mean=water_extraction_kg_day,
                        lower=water_extraction_kg_day * 0.88,
                        upper=water_extraction_kg_day * 1.12,
                        unit="kg/day",
                    ),
                ),
                ModuleMetric(
                    name="isru_power_demand_kw",
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
