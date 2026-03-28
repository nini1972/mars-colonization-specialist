"""Central planner that decomposes goals, runs specialists, and replans."""

from __future__ import annotations

from dataclasses import dataclass, field

from mars_agent.orchestration.coupling import CouplingChecker
from mars_agent.orchestration.models import (
    CrossDomainConflict,
    MissionGoal,
    MissionPhase,
    PlannerSettings,
    PlanResult,
    ReadinessSignals,
    ReplanEvent,
)
from mars_agent.orchestration.state_machine import MissionPhaseStateMachine
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.specialists import (
    ECLSSSpecialist,
    ISRUSpecialist,
    ModuleInput,
    ModuleRequest,
    ModuleResponse,
    PowerSpecialist,
    Subsystem,
    UncertaintyBounds,
)


def _replace_input(request: ModuleRequest, name: str, updated: UncertaintyBounds) -> ModuleRequest:
    rewritten = tuple(
        ModuleInput(item.name, updated) if item.name == name else item for item in request.inputs
    )
    return ModuleRequest(
        mission=request.mission,
        subsystem=request.subsystem,
        inputs=rewritten,
        evidence=request.evidence,
        desired_confidence=request.desired_confidence,
    )


@dataclass(slots=True)
class CentralPlanner:
    """Orchestrates subsystem analysis and resolves cross-domain couplings."""

    eclss: ECLSSSpecialist = field(default_factory=ECLSSSpecialist)
    isru: ISRUSpecialist = field(default_factory=ISRUSpecialist)
    power: PowerSpecialist = field(default_factory=PowerSpecialist)
    coupling_checker: CouplingChecker = field(default_factory=CouplingChecker)
    state_machine: MissionPhaseStateMachine = field(default_factory=MissionPhaseStateMachine)
    settings: PlannerSettings = field(default_factory=PlannerSettings)

    def _eclss_request(
        self,
        goal: MissionGoal,
        evidence: tuple[EvidenceReference, ...],
    ) -> ModuleRequest:
        phase_thermal_factor = {
            MissionPhase.TRANSIT: 0.9,
            MissionPhase.LANDING: 1.0,
            MissionPhase.EARLY_OPERATIONS: 1.1,
            MissionPhase.SCALING: 1.2,
        }[goal.current_phase]

        return ModuleRequest(
            mission=goal.mission_id,
            subsystem=Subsystem.ECLSS,
            evidence=evidence,
            desired_confidence=goal.desired_confidence,
            inputs=(
                ModuleInput(
                    "crew_count",
                    UncertaintyBounds(
                        float(goal.crew_size),
                        float(goal.crew_size),
                        float(goal.crew_size),
                        "crew",
                    ),
                ),
                ModuleInput(
                    "o2_demand_per_crew_kg_day",
                    UncertaintyBounds(0.84, 0.8, 0.9, "kg/day"),
                ),
                ModuleInput(
                    "water_demand_per_crew_l_day",
                    UncertaintyBounds(18.0, 16.0, 20.0, "L/day"),
                ),
                ModuleInput("recycle_efficiency", UncertaintyBounds(0.87, 0.82, 0.9, "ratio")),
                ModuleInput(
                    "thermal_load_kw",
                    UncertaintyBounds(
                        6.0 * goal.crew_size * phase_thermal_factor,
                        5.0 * goal.crew_size * phase_thermal_factor,
                        7.0 * goal.crew_size * phase_thermal_factor,
                        "kW",
                    ),
                ),
                ModuleInput("o2_partial_pressure_kpa", UncertaintyBounds(20.8, 20.0, 21.5, "kPa")),
            ),
        )

    def _isru_request(
        self,
        goal: MissionGoal,
        evidence: tuple[EvidenceReference, ...],
        reduction: float = 0.0,
    ) -> ModuleRequest:
        baseline_feedstock = {
            MissionPhase.TRANSIT: 0.0,
            MissionPhase.LANDING: 50.0,
            MissionPhase.EARLY_OPERATIONS: 90.0,
            MissionPhase.SCALING: 140.0,
        }[goal.current_phase]
        adjusted_feedstock = baseline_feedstock * (1.0 - reduction)

        return ModuleRequest(
            mission=goal.mission_id,
            subsystem=Subsystem.ISRU,
            evidence=evidence,
            desired_confidence=goal.desired_confidence,
            inputs=(
                ModuleInput(
                    "regolith_feed_kg_day",
                    UncertaintyBounds(
                        adjusted_feedstock,
                        max(0.0, adjusted_feedstock * 0.9),
                        adjusted_feedstock * 1.1 if adjusted_feedstock > 0 else 0.0,
                        "kg/day",
                    ),
                ),
                ModuleInput("ice_grade_fraction", UncertaintyBounds(0.24, 0.2, 0.3, "ratio")),
                ModuleInput("reactor_efficiency", UncertaintyBounds(0.74, 0.68, 0.8, "ratio")),
                ModuleInput(
                    "electrolysis_kwh_per_kg_o2",
                    UncertaintyBounds(44.0, 40.0, 48.0, "kWh/kg"),
                ),
                ModuleInput(
                    "available_power_kw",
                    UncertaintyBounds(
                        goal.solar_generation_kw,
                        goal.solar_generation_kw * 0.8,
                        goal.solar_generation_kw * 1.1,
                        "kW",
                    ),
                ),
            ),
        )

    def _power_request(
        self,
        goal: MissionGoal,
        evidence: tuple[EvidenceReference, ...],
        critical_load_kw: float,
    ) -> ModuleRequest:
        return ModuleRequest(
            mission=goal.mission_id,
            subsystem=Subsystem.POWER,
            evidence=evidence,
            desired_confidence=goal.desired_confidence,
            inputs=(
                ModuleInput(
                    "solar_generation_kw",
                    UncertaintyBounds(
                        goal.solar_generation_kw,
                        goal.solar_generation_kw * 0.85,
                        goal.solar_generation_kw * 1.15,
                        "kW",
                    ),
                ),
                ModuleInput(
                    "battery_capacity_kwh",
                    UncertaintyBounds(
                        goal.battery_capacity_kwh,
                        goal.battery_capacity_kwh * 0.9,
                        goal.battery_capacity_kwh * 1.1,
                        "kWh",
                    ),
                ),
                ModuleInput(
                    "critical_load_kw",
                    UncertaintyBounds(
                        critical_load_kw,
                        critical_load_kw * 0.95,
                        critical_load_kw * 1.05,
                        "kW",
                    ),
                ),
                ModuleInput(
                    "dust_degradation_fraction",
                    UncertaintyBounds(
                        goal.dust_degradation_fraction,
                        max(0.0, goal.dust_degradation_fraction * 0.8),
                        min(0.95, goal.dust_degradation_fraction * 1.2),
                        "ratio",
                    ),
                ),
                ModuleInput(
                    "hours_without_sun",
                    UncertaintyBounds(
                        goal.hours_without_sun,
                        goal.hours_without_sun * 0.9,
                        goal.hours_without_sun * 1.1,
                        "h",
                    ),
                ),
            ),
        )

    def _run_modules(
        self,
        goal: MissionGoal,
        evidence: tuple[EvidenceReference, ...],
        isru_reduction: float,
    ) -> tuple[ModuleResponse, ModuleResponse, ModuleResponse]:
        eclss_request = self._eclss_request(goal, evidence)
        isru_request = self._isru_request(goal, evidence, reduction=isru_reduction)

        eclss_response = self.eclss.analyze(eclss_request)
        isru_response = self.isru.analyze(isru_request)

        critical_load_kw = (
            eclss_response.get_metric("eclss_power_demand_kw").value.mean
            + isru_response.get_metric("isru_power_demand_kw").value.mean
        )

        power_request = self._power_request(goal, evidence, critical_load_kw)
        power_response = self.power.analyze(power_request)
        return eclss_response, isru_response, power_response

    def _readiness(
        self,
        eclss: ModuleResponse,
        isru: ModuleResponse,
        power: ModuleResponse,
    ) -> ReadinessSignals:
        effective_generation = power.get_metric("effective_generation_kw").value.mean
        total_critical_load = (
            eclss.get_metric("eclss_power_demand_kw").value.mean
            + isru.get_metric("isru_power_demand_kw").value.mean
        )
        oxygen_generation = isru.get_metric("oxygen_production_kg_day").value.mean

        surplus_ratio = effective_generation / max(total_critical_load, 1e-6)
        return ReadinessSignals(
            landing_site_validated=eclss.gate.accepted,
            surface_power_stable=power.gate.accepted,
            life_support_stable=eclss.gate.accepted,
            resource_surplus_ratio=surplus_ratio,
            additional_signals={
                "oxygen_generation_kg_day": oxygen_generation,
                "total_critical_load_kw": total_critical_load,
                "effective_generation_kw": effective_generation,
            },
        )

    def plan(self, goal: MissionGoal, evidence: tuple[EvidenceReference, ...]) -> PlanResult:
        replan_events: list[ReplanEvent] = []
        current_reduction = 0.0

        eclss_response: ModuleResponse
        isru_response: ModuleResponse
        power_response: ModuleResponse

        conflicts: tuple[CrossDomainConflict, ...] = ()
        for attempt in range(self.settings.max_replan_attempts + 1):
            eclss_response, isru_response, power_response = self._run_modules(
                goal,
                evidence,
                isru_reduction=current_reduction,
            )
            conflicts = self.coupling_checker.evaluate(
                eclss=eclss_response,
                isru=isru_response,
                power=power_response,
            )
            if not conflicts:
                break

            if attempt < self.settings.max_replan_attempts:
                replan_events.append(
                    ReplanEvent(
                        attempt=attempt + 1,
                        reason="Detected cross-domain conflicts; reducing ISRU feedstock demand.",
                    )
                )
                current_reduction = min(
                    0.8,
                    current_reduction + self.settings.replan_feedstock_reduction,
                )

        readiness = self._readiness(eclss_response, isru_response, power_response)
        next_phase = self.state_machine.next_phase(goal.current_phase, readiness)

        return PlanResult(
            mission_id=goal.mission_id,
            started_phase=goal.current_phase,
            next_phase=next_phase,
            subsystem_responses=(eclss_response, isru_response, power_response),
            conflicts=conflicts,
            replan_events=tuple(replan_events),
        )
