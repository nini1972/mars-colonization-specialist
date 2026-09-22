"""AresSim empirical physical simulation engine for Mars environments.

Derived from Flowith Neon Agent empirical calibration against NASA PDS, MCD v6.1,
Perseverance MEDA, MOXIE telemetry, and SWIM Arcadia Planitia ice datasets.
"""

from __future__ import annotations

import math
from typing import Any
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class SimulationMetadata(BaseModel):
    """Metadata tracking temporal progression of the simulation."""

    step_duration_seconds: float = Field(..., ge=1.0, le=88775.0)
    sol_number: int = Field(..., ge=0)
    ls_degrees: float = Field(..., ge=0.0, le=360.0)
    hour_of_sol: float = Field(default=12.0, ge=0.0, le=25.0)


class EnvironmentState(BaseModel):
    """Atmospheric and geographic boundary conditions."""

    latitude_degrees: float = Field(..., ge=-90.0, le=90.0)
    longitude_degrees: float = Field(..., ge=0.0, le=360.0)
    dust_optical_depth: float = Field(..., ge=0.1, le=10.0)
    ambient_temperature_k: float = Field(..., ge=120.0, le=300.0)
    wind_speed_m_s: float = Field(..., ge=0.0, le=100.0)


class PowerGridControls(BaseModel):
    """Operating parameters for surface power generation and storage."""

    pv_total_aperture_m2: float = Field(..., ge=0.0)
    fsp_active_reactors: int = Field(..., ge=0, le=10)
    bess_current_soc: float = Field(..., ge=0.0, le=1.0)
    trigger_pv_clean_event: bool = False


class ISRUSubsystemDemands(BaseModel):
    """Target production rates for in-situ resource extraction."""

    target_o2_production_g_hr: float = Field(..., ge=0.0)
    target_water_extraction_kg_hr: float = Field(..., ge=0.0)
    target_methane_production_g_hr: float = Field(..., ge=0.0)


class ECLSSParameters(BaseModel):
    """Crew life support and habitat envelope parameters."""

    crew_count: int = Field(..., ge=0)
    target_internal_temp_k: float = Field(default=294.15, ge=280.0, le=298.0)
    aerogel_insulation_thickness_m: float = Field(default=0.1, ge=0.01, le=0.5)
    regolith_shielding_meters: float = Field(default=2.5, ge=0.0, le=10.0)


class AresSimStepRequest(BaseModel):
    """Input payload for a single time-stepped simulation slice."""

    simulation_metadata: SimulationMetadata
    environment_state: EnvironmentState
    power_grid_controls: PowerGridControls
    isru_subsystem_demands: ISRUSubsystemDemands
    eclss_parameters: ECLSSParameters


class EnvironmentDerivatives(BaseModel):
    """Computed local environmental metrics for the step."""

    solar_zenith_angle_rad: float
    global_horizontal_irradiance_w_m2: float
    local_pressure_pa: float


class PowerGridState(BaseModel):
    """Power generation, distribution, and battery state for the step."""

    pv_power_generated_kw: float
    fsp_power_generated_kw: float
    bess_thermal_power_loss_kw: float
    net_bess_power_flow_kw: float
    new_bess_soc: float
    dust_degradation_factor: float


class ISRUYields(BaseModel):
    """Mass accumulation and power draw for in-situ chemical plants."""

    actual_o2_produced_g: float
    actual_water_extracted_kg: float
    actual_methane_produced_g: float
    power_consumed_isru_kw: float


class HabitatThermalAndLifeSupport(BaseModel):
    """Internal habitat thermal state and life support mass balances."""

    habitat_temperature_k: float
    heat_loss_environment_kw: float
    supplemental_hvac_power_kw: float
    mass_water_deficit_kg: float
    accumulated_co2_vent_g: float


class AresSimStepResponse(BaseModel):
    """Aggregate simulation state returned after stepping the physical engine."""

    environment_derivatives: EnvironmentDerivatives
    power_grid_state: PowerGridState
    isru_yields: ISRUYields
    habitat_thermal_and_life_support: HabitatThermalAndLifeSupport


class PhysicalEngine:
    """Core deterministic physical modeling engine for Mars surface conditions."""

    def __init__(self) -> None:
        self.e = 0.093412  # Mars orbital eccentricity
        self.p_orb = 668.599  # Orbital period in Sols
        self.mean_motion = 360.0 / self.p_orb  # Degrees per sol
        self.m_0 = 19.3807  # Mean anomaly at epoch
        self.omega = 7.0776e-5  # Martian planetary rotation rate (rad/s)
        self.solar_const = 589.2  # Solar constant at 1.524 AU (W/m^2)
        self.obliquity = 25.19  # Axial tilt (degrees)

        # ISRU Energetics calibrated from MOXIE and literature
        self.o2_specific_energy_wh_per_g = 9.0  # MOXIE SOXE (~9 Wh/g O2)
        self.h2o_extraction_energy_kwh_per_kg = 2.0  # Thermal ice extraction
        self.sabatier_reaction_energy_kwh_per_kg_ch4 = 0.45  # Methanation power

    def solve_kepler(self, m_deg: float) -> float:
        """Solve Kepler's equation for eccentric anomaly E using Newton-Raphson."""
        m_rad = math.radians(m_deg)
        e_ecc = m_rad
        for _ in range(8):
            delta = (e_ecc - self.e * math.sin(e_ecc) - m_rad) / (
                1.0 - self.e * math.cos(e_ecc)
            )
            e_ecc -= delta
            if abs(delta) < 1e-7:
                break
        return e_ecc

    def solve_solar_geometry(
        self,
        sol: float,
        hour: float,
        latitude_deg: float,
        longitude_deg: float,
        tau: float,
        use_local_solar_time: bool = True,
    ) -> tuple[float, float, float]:
        """Compute solar longitude (Ls), zenith angle (theta_z), and global horizontal irradiance (GHI)."""
        m = (self.m_0 + self.mean_motion * sol) % 360.0
        e_ecc = self.solve_kepler(m)
        f = 2.0 * math.atan2(
            math.sqrt(1.0 + self.e) * math.sin(e_ecc / 2.0),
            math.sqrt(1.0 - self.e) * math.cos(e_ecc / 2.0),
        )
        f_deg = math.degrees(f)
        ls = (f_deg + 251.0) % 360.0
        if ls < 0:
            ls += 360.0

        delta_deg = math.degrees(
            math.asin(
                math.sin(math.radians(self.obliquity)) * math.sin(math.radians(ls))
            )
        )
        if use_local_solar_time:
            # Local True Solar Time: local solar noon (zenith maximum) occurs at hour 12:00
            hour_angle = (hour - 12.0) * (math.pi / 12.0)
        else:
            sol_sec = hour * 3600.0
            hour_angle = self.omega * (sol_sec - 44387.6) + math.radians(longitude_deg)

        lat_rad = math.radians(latitude_deg)
        delta_rad = math.radians(delta_deg)
        cos_theta_z = math.sin(lat_rad) * math.sin(delta_rad) + math.cos(
            lat_rad
        ) * math.cos(delta_rad) * math.cos(hour_angle)
        cos_theta_z_clamped = max(-1.0, min(1.0, cos_theta_z))
        theta_z = math.acos(cos_theta_z_clamped)

        # Orbital distance flux scaling
        r_ratio = (1.0 + self.e * math.cos(math.radians(ls - 251.0))) / (
            1.0 - self.e**2
        )
        i_0 = self.solar_const * (r_ratio**2)

        ghi = 0.0
        if cos_theta_z_clamped > 0.0:
            airmass = 1.0 / max(cos_theta_z_clamped, 0.05)
            i_dir = i_0 * cos_theta_z_clamped * math.exp(-tau * airmass)
            # Diffuse scattering formulation calibrated to MCD v6.1
            i_dif = (
                i_0
                * cos_theta_z_clamped
                * (0.5 * (1.0 - math.exp(-tau * airmass)) * (1.0 - 0.9 * 0.68))
            )
            ghi = max(0.0, i_dir + i_dif)

        return ls, theta_z, ghi

    def calculate_habitat_u_value(
        self,
        aerogel_thickness_m: float,
        regolith_thickness_m: float,
        wind_speed_m_s: float,
    ) -> float:
        """Compute overall heat transfer coefficient U (W / m^2-K) for multi-layer habitat wall."""
        r_int = 0.13  # Interior convection & radiation resistance
        r_shell = 0.008 / 120.0  # 8mm aluminum pressure vessel (k = 120 W/m-K)
        r_aerogel = aerogel_thickness_m / 0.015  # Silica aerogel blanket (k = 0.015 W/m-K)
        r_regolith = regolith_thickness_m / 0.180  # Compacted Martian regolith (k = 0.180 W/m-K)
        r_ext = 1.0 / max(1.0, 10.0 + 4.2 * wind_speed_m_s)  # Exterior convective film
        inv_u = r_int + r_shell + r_aerogel + r_regolith + r_ext
        return 1.0 / max(inv_u, 1e-4)


def simulate_step(request: AresSimStepRequest) -> AresSimStepResponse:
    """Execute a single physical simulation step for the given parameters."""
    engine = PhysicalEngine()
    meta = request.simulation_metadata
    env = request.environment_state
    power_ctrl = request.power_grid_controls
    isru_demand = request.isru_subsystem_demands
    eclss_param = request.eclss_parameters

    step_hours = meta.step_duration_seconds / 3600.0
    hour_of_sol = meta.hour_of_sol

    ls, theta_z, ghi = engine.solve_solar_geometry(
        sol=float(meta.sol_number),
        hour=hour_of_sol,
        latitude_deg=env.latitude_degrees,
        longitude_deg=env.longitude_degrees,
        tau=env.dust_optical_depth,
        use_local_solar_time=True,
    )


    # Local barometric pressure oscillation (MCD v6.1 model: ~750 +/- 100 Pa seasonal, +/- 15 Pa diurnal)
    t_rad = hour_of_sol * math.pi / 12.0
    p_mean = 750.0 + 100.0 * math.sin(math.radians(ls))
    local_pressure_pa = p_mean + 15.0 * math.cos(2.0 * t_rad - 0.8)

    # Solar array dust degradation & cleaning
    if power_ctrl.trigger_pv_clean_event:
        dust_factor = 0.98
    else:
        # Base factor slightly decaying with tau
        dust_factor = max(0.2, 0.98 - 0.08 * (env.dust_optical_depth - 0.4))

    t_pv = env.ambient_temperature_k + 0.035 * ghi
    pv_eff = 0.28 * max(0.1, (1.0 - 0.0022 * (t_pv - 298.15)))
    pv_kw = max(
        0.0,
        power_ctrl.pv_total_aperture_m2
        * pv_eff
        * ghi
        * dust_factor
        / 1000.0,
    )

    # FSP Stirling Microreactor: 40 kW nominal per unit, slight efficiency gain in cold Mars sink
    fsp_eff = 1.0 - 0.0012 * (env.ambient_temperature_k - 200.0)
    fsp_kw = power_ctrl.fsp_active_reactors * 40.0 * max(0.7, min(1.2, fsp_eff))

    # BESS thermal loss to ambient
    bess_thermal_loss_kw = max(
        0.0, 0.15 * 150.0 * (285.0 - env.ambient_temperature_k) / 1000.0
    )

    # ISRU power consumption & yields
    target_o2_hr = isru_demand.target_o2_production_g_hr
    o2_power_req = (target_o2_hr * engine.o2_specific_energy_wh_per_g) / 1000.0
    actual_o2_g = target_o2_hr * step_hours

    target_h2o_hr = isru_demand.target_water_extraction_kg_hr
    h2o_power_req = target_h2o_hr * engine.h2o_extraction_energy_kwh_per_kg
    actual_h2o_kg = target_h2o_hr * step_hours

    target_ch4_hr = isru_demand.target_methane_production_g_hr
    actual_methane_produced_g = target_ch4_hr * step_hours
    ch4_power_req = (
        target_ch4_hr
        / 1000.0
        * engine.sabatier_reaction_energy_kwh_per_kg_ch4
    )

    total_isru_kw = o2_power_req + h2o_power_req + ch4_power_req

    # Habitat Thermal & Life Support mass balances
    metabolic_heat_kw = eclss_param.crew_count * 0.12  # 120 W per person
    avionics_heat_kw = 4.5  # Base housekeeping avionics

    u_hab = engine.calculate_habitat_u_value(
        aerogel_thickness_m=eclss_param.aerogel_insulation_thickness_m,
        regolith_thickness_m=eclss_param.regolith_shielding_meters,
        wind_speed_m_s=env.wind_speed_m_s,
    )
    a_hab = 950.0  # m^2 surface area
    heat_loss_kw = max(
        0.0,
        u_hab
        * a_hab
        * (eclss_param.target_internal_temp_k - env.ambient_temperature_k)
        / 1000.0,
    )

    # Supplemental HVAC active heating
    passive_heat_gain_kw = metabolic_heat_kw + avionics_heat_kw
    supplemental_hvac_kw = max(0.0, heat_loss_kw - passive_heat_gain_kw)

    # Net electrical load & battery flow
    total_generation_kw = pv_kw + fsp_kw
    total_demand_kw = (
        total_isru_kw
        + supplemental_hvac_kw
        + avionics_heat_kw
        + bess_thermal_loss_kw
    )
    net_bess_power_flow_kw = total_generation_kw - total_demand_kw

    # BESS update (3500 kWh nominal battery capacity, 88% roundtrip efficiency)
    bess_capacity_kwh = 3500.0
    current_charge_kwh = power_ctrl.bess_current_soc * bess_capacity_kwh
    roundtrip_factor = math.sqrt(0.88)
    if net_bess_power_flow_kw >= 0:
        actual_flow = net_bess_power_flow_kw * roundtrip_factor
    else:
        actual_flow = net_bess_power_flow_kw / roundtrip_factor

    new_charge_kwh = max(
        0.0, min(bess_capacity_kwh, current_charge_kwh + actual_flow * step_hours)
    )
    new_bess_soc = new_charge_kwh / bess_capacity_kwh

    # Crew consumption mass metrics
    water_deficit_kg = (
        eclss_param.crew_count * (2.5 / 24.0) * step_hours * 0.05
    )  # 5% unrecovered
    accumulated_co2_vent_g = (
        eclss_param.crew_count * (1000.0 / 24.0) * step_hours
    )

    return AresSimStepResponse(
        environment_derivatives=EnvironmentDerivatives(
            solar_zenith_angle_rad=theta_z,
            global_horizontal_irradiance_w_m2=ghi,
            local_pressure_pa=local_pressure_pa,
        ),
        power_grid_state=PowerGridState(
            pv_power_generated_kw=pv_kw,
            fsp_power_generated_kw=fsp_kw,
            bess_thermal_power_loss_kw=bess_thermal_loss_kw,
            net_bess_power_flow_kw=net_bess_power_flow_kw,
            new_bess_soc=new_bess_soc,
            dust_degradation_factor=dust_factor,
        ),
        isru_yields=ISRUYields(
            actual_o2_produced_g=actual_o2_g,
            actual_water_extracted_kg=actual_h2o_kg,
            actual_methane_produced_g=actual_methane_produced_g,
            power_consumed_isru_kw=total_isru_kw,
        ),
        habitat_thermal_and_life_support=HabitatThermalAndLifeSupport(
            habitat_temperature_k=eclss_param.target_internal_temp_k,
            heat_loss_environment_kw=heat_loss_kw,
            supplemental_hvac_power_kw=supplemental_hvac_kw,
            mass_water_deficit_kg=water_deficit_kg,
            accumulated_co2_vent_g=accumulated_co2_vent_g,
        ),
    )


# FastAPI microservice definition
app = FastAPI(
    title="AresSim Engineering Simulation API",
    version="1.5.0",
    description="Deterministic empirical Mars physical simulation service for multi-agent colony planning.",
)
aressim_app = app


@app.get("/health")
def health() -> dict[str, str]:
    """Healthcheck endpoint."""
    return {"status": "ok", "engine": "AresSim-1.5"}


@app.post("/api/v1/simulate/step", response_model=AresSimStepResponse)
def api_simulate_step(request: AresSimStepRequest) -> AresSimStepResponse:
    """HTTP endpoint to execute one simulation step."""
    try:
        return simulate_step(request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
