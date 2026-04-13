"""Central planner that decomposes goals, runs specialists, and replans."""

from __future__ import annotations

import concurrent.futures
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Protocol

from mars_agent.orchestration.coupling import CouplingChecker
from mars_agent.orchestration.models import (
    CrossDomainConflict,
    MissionGoal,
    MissionPhase,
    PlannerSettings,
    PlanResult,
    ReadinessSignals,
    ReplanEvent,
    SpecialistTiming,
)
from mars_agent.orchestration.negotiation_store import (
    NegotiationMemoryStore,
    NegotiationOutcome,
    _conflict_fingerprint,
    now_utc_iso,
)
from mars_agent.orchestration.negotiator import MultiAgentNegotiator, NegotiationRound
from mars_agent.orchestration.registry import SpecialistRegistry
from mars_agent.orchestration.state_machine import MissionPhaseStateMachine
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.specialists import (
    ModuleInput,
    ModuleRequest,
    ModuleResponse,
    Subsystem,
    UncertaintyBounds,
)


class _HasAnalyze(Protocol):
    def analyze(self, request: ModuleRequest) -> ModuleResponse: ...


def _timed_analyze(specialist: _HasAnalyze, request: ModuleRequest) -> tuple[ModuleResponse, float]:
    """Call specialist.analyze and return (response, latency_ms)."""
    t0 = perf_counter()
    response = specialist.analyze(request)
    return response, (perf_counter() - t0) * 1000.0


# Built-in per-conflict knob adjustments: (isru_delta, crew_delta, dust_delta).
# Overridable via PlannerSettings.conflict_knob_overrides.
_CONFLICT_KNOB_TABLE: dict[str, tuple[float, int, float]] = {
    "coupling.power_balance.thermal_shortfall": (0.05, 0, 0.0),
    "coupling.power_balance.shortfall": (0.05, 0, 0.0),
    "coupling.isru_power_share.high": (0.05, 0, 0.02),
    "coupling.power_margin.low": (0.03, 1, 0.0),
    "coupling.power_gate.failed": (0.05, 1, 0.0),
}


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


def _power_inputs(goal: MissionGoal, critical_load_kw: float) -> tuple[ModuleInput, ...]:
    """Build the five ModuleInput entries for the power specialist request."""
    return (
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
    )


@dataclass(slots=True)
class CentralPlanner:
    """Orchestrates subsystem analysis and resolves cross-domain couplings."""

    registry: SpecialistRegistry = field(default_factory=SpecialistRegistry.default)
    coupling_checker: CouplingChecker = field(default_factory=CouplingChecker)
    state_machine: MissionPhaseStateMachine = field(default_factory=MissionPhaseStateMachine)
    settings: PlannerSettings = field(default_factory=PlannerSettings)
    negotiator: MultiAgentNegotiator = field(default_factory=MultiAgentNegotiator)
    negotiation_store: NegotiationMemoryStore = field(default_factory=NegotiationMemoryStore)

    def __post_init__(self) -> None:
        if self.settings.negotiation_store_path is not None:
            self.negotiation_store = NegotiationMemoryStore(
                Path(self.settings.negotiation_store_path)
            )

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
            inputs=_power_inputs(goal, critical_load_kw),
        )

    def _thermal_request(
        self,
        goal: MissionGoal,
        evidence: tuple[EvidenceReference, ...],
    ) -> ModuleRequest:
        base_solar_flux = {
            MissionPhase.TRANSIT: 300.0,
            MissionPhase.LANDING: 590.0,
            MissionPhase.EARLY_OPERATIONS: 590.0,
            MissionPhase.SCALING: 590.0,
        }[goal.current_phase]
        solar_flux = base_solar_flux * (1.0 - goal.dust_degradation_fraction)
        habitat_area = 50.0 * goal.crew_size
        thermal_budget_kw = goal.solar_generation_kw * 0.2

        return ModuleRequest(
            mission=goal.mission_id,
            subsystem=Subsystem.HABITAT_THERMODYNAMICS,
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
                    "solar_flux_w_m2",
                    UncertaintyBounds(solar_flux, solar_flux * 0.85, solar_flux * 1.15, "W/m²"),
                ),
                ModuleInput(
                    "habitat_area_m2",
                    UncertaintyBounds(habitat_area, habitat_area * 0.95, habitat_area * 1.05, "m²"),
                ),
                ModuleInput(
                    "insulation_r_value",
                    UncertaintyBounds(3.5, 3.0, 4.0, "m²·K/W"),
                ),
                ModuleInput(
                    "thermal_power_budget_kw",
                    UncertaintyBounds(
                        thermal_budget_kw,
                        thermal_budget_kw * 0.9,
                        thermal_budget_kw * 1.1,
                        "kW",
                    ),
                ),
            ),
        )

    def _run_modules(
        self,
        goal: MissionGoal,
        evidence: tuple[EvidenceReference, ...],
        isru_reduction: float,
    ) -> tuple[
        tuple[ModuleResponse, ModuleResponse, ModuleResponse, ModuleResponse],
        tuple[SpecialistTiming, ...],
    ]:
        eclss_request = self._eclss_request(goal, evidence)
        isru_request = self._isru_request(goal, evidence, reduction=isru_reduction)
        thermal_request = self._thermal_request(goal, evidence)

        # ECLSS, ISRU, and Thermal are independent — run them concurrently.
        # Power depends on the ECLSS + ISRU outputs (critical_load_kw) so it
        # must wait for the first wave to complete.
        eclss_specialist = self.registry.get(Subsystem.ECLSS)
        isru_specialist = self.registry.get(Subsystem.ISRU)
        thermal_specialist = self.registry.get(Subsystem.HABITAT_THERMODYNAMICS)
        power_specialist = self.registry.get(Subsystem.POWER)

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            eclss_fut = pool.submit(_timed_analyze, eclss_specialist, eclss_request)
            isru_fut = pool.submit(_timed_analyze, isru_specialist, isru_request)
            thermal_fut = pool.submit(_timed_analyze, thermal_specialist, thermal_request)

            eclss_response, eclss_ms = eclss_fut.result()
            isru_response, isru_ms = isru_fut.result()
            thermal_response, thermal_ms = thermal_fut.result()

        critical_load_kw = (
            eclss_response.get_metric("eclss_power_demand_kw").value.mean
            + isru_response.get_metric("isru_power_demand_kw").value.mean
        )

        power_request = self._power_request(goal, evidence, critical_load_kw)
        power_response, power_ms = _timed_analyze(power_specialist, power_request)

        timings: tuple[SpecialistTiming, ...] = (
            SpecialistTiming(
                subsystem_name=eclss_response.subsystem.value,
                latency_ms=eclss_ms,
                gate_accepted=eclss_response.gate.accepted,
            ),
            SpecialistTiming(
                subsystem_name=isru_response.subsystem.value,
                latency_ms=isru_ms,
                gate_accepted=isru_response.gate.accepted,
            ),
            SpecialistTiming(
                subsystem_name=power_response.subsystem.value,
                latency_ms=power_ms,
                gate_accepted=power_response.gate.accepted,
            ),
            SpecialistTiming(
                subsystem_name=thermal_response.subsystem.value,
                latency_ms=thermal_ms,
                gate_accepted=thermal_response.gate.accepted,
            ),
        )
        return (eclss_response, isru_response, power_response, thermal_response), timings

    def _conflict_aware_fallback(
        self,
        conflicts: tuple[CrossDomainConflict, ...],
        current_reduction: float,
    ) -> tuple[float, int, float, str]:
        """Return (new_isru_reduction, crew_delta, dust_delta, rationale) for active conflicts.

        Knob deltas are derived first from the live specialist registry (capability-driven),
        then supplemented by the static ``_CONFLICT_KNOB_TABLE`` for conflict IDs that map
        to specific subsystem actions.  Deltas are combined via max() per knob across all
        active conflict IDs.  Unknown IDs fall back to replan_feedstock_reduction with no
        crew/dust change.
        """
        # Build per-knob preferred deltas from the registry's live capabilities.
        # Only the ISRU knob is used in the unknown-conflict fallback path;
        # crew and dust deltas are governed by the static conflict-ID table entries.
        cap_isru_delta = 0.0
        for cap in self.registry.all_capabilities():
            for knob in cap.tradeoff_knobs:
                if knob.name == "isru_reduction_fraction":
                    cap_isru_delta = max(cap_isru_delta, knob.preferred_delta)

        # Override/supplement with the static conflict-ID table (and any per-deployment
        # overrides from PlannerSettings) so that conflict-specific logic is preserved.
        table: dict[str, tuple[float, int, float]] = {
            **_CONFLICT_KNOB_TABLE,
            **self.settings.conflict_knob_overrides,
        }
        isru_delta = 0.0
        crew_delta = 0
        dust_delta = 0.0
        named: list[str] = []
        for conflict in conflicts:
            cid = conflict.conflict_id
            if cid in table:
                d_isru, d_crew, d_dust = table[cid]
            else:
                # For unknown conflicts apply only ISRU reduction (safest default).
                # Respect the larger of the capability preferred_delta and the
                # configured replan_feedstock_reduction.
                d_isru = max(cap_isru_delta, self.settings.replan_feedstock_reduction)
                d_crew = 0
                d_dust = 0.0
            isru_delta = max(isru_delta, d_isru)
            crew_delta = max(crew_delta, d_crew)
            dust_delta = max(dust_delta, d_dust)
            named.append(cid)
        new_isru_reduction = min(0.8, current_reduction + isru_delta)
        crew_delta = min(5, crew_delta)
        dust_delta = min(0.1, dust_delta)
        conflict_list = ", ".join(named) if named else "none"
        rationale = (
            f"Conflict-aware fallback for [{conflict_list}]: "
            f"ISRU +{isru_delta:.2f}, crew -{crew_delta}, dust -{dust_delta:.2f}."
        )
        return new_isru_reduction, crew_delta, dust_delta, rationale

    def _handle_replan(
        self,
        goal: MissionGoal,
        conflicts: tuple[CrossDomainConflict, ...],
        current_reduction: float,
        history: tuple[NegotiationRound, ...] = (),
    ) -> tuple[float, MissionGoal, str, NegotiationRound]:
        """Negotiate or fall back; return (new_reduction, updated_goal, rationale, round)."""
        fingerprint = _conflict_fingerprint(goal, conflicts)
        cached = self.negotiation_store.lookup(fingerprint)
        if cached is not None and cached.accepted:
            new_reduction = min(0.8, max(current_reduction, cached.isru_reduction_fraction))
            crew_reduction = cached.crew_reduction
            dust_adj = cached.dust_degradation_adjustment
            memory_tag = "fallback" if cached.is_fallback else "llm"
            rationale = f"[memory hit][{memory_tag}] {cached.rationale}"
            accepted = True
            self.negotiation_store.store(
                NegotiationOutcome(
                    conflict_fingerprint=fingerprint,
                    isru_reduction_fraction=cached.isru_reduction_fraction,
                    crew_reduction=crew_reduction,
                    dust_degradation_adjustment=dust_adj,
                    rationale=cached.rationale,
                    accepted=cached.accepted,
                    is_fallback=cached.is_fallback,
                    stored_at=cached.stored_at,
                    hit_count=cached.hit_count + 1,
                )
            )
        else:
            accepted, new_reduction, crew_reduction, dust_adj, rationale = (
                self.negotiator.negotiate(
                    goal=goal,
                    conflicts=conflicts,
                    current_reduction=current_reduction,
                    history=history,
                    capabilities=self.registry.all_capabilities(),
                )
            )
            is_fallback: bool
            if not accepted:
                new_reduction, crew_reduction, dust_adj, rationale = (
                    self._conflict_aware_fallback(conflicts, current_reduction)
                )
                is_fallback = True
            else:
                is_fallback = False
            self.negotiation_store.store(
                NegotiationOutcome(
                    conflict_fingerprint=fingerprint,
                    isru_reduction_fraction=new_reduction,
                    crew_reduction=crew_reduction,
                    dust_degradation_adjustment=dust_adj,
                    rationale=rationale,
                    accepted=True,
                    is_fallback=is_fallback,
                    stored_at=now_utc_iso(),
                    hit_count=0,
                )
            )
        final_reduction = max(current_reduction, new_reduction)
        updated_goal = dataclasses.replace(
            goal,
            crew_size=max(1, goal.crew_size - crew_reduction),
            dust_degradation_fraction=max(0.0, goal.dust_degradation_fraction - dust_adj),
        )
        neg_round = NegotiationRound(
            round_num=len(history),
            reduction_fraction=final_reduction,
            accepted=accepted,
            rationale=rationale,
            crew_reduction=crew_reduction,
            dust_degradation_adjustment=dust_adj,
        )
        return final_reduction, updated_goal, rationale, neg_round

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
        negotiation_history: list[NegotiationRound] = []
        current_reduction = 0.0

        eclss_response: ModuleResponse
        isru_response: ModuleResponse
        power_response: ModuleResponse
        thermal_response: ModuleResponse
        last_timings: tuple[SpecialistTiming, ...] = ()

        conflicts: tuple[CrossDomainConflict, ...] = ()
        for attempt in range(self.settings.max_replan_attempts + 1):
            (
                eclss_response,
                isru_response,
                power_response,
                thermal_response,
            ), last_timings = self._run_modules(
                goal,
                evidence,
                isru_reduction=current_reduction,
            )
            conflicts = self.coupling_checker.evaluate(
                eclss=eclss_response,
                isru=isru_response,
                power=power_response,
                thermal=thermal_response,
            )
            if not conflicts:
                break

            if attempt < self.settings.max_replan_attempts:
                current_reduction, goal, rationale, neg_round = self._handle_replan(
                    goal, conflicts, current_reduction, history=tuple(negotiation_history)
                )
                negotiation_history.append(neg_round)
                replan_events.append(ReplanEvent(attempt=attempt + 1, reason=rationale))

        readiness = self._readiness(eclss_response, isru_response, power_response)
        next_phase = self.state_machine.next_phase(goal.current_phase, readiness)

        return PlanResult(
            mission_id=goal.mission_id,
            started_phase=goal.current_phase,
            next_phase=next_phase,
            subsystem_responses=(eclss_response, isru_response, power_response, thermal_response),
            conflicts=conflicts,
            replan_events=tuple(replan_events),
            specialist_timings=last_timings,
        )
