"""Tests for Track C — Specialist Capability Protocol (gaps #3 and #6)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from mars_agent.knowledge.models import TrustTier
from mars_agent.orchestration import CentralPlanner, SpecialistRegistry
from mars_agent.orchestration.models import (
    ConflictSeverity,
    CrossDomainConflict,
    MissionGoal,
    MissionPhase,
    MitigationOption,
)
from mars_agent.orchestration.negotiator import MultiAgentNegotiator
from mars_agent.orchestration.registry import _HasCapabilitiesAndAnalyze
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.specialists import (
    ECLSSSpecialist,
    HabitatThermodynamicsSpecialist,
    ISRUSpecialist,
    PowerSpecialist,
    SpecialistCapability,
    Subsystem,
    TradeoffKnob,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _evidence() -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            source_id="nasa-std",
            doc_id="nasa-track-c-001",
            title="Track C capability tests",
            url="https://example.org/nasa-track-c-001",
            published_on=date(2026, 2, 10),
            tier=TrustTier.AGENCY_STANDARD,
            relevance_score=0.91,
        ),
    )


def _make_conflict(conflict_id: str) -> CrossDomainConflict:
    return CrossDomainConflict(
        conflict_id=conflict_id,
        description="test conflict",
        severity=ConflictSeverity.HIGH,
        impacted_subsystems=(Subsystem.POWER, Subsystem.ISRU),
        mitigations=(MitigationOption(rank=1, action="reduce feedstock"),),
    )


# ---------------------------------------------------------------------------
# TradeoffKnob validation
# ---------------------------------------------------------------------------


def test_tradeoff_knob_rejects_negative_preferred_delta() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        TradeoffKnob(
            name="test",
            description="test",
            min_value=0.0,
            max_value=1.0,
            preferred_delta=-0.1,
            unit="",
        )


def test_tradeoff_knob_rejects_inverted_bounds() -> None:
    with pytest.raises(ValueError, match="min_value must be <= max_value"):
        TradeoffKnob(
            name="test",
            description="test",
            min_value=1.0,
            max_value=0.0,
            preferred_delta=0.1,
            unit="",
        )


# ---------------------------------------------------------------------------
# Each specialist's capabilities() returns the correct subsystem
# ---------------------------------------------------------------------------


def test_eclss_capabilities_returns_correct_subsystem() -> None:
    cap = ECLSSSpecialist().capabilities()
    assert isinstance(cap, SpecialistCapability)
    assert cap.subsystem is Subsystem.ECLSS


def test_isru_capabilities_returns_correct_subsystem() -> None:
    cap = ISRUSpecialist().capabilities()
    assert isinstance(cap, SpecialistCapability)
    assert cap.subsystem is Subsystem.ISRU


def test_power_capabilities_returns_correct_subsystem() -> None:
    cap = PowerSpecialist().capabilities()
    assert isinstance(cap, SpecialistCapability)
    assert cap.subsystem is Subsystem.POWER


def test_thermal_capabilities_returns_correct_subsystem() -> None:
    cap = HabitatThermodynamicsSpecialist().capabilities()
    assert isinstance(cap, SpecialistCapability)
    assert cap.subsystem is Subsystem.HABITAT_THERMODYNAMICS


# ---------------------------------------------------------------------------
# accepts_inputs matches the inputs each analyze() actually reads
# ---------------------------------------------------------------------------


def test_eclss_accepts_inputs_covers_all_required_keys() -> None:
    cap = ECLSSSpecialist().capabilities()
    required = {
        "crew_count",
        "o2_demand_per_crew_kg_day",
        "water_demand_per_crew_l_day",
        "recycle_efficiency",
        "thermal_load_kw",
        "o2_partial_pressure_kpa",
    }
    assert required == set(cap.accepts_inputs)


def test_isru_accepts_inputs_covers_all_required_keys() -> None:
    cap = ISRUSpecialist().capabilities()
    required = {
        "regolith_feed_kg_day",
        "ice_grade_fraction",
        "reactor_efficiency",
        "electrolysis_kwh_per_kg_o2",
        "available_power_kw",
    }
    assert required == set(cap.accepts_inputs)


def test_power_accepts_inputs_covers_all_required_keys() -> None:
    cap = PowerSpecialist().capabilities()
    required = {
        "solar_generation_kw",
        "battery_capacity_kwh",
        "critical_load_kw",
        "dust_degradation_fraction",
        "hours_without_sun",
    }
    assert required == set(cap.accepts_inputs)


def test_thermal_accepts_inputs_covers_all_required_keys() -> None:
    cap = HabitatThermodynamicsSpecialist().capabilities()
    required = {
        "crew_count",
        "solar_flux_w_m2",
        "habitat_area_m2",
        "insulation_r_value",
        "thermal_power_budget_kw",
    }
    assert required == set(cap.accepts_inputs)


# ---------------------------------------------------------------------------
# All TradeoffKnobs have valid preferred_delta (non-negative, within range)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "specialist",
    [
        ECLSSSpecialist(),
        ISRUSpecialist(),
        PowerSpecialist(),
        HabitatThermodynamicsSpecialist(),
    ],
)
def test_all_tradeoff_knobs_have_valid_preferred_delta(
    specialist: Any,
) -> None:
    cap = specialist.capabilities()
    for knob in cap.tradeoff_knobs:
        assert knob.preferred_delta >= 0.0, (
            f"{knob.name}: preferred_delta must be >= 0"
        )
        assert knob.min_value <= knob.max_value, (
            f"{knob.name}: min_value must be <= max_value"
        )


# ---------------------------------------------------------------------------
# SpecialistRegistry
# ---------------------------------------------------------------------------


def test_default_registry_contains_all_four_subsystems() -> None:
    registry = SpecialistRegistry.default()
    subsystems = set(registry.subsystems())
    assert subsystems == {
        Subsystem.ECLSS,
        Subsystem.ISRU,
        Subsystem.POWER,
        Subsystem.HABITAT_THERMODYNAMICS,
    }


def test_registry_all_capabilities_returns_four_entries() -> None:
    registry = SpecialistRegistry.default()
    caps = registry.all_capabilities()
    assert len(caps) == 4
    assert all(isinstance(c, SpecialistCapability) for c in caps)


def test_registry_register_raises_on_duplicate() -> None:
    registry = SpecialistRegistry.default()
    with pytest.raises(ValueError, match="already registered"):
        registry.register(ECLSSSpecialist())


def test_registry_get_raises_key_error_on_unknown_subsystem() -> None:
    registry = SpecialistRegistry()
    with pytest.raises(KeyError):
        registry.get(Subsystem.ECLSS)


def test_registry_get_returns_correct_specialist() -> None:
    registry = SpecialistRegistry.default()
    specialist = registry.get(Subsystem.ISRU)
    cap = specialist.capabilities()  # type: ignore[union-attr]
    assert cap.subsystem is Subsystem.ISRU


# ---------------------------------------------------------------------------
# CentralPlanner uses the registry
# ---------------------------------------------------------------------------


def test_central_planner_run_modules_produces_four_specialist_timings() -> None:
    planner = CentralPlanner()
    goal = MissionGoal(
        mission_id="track-c-smoke",
        crew_size=20,
        horizon_years=1.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=500.0,
        battery_capacity_kwh=5000.0,
        dust_degradation_fraction=0.1,
        hours_without_sun=14.0,
        desired_confidence=0.9,
    )
    result = planner.plan(goal, _evidence())
    assert len(result.specialist_timings) == 4
    assert all(t.latency_ms > 0.0 for t in result.specialist_timings)
    assert all(isinstance(t.gate_accepted, bool) for t in result.specialist_timings)


# ---------------------------------------------------------------------------
# _conflict_aware_fallback uses capability-driven path
# ---------------------------------------------------------------------------


def test_conflict_aware_fallback_uses_registry_capabilities_for_unknown_id() -> None:
    """An unknown conflict ID falls back to ISRU preferred_delta from the registry."""
    planner = CentralPlanner()
    # The ISRU specialist declares preferred_delta=0.05 for isru_reduction_fraction.
    # The default replan_feedstock_reduction is 0.2, so max(0.05, 0.2) = 0.2.
    conflicts = (_make_conflict("coupling.completely.unknown"),)
    new_reduction, crew_delta, dust_delta, rationale = planner._conflict_aware_fallback(
        conflicts, current_reduction=0.0
    )
    # Should use max(cap_isru_delta=0.05, replan_feedstock_reduction=0.2) = 0.2
    assert new_reduction == pytest.approx(0.2)
    assert crew_delta == 0
    assert dust_delta == 0.0


def test_conflict_aware_fallback_capability_driven_produces_nonzero_delta() -> None:
    """Registry capabilities are queried; at least one knob has preferred_delta > 0."""
    planner = CentralPlanner()
    caps = planner.registry.all_capabilities()
    all_knob_deltas = [k.preferred_delta for cap in caps for k in cap.tradeoff_knobs]
    assert any(d > 0.0 for d in all_knob_deltas), (
        "Expected at least one knob with preferred_delta > 0 across all specialists"
    )
    # Now run the fallback with a known conflict to confirm it produces a non-zero reduction
    conflicts = (_make_conflict("coupling.power_balance.shortfall"),)
    new_reduction, _, _, _ = planner._conflict_aware_fallback(
        conflicts, current_reduction=0.0
    )
    assert new_reduction > 0.0


# ---------------------------------------------------------------------------
# MultiAgentNegotiator._build_messages includes specialist preferences
# ---------------------------------------------------------------------------


def test_build_messages_includes_knob_descriptions_when_capabilities_provided() -> None:
    negotiator = MultiAgentNegotiator()
    goal = MissionGoal(
        mission_id="track-c-msg",
        crew_size=10,
        horizon_years=1.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=200.0,
        battery_capacity_kwh=2000.0,
        dust_degradation_fraction=0.05,
        hours_without_sun=12.0,
    )
    conflicts = (_make_conflict("coupling.power_balance.shortfall"),)
    caps = SpecialistRegistry.default().all_capabilities()

    messages = negotiator._build_messages(
        goal=goal,
        conflicts=conflicts,
        current_reduction=0.0,
        history=(),
        capabilities=caps,
    )
    system_content = messages[0]["content"]
    assert isinstance(system_content, str)
    assert "Specialist preferences" in system_content
    assert "isru_reduction_fraction" in system_content
    assert "preferred_delta" in system_content


def test_build_messages_without_capabilities_has_no_specialist_preferences() -> None:
    negotiator = MultiAgentNegotiator()
    goal = MissionGoal(
        mission_id="track-c-msg-no-cap",
        crew_size=10,
        horizon_years=1.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=200.0,
        battery_capacity_kwh=2000.0,
        dust_degradation_fraction=0.05,
        hours_without_sun=12.0,
    )
    conflicts = (_make_conflict("coupling.power_balance.shortfall"),)

    messages = negotiator._build_messages(
        goal=goal,
        conflicts=conflicts,
        current_reduction=0.0,
        history=(),
        # no capabilities
    )
    system_content = messages[0]["content"]
    assert isinstance(system_content, str)
    assert "Specialist preferences" not in system_content
