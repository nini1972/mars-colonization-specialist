"""Habitat Thermodynamics specialist module (thermal regulation, power demand)."""

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
from mars_agent.reasoning.models import DecisionRecord, EvidenceReference
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

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------
_T_MARS_AMBIENT_C: float = -60.0  # Mars mean surface temperature (°C)
_CREW_METABOLIC_W: float = 80.0  # Metabolic heat output per crew member (W)
_SOLAR_GAIN_COEFF: float = 0.0225  # Effective solar thermal gain fraction
_HVAC_EFFICIENCY: float = 0.85  # HVAC system efficiency (COP-normalised)
_T_TARGET_C: float = 22.0  # Nominal habitat temperature setpoint (°C)
_T_COMFORT_LOW: float = 18.0  # Lower habitability bound (°C)
_T_COMFORT_HIGH: float = 26.0  # Upper habitability bound (°C)
_T_DELTA_RANGE: float = _T_TARGET_C - _T_MARS_AMBIENT_C  # 82 °C — normalisation span


# ---------------------------------------------------------------------------
# Module-level helpers (extracted to keep cyclomatic complexity ≤ 4 each)
# ---------------------------------------------------------------------------


def _passive_temperature(
    solar_flux_w_m2: float,
    habitat_area_m2: float,
    insulation_r_value: float,
    crew_count: float,
) -> float:
    """Return equilibrium habitat temperature without active HVAC (°C)."""
    q_solar = solar_flux_w_m2 * habitat_area_m2 * _SOLAR_GAIN_COEFF
    q_metabolic = crew_count * _CREW_METABOLIC_W
    return _T_MARS_AMBIENT_C + (q_solar + q_metabolic) * insulation_r_value / max(
        habitat_area_m2, 1e-6
    )


def _thermal_state(
    t_passive: float,
    habitat_area_m2: float,
    insulation_r_value: float,
) -> tuple[float, float, float]:
    """Derive (internal_temp_c, thermal_power_demand_kw, temp_regulation_efficiency).

    * Overheating  (t_passive > 26 °C): HVAC cannot cool; internal_temp = t_passive.
    * Under-heating (t_passive < 18 °C): HVAC heats to 22 °C setpoint.
    * Comfortable  (18–26 °C): passive management; no demand.
    """
    r_safe = max(insulation_r_value, 1e-6)

    if t_passive > _T_COMFORT_HIGH:
        internal_temp = t_passive
        q_hvac = (t_passive - _T_TARGET_C) * habitat_area_m2 / r_safe
    elif t_passive < _T_COMFORT_LOW:
        internal_temp = _T_TARGET_C
        q_hvac = (_T_TARGET_C - t_passive) * habitat_area_m2 / r_safe
    else:
        internal_temp = t_passive
        q_hvac = 0.0

    demand_kw = q_hvac / (1000.0 * _HVAC_EFFICIENCY)
    efficiency = max(0.0, 1.0 - abs(internal_temp - _T_TARGET_C) / _T_DELTA_RANGE)
    return internal_temp, demand_kw, efficiency


# ---------------------------------------------------------------------------
# Builder helpers (extracted to keep analyze() ≤ 50 NLOC)
# ---------------------------------------------------------------------------

def _make_forecast_request(demand_kw: float, confidence: float) -> ForecastRequest:
    """Build the Monte-Carlo forecast request for thermal power demand."""
    return ForecastRequest(
        metric="thermal_power_demand_kw",
        confidence_level=confidence,
        inputs=(
            ForecastInput(
                name="hvac_demand_kw",
                mean=demand_kw,
                std_dev=max(0.05, demand_kw * 0.1),
                minimum=0.0,
            ),
            ForecastInput(
                name="insulation_variability_kw",
                mean=0.0,
                std_dev=max(0.02, demand_kw * 0.05),
            ),
        ),
    )


def _make_gate(thermal_power_budget_kw: float) -> GlobalValidationGate:
    """Build the validation gate with temperature and power-budget constraints."""
    return GlobalValidationGate(
        rules_engine=SymbolicRulesEngine(
            constraints=(
                LowerBoundConstraint(
                    constraint_id="habitat_thermal.temp.min",
                    metric_key="internal_temp_c",
                    minimum=_T_COMFORT_LOW,
                ),
                UpperBoundConstraint(
                    constraint_id="habitat_thermal.temp.max",
                    metric_key="internal_temp_c",
                    maximum=_T_COMFORT_HIGH,
                ),
                UpperBoundConstraint(
                    constraint_id="habitat_thermal.power.budget",
                    metric_key="thermal_power_demand_kw",
                    maximum=thermal_power_budget_kw,
                ),
            )
        ),
        policy=GatePolicy(min_confidence=0.75, min_evidence_count=1),
    )


def _make_decision(
    evidence: tuple[EvidenceReference, ...], confidence: float
) -> DecisionRecord:
    """Build the decision record for a thermal analysis run."""
    return DecisionRecord(
        decision_id="habitat_thermal.phase3.baseline",
        subsystem=Subsystem.HABITAT_THERMODYNAMICS.value,
        recommendation=(
            "Monitor habitat thermal envelope continuously; "
            "schedule HVAC maintenance before dust season to protect thermal margins."
        ),
        rationale=(
            "Maintains crew habitability by keeping internal temperature "
            "within survivable bounds despite Mars extreme ambient."
        ),
        assumptions=(
            "Habitat insulation R-value is nominal with ≤10% degradation",
            "Solar flux model accounts for dust opacity attenuation",
            "HVAC system operates at stated efficiency",
        ),
        evidence=evidence,
        confidence=confidence,
    )


def _make_metrics(
    internal_temp_c: float,
    demand_kw: float,
    lower_kw: float,
    upper_kw: float,
    efficiency: float,
) -> tuple[ModuleMetric, ...]:
    """Assemble the three output metrics with uncertainty bounds."""
    return (
        ModuleMetric(
            name="internal_temp_c",
            value=UncertaintyBounds(
                mean=internal_temp_c,
                lower=internal_temp_c - 1.5,
                upper=internal_temp_c + 1.5,
                unit="°C",
            ),
        ),
        ModuleMetric(
            name="thermal_power_demand_kw",
            value=UncertaintyBounds(
                mean=demand_kw,
                lower=lower_kw,
                upper=upper_kw,
                unit="kW",
            ),
        ),
        ModuleMetric(
            name="temp_regulation_efficiency",
            value=UncertaintyBounds(
                mean=efficiency,
                lower=max(0.0, efficiency - 0.05),
                upper=min(1.0, efficiency + 0.05),
                unit="",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Specialist
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HabitatThermodynamicsSpecialist:
    """Models thermal power demand and interior temperature for Mars habitats."""

    forecaster: GaussianMonteCarloForecaster = field(
        default_factory=GaussianMonteCarloForecaster
    )

    def capabilities(self) -> SpecialistCapability:
        """Return this specialist's self-description for negotiation."""
        return SpecialistCapability(
            subsystem=Subsystem.HABITAT_THERMODYNAMICS,
            accepts_inputs=(
                "crew_count",
                "solar_flux_w_m2",
                "habitat_area_m2",
                "insulation_r_value",
                "thermal_power_budget_kw",
            ),
            produces_metrics=(
                "internal_temp_c",
                "thermal_power_demand_kw",
                "temp_regulation_efficiency",
            ),
            tradeoff_knobs=(
                TradeoffKnob(
                    name="thermal_power_budget_reduction",
                    description=(
                        "Reduce the thermal power budget allocation to free capacity for "
                        "other subsystems"
                    ),
                    min_value=0.0,
                    max_value=20.0,
                    preferred_delta=2.0,
                    unit="kW",
                ),
            ),
        )

    def propose_tradeoffs(self, conflict_ids: tuple[str, ...]) -> tuple[TradeoffProposal, ...]:
        relevant = tuple(
            sorted(cid for cid in conflict_ids if cid == "coupling.power_balance.thermal_shortfall")
        )
        if not relevant:
            return ()
        return (
            TradeoffProposal(
                subsystem=Subsystem.HABITAT_THERMODYNAMICS,
                knob_name="thermal_power_budget_reduction",
                suggested_delta=2.0,
                rationale=(
                    "Thermal control recommends temporarily trimming the discretionary "
                    "thermal budget by 2 kW to relieve shared power pressure."
                ),
                conflict_ids=relevant,
            ),
        )

    def analyze(self, request: ModuleRequest) -> ModuleResponse:
        if request.subsystem is not Subsystem.HABITAT_THERMODYNAMICS:
            raise ValueError(
                "HabitatThermodynamicsSpecialist only accepts "
                "HABITAT_THERMODYNAMICS requests"
            )

        crew_count = request.get_input("crew_count").value.mean
        solar_flux_w_m2 = request.get_input("solar_flux_w_m2").value.mean
        habitat_area_m2 = request.get_input("habitat_area_m2").value.mean
        insulation_r_value = request.get_input("insulation_r_value").value.mean
        thermal_power_budget_kw = request.get_input("thermal_power_budget_kw").value.mean

        t_passive = _passive_temperature(
            solar_flux_w_m2, habitat_area_m2, insulation_r_value, crew_count
        )
        internal_temp_c, thermal_power_demand_kw, temp_regulation_efficiency = (
            _thermal_state(t_passive, habitat_area_m2, insulation_r_value)
        )

        forecast = self.forecaster.forecast(
            _make_forecast_request(thermal_power_demand_kw, request.desired_confidence)
        )
        state = {
            "internal_temp_c": internal_temp_c,
            "thermal_power_demand_kw": thermal_power_demand_kw,
        }
        gate = _make_gate(thermal_power_budget_kw)
        decision = _make_decision(request.evidence, forecast.confidence_level)
        gate_result = gate.evaluate(decision=decision, state=state, forecast=forecast)

        return ModuleResponse(
            subsystem=Subsystem.HABITAT_THERMODYNAMICS,
            decision=decision,
            metrics=_make_metrics(
                internal_temp_c,
                forecast.expected_value,
                forecast.lower_bound,
                forecast.upper_bound,
                temp_regulation_efficiency,
            ),
            gate=gate_result,
        )
