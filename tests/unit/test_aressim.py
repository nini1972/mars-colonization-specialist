"""Unit tests for the AresSim physical simulation engine and multi-agent adapter."""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from mars_agent.orchestration import CentralPlanner, MissionGoal, MissionPhase, PlannerSettings
from mars_agent.simulation.aressim import (
    AresSimStepRequest,
    AresSimStepResponse,
    ECLSSParameters,
    EnvironmentState,
    ISRUSubsystemDemands,
    PhysicalEngine,
    PowerGridControls,
    SimulationMetadata,
    aressim_app,
    simulate_step,
)
from mars_agent.simulation.aressim_adapter import AresSimRunner, AresSimTrajectoryReport


def _create_sample_request(
    hour: float = 12.0,
    sol: int = 10,
    tau: float = 0.4,
    ambient_temp_k: float = 210.0,
    fsp_reactors: int = 1,
    pv_area_m2: float = 3200.0,
    bess_soc: float = 0.85,
    crew_count: int = 12,
    regolith_meters: float = 2.5,
) -> AresSimStepRequest:
    return AresSimStepRequest(
        simulation_metadata=SimulationMetadata(
            step_duration_seconds=3600.0,
            sol_number=sol,
            ls_degrees=270.0,
            hour_of_sol=hour,
        ),
        environment_state=EnvironmentState(
            latitude_degrees=39.2,  # Arcadia Planitia
            longitude_degrees=189.7,
            dust_optical_depth=tau,
            ambient_temperature_k=ambient_temp_k,
            wind_speed_m_s=6.0,
        ),
        power_grid_controls=PowerGridControls(
            pv_total_aperture_m2=pv_area_m2,
            fsp_active_reactors=fsp_reactors,
            bess_current_soc=bess_soc,
            trigger_pv_clean_event=False,
        ),
        isru_subsystem_demands=ISRUSubsystemDemands(
            target_o2_production_g_hr=50.0,
            target_water_extraction_kg_hr=10.0,
            target_methane_production_g_hr=25.0,
        ),
        eclss_parameters=ECLSSParameters(
            crew_count=crew_count,
            target_internal_temp_k=294.15,
            aerogel_insulation_thickness_m=0.1,
            regolith_shielding_meters=regolith_meters,
        ),
    )


def test_physical_engine_kepler_and_solar_geometry() -> None:
    engine = PhysicalEngine()
    
    # Kepler solver should be accurate: E - e*sin(E) == M
    m_test = 45.0
    e_ecc = engine.solve_kepler(m_test)
    computed_m = math.degrees(e_ecc - engine.e * math.sin(e_ecc))
    assert abs(computed_m - m_test) < 1e-4

    # Midday insolation should be positive with clear skies
    ls_midday, theta_midday, ghi_midday = engine.solve_solar_geometry(
        sol=10.0, hour=12.0, latitude_deg=39.2, longitude_deg=189.7, tau=0.4
    )
    assert 0.0 <= ls_midday < 360.0
    assert theta_midday < math.pi / 2.0  # Sun above horizon at midday
    assert ghi_midday > 100.0  # Significant daytime solar flux

    # Midnight insolation must be zero (sun below horizon)
    _, theta_night, ghi_night = engine.solve_solar_geometry(
        sol=10.0, hour=0.0, latitude_deg=39.2, longitude_deg=189.7, tau=0.4
    )
    assert theta_night > math.pi / 2.0
    assert ghi_night == 0.0


def test_simulate_step_power_and_isru_production() -> None:
    req_midday = _create_sample_request(hour=12.0)
    res_midday = simulate_step(req_midday)

    assert isinstance(res_midday, AresSimStepResponse)
    assert res_midday.power_grid_state.pv_power_generated_kw > 0.0
    assert res_midday.power_grid_state.fsp_power_generated_kw > 35.0  # 1 reactor ~40kW
    assert res_midday.isru_yields.actual_o2_produced_g == 50.0  # 1 hr step
    assert res_midday.isru_yields.actual_water_extracted_kg == 10.0
    assert res_midday.isru_yields.actual_methane_produced_g == 25.0
    assert res_midday.isru_yields.power_consumed_isru_kw > 0.0


def test_simulate_step_night_power_deficit_and_battery_drain() -> None:
    # Midnight request with 0 FSP nuclear backup
    req_midnight = _create_sample_request(hour=0.0, fsp_reactors=0, bess_soc=0.8)
    res_midnight = simulate_step(req_midnight)

    # PV should be 0, nuclear is 0, so net power flow is negative
    assert res_midnight.power_grid_state.pv_power_generated_kw == 0.0
    assert res_midnight.power_grid_state.fsp_power_generated_kw == 0.0
    assert res_midnight.power_grid_state.net_bess_power_flow_kw < 0.0
    # Battery state of charge should decrease
    assert res_midnight.power_grid_state.new_bess_soc < 0.8


def test_habitat_regolith_insulation_scales_properly() -> None:
    engine = PhysicalEngine()
    u_thin = engine.calculate_habitat_u_value(
        aerogel_thickness_m=0.05, regolith_thickness_m=0.5, wind_speed_m_s=6.0
    )
    u_thick = engine.calculate_habitat_u_value(
        aerogel_thickness_m=0.05, regolith_thickness_m=3.0, wind_speed_m_s=6.0
    )
    # Thicker regolith shielding must result in lower thermal transmittance (U-value)
    assert u_thick < u_thin

    # Simulate steps comparing thin vs thick shielding
    req_thin = _create_sample_request(regolith_meters=0.5)
    req_thick = _create_sample_request(regolith_meters=3.0)
    res_thin = simulate_step(req_thin)
    res_thick = simulate_step(req_thick)

    assert (
        res_thick.habitat_thermal_and_life_support.heat_loss_environment_kw
        < res_thin.habitat_thermal_and_life_support.heat_loss_environment_kw
    )


def test_aressim_runner_nominal_trajectory() -> None:
    runner = AresSimRunner()
    report = runner.run_nominal_sol()

    assert isinstance(report, AresSimTrajectoryReport)
    assert report.total_steps == 24
    assert report.duration_sols == 1.0
    assert report.peak_solar_generation_kw > 0.0
    assert report.cumulative_o2_g == pytest.approx(50.0 * 24.0, rel=1e-3)
    assert report.cumulative_water_kg == pytest.approx(10.0 * 24.0, rel=1e-3)
    assert report.cumulative_methane_g == pytest.approx(20.0 * 24.0, rel=1e-3)

    metrics = report.to_uncertainty_metrics()
    assert "effective_generation_kw" in metrics
    assert "hvac_power_demand_kw" in metrics
    assert "isru_power_demand_kw" in metrics
    gen_metrics = metrics["effective_generation_kw"]
    assert gen_metrics.lower <= gen_metrics.mean <= gen_metrics.upper


def test_aressim_runner_from_central_planner() -> None:
    planner = CentralPlanner(settings=PlannerSettings(max_replan_attempts=1))
    goal = MissionGoal(
        mission_id="mars-aressim-plan-test",
        crew_size=20,
        horizon_years=3.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=1000.0,
        battery_capacity_kwh=5000.0,
        dust_degradation_fraction=0.3,
        hours_without_sun=22.0,
    )
    plan = planner.plan(goal=goal, evidence=())

    runner = AresSimRunner()
    report = runner.run_from_plan(plan, duration_sols=1)
    assert report.mission_id == "mars-aressim-plan-test"
    assert report.total_steps == 24
    assert len(report.step_history) == 24


def test_fastapi_aressim_endpoints() -> None:
    client = TestClient(aressim_app)
    
    # Test health
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "ok"

    # Test step simulation endpoint
    sample_req = _create_sample_request().model_dump()
    step_resp = client.post("/api/v1/simulate/step", json=sample_req)
    assert step_resp.status_code == 200
    data = step_resp.json()
    assert "environment_derivatives" in data
    assert "power_grid_state" in data
    assert "isru_yields" in data
    assert "habitat_thermal_and_life_support" in data
