"""Adapter connecting multi-agent settlement plans to the AresSim physical simulation engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mars_agent.orchestration.models import MissionGoal, PlanResult
from mars_agent.simulation.aressim import (
    AresSimStepRequest,
    AresSimStepResponse,
    ECLSSParameters,
    EnvironmentState,
    ISRUSubsystemDemands,
    PowerGridControls,
    SimulationMetadata,
    simulate_step,
)
from mars_agent.specialists.contracts import UncertaintyBounds


@dataclass(frozen=True, slots=True)
class AresSimTrajectoryReport:
    """Consolidated report across a simulated Mars operational window."""

    mission_id: str
    total_steps: int
    duration_sols: float
    min_bess_soc: float
    final_bess_soc: float
    peak_solar_generation_kw: float
    cumulative_o2_g: float
    cumulative_water_kg: float
    cumulative_methane_g: float
    total_hvac_energy_kwh: float
    step_history: tuple[AresSimStepResponse, ...]

    def to_uncertainty_metrics(self) -> dict[str, UncertaintyBounds]:
        """Convert trajectory outputs into standardized UncertaintyBounds metrics."""
        solar_values = [
            s.power_grid_state.pv_power_generated_kw for s in self.step_history
        ]
        hvac_values = [
            s.habitat_thermal_and_life_support.supplemental_hvac_power_kw
            for s in self.step_history
        ]
        isru_power_values = [
            s.isru_yields.power_consumed_isru_kw for s in self.step_history
        ]

        def _stats(arr: list[float], unit: str) -> UncertaintyBounds:
            if not arr:
                return UncertaintyBounds(mean=0.0, lower=0.0, upper=0.0, unit=unit)
            mean_v = sum(arr) / len(arr)
            return UncertaintyBounds(
                mean=round(mean_v, 2),
                lower=round(min(arr), 2),
                upper=round(max(arr), 2),
                unit=unit,
            )

        return {
            "effective_generation_kw": _stats(solar_values, "kW"),
            "hvac_power_demand_kw": _stats(hvac_values, "kW"),
            "isru_power_demand_kw": _stats(isru_power_values, "kW"),
        }


class AresSimRunner:
    """Executes multi-step physical Mars simulations derived from mission goals."""

    def __init__(
        self,
        latitude_deg: float = 39.2,  # Arcadia Planitia
        longitude_deg: float = 189.7,
        fsp_reactors: int = 1,
    ) -> None:
        self.latitude_deg = latitude_deg
        self.longitude_deg = longitude_deg
        self.fsp_reactors = fsp_reactors

    def run_trajectory(
        self,
        goal: MissionGoal,
        duration_sols: int = 1,
        time_step_hours: float = 1.0,
        dust_optical_depth: float | None = None,
        ambient_temp_k: float = 210.0,
        wind_speed_m_s: float = 6.5,
        target_o2_g_hr: float = 50.0,
        target_water_kg_hr: float = 10.0,
        target_ch4_g_hr: float = 20.0,
    ) -> AresSimTrajectoryReport:
        """Step the physical engine across the specified sol horizon."""
        tau = (
            dust_optical_depth
            if dust_optical_depth is not None
            else (goal.dust_degradation_fraction * 2.0 + 0.4)
        )
        step_seconds = time_step_hours * 3600.0
        total_steps = int(duration_sols * 24.0 / time_step_hours)

        # Estimate PV aperture from solar_generation_kw (assuming ~28% eff & nominal irradiance)
        pv_area_m2 = max(100.0, goal.solar_generation_kw * 4.0)
        current_soc = 0.85  # Starting at 85% battery charge

        step_history: list[AresSimStepResponse] = []
        cumulative_o2_g = 0.0
        cumulative_water_kg = 0.0
        cumulative_ch4_g = 0.0
        total_hvac_energy_kwh = 0.0
        min_soc = current_soc
        peak_solar = 0.0

        for step_idx in range(total_steps):
            current_hour = (step_idx * time_step_hours) % 24.0
            sol_num = int((step_idx * time_step_hours) // 24.0)

            request = AresSimStepRequest(
                simulation_metadata=SimulationMetadata(
                    step_duration_seconds=step_seconds,
                    sol_number=sol_num,
                    ls_degrees=270.0,  # Summer perihelion baseline
                    hour_of_sol=current_hour,
                ),
                environment_state=EnvironmentState(
                    latitude_degrees=self.latitude_deg,
                    longitude_degrees=self.longitude_deg,
                    dust_optical_depth=tau,
                    ambient_temperature_k=ambient_temp_k,
                    wind_speed_m_s=wind_speed_m_s,
                ),
                power_grid_controls=PowerGridControls(
                    pv_total_aperture_m2=pv_area_m2,
                    fsp_active_reactors=self.fsp_reactors,
                    bess_current_soc=current_soc,
                    trigger_pv_clean_event=(step_idx == 0),
                ),
                isru_subsystem_demands=ISRUSubsystemDemands(
                    target_o2_production_g_hr=target_o2_g_hr,
                    target_water_extraction_kg_hr=target_water_kg_hr,
                    target_methane_production_g_hr=target_ch4_g_hr,
                ),
                eclss_parameters=ECLSSParameters(
                    crew_count=goal.crew_size,
                    target_internal_temp_k=294.15,
                    aerogel_insulation_thickness_m=0.1,
                    regolith_shielding_meters=2.5,
                ),
            )

            response = simulate_step(request)
            step_history.append(response)

            current_soc = response.power_grid_state.new_bess_soc
            min_soc = min(min_soc, current_soc)
            peak_solar = max(
                peak_solar, response.power_grid_state.pv_power_generated_kw
            )

            cumulative_o2_g += response.isru_yields.actual_o2_produced_g
            cumulative_water_kg += response.isru_yields.actual_water_extracted_kg
            cumulative_ch4_g += response.isru_yields.actual_methane_produced_g
            total_hvac_energy_kwh += (
                response.habitat_thermal_and_life_support.supplemental_hvac_power_kw
                * time_step_hours
            )

        return AresSimTrajectoryReport(
            mission_id=goal.mission_id,
            total_steps=total_steps,
            duration_sols=float(duration_sols),
            min_bess_soc=round(min_soc, 4),
            final_bess_soc=round(current_soc, 4),
            peak_solar_generation_kw=round(peak_solar, 2),
            cumulative_o2_g=round(cumulative_o2_g, 2),
            cumulative_water_kg=round(cumulative_water_kg, 2),
            cumulative_methane_g=round(cumulative_ch4_g, 2),
            total_hvac_energy_kwh=round(total_hvac_energy_kwh, 2),
            step_history=tuple(step_history),
        )

    def run_from_plan(
        self,
        plan: PlanResult,
        goal: MissionGoal | None = None,
        duration_sols: int = 1,
        dust_optical_depth: float | None = None,
    ) -> AresSimTrajectoryReport:
        """Execute AresSim directly against an existing CentralPlanner PlanResult."""
        if goal is None:
            # Extract subsystem metrics from plan responses
            def _extract_metric(name: str, fallback: float) -> float:
                for response in plan.subsystem_responses:
                    for metric in response.metrics:
                        if metric.name == name:
                            return metric.value.mean
                return fallback

            solar_gen = _extract_metric("effective_generation_kw", 800.0)
            battery_hours = _extract_metric("storage_cover_hours", 20.0)
            # Estimate battery kWh based on storage cover hours and critical loads
            battery_kwh = max(1000.0, solar_gen * battery_hours)

            synthesized_goal = MissionGoal(
                mission_id=plan.mission_id,
                crew_size=12,
                horizon_years=2.0,
                current_phase=plan.started_phase,
                solar_generation_kw=solar_gen,
                battery_capacity_kwh=battery_kwh,
                dust_degradation_fraction=0.2,
                hours_without_sun=battery_hours,
            )
        else:
            synthesized_goal = goal

        return self.run_trajectory(
            goal=synthesized_goal,
            duration_sols=duration_sols,
            dust_optical_depth=dust_optical_depth,
        )

    def run_nominal_sol(self) -> AresSimTrajectoryReport:
        """Convenience execution of a single 24-hour nominal sol for baseline evaluation."""
        from mars_agent.orchestration.models import MissionPhase

        default_goal = MissionGoal(
            mission_id="mars-aressim-baseline",
            crew_size=12,
            horizon_years=2.0,
            current_phase=MissionPhase.EARLY_OPERATIONS,
            solar_generation_kw=800.0,
            battery_capacity_kwh=3500.0,
            dust_degradation_fraction=0.2,
            hours_without_sun=20.0,
        )
        return self.run_trajectory(default_goal, duration_sols=1)
