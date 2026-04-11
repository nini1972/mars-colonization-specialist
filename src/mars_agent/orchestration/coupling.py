"""Cross-domain coupling checks for specialist module outputs."""

from __future__ import annotations

from dataclasses import dataclass

from mars_agent.orchestration.models import (
    ConflictSeverity,
    CrossDomainConflict,
    MitigationOption,
)
from mars_agent.specialists.contracts import ModuleResponse, Subsystem

# ---------------------------------------------------------------------------
# Conflict builder helpers (extracted to keep evaluate() ≤ 50 NLOC)
# ---------------------------------------------------------------------------


def _thermal_shortfall_conflict(
    combined: float, effective_generation: float
) -> CrossDomainConflict:
    return CrossDomainConflict(
        conflict_id="coupling.power_balance.thermal_shortfall",
        description=(
            "Combined ECLSS+ISRU+Thermal demand exceeds effective power generation "
            f"({combined:.2f} kW > {effective_generation:.2f} kW)."
        ),
        severity=ConflictSeverity.HIGH,
        impacted_subsystems=(
            Subsystem.ECLSS,
            Subsystem.ISRU,
            Subsystem.POWER,
            Subsystem.HABITAT_THERMODYNAMICS,
        ),
        mitigations=(
            MitigationOption(
                1, "Reduce ISRU throughput to free headroom for thermal regulation"
            ),
            MitigationOption(2, "Improve habitat insulation to lower HVAC power demand"),
            MitigationOption(3, "Increase dispatchable generation capacity"),
        ),
    )


def _power_shortfall_conflict(
    total_critical_load: float, effective_generation: float
) -> CrossDomainConflict:
    return CrossDomainConflict(
        conflict_id="coupling.power_balance.shortfall",
        description=(
            "Combined ECLSS+ISRU demand exceeds effective power generation "
            f"({total_critical_load:.2f} kW > {effective_generation:.2f} kW)."
        ),
        severity=ConflictSeverity.HIGH,
        impacted_subsystems=(Subsystem.ECLSS, Subsystem.ISRU, Subsystem.POWER),
        mitigations=(
            MitigationOption(1, "Reduce ISRU throughput during low-generation windows"),
            MitigationOption(
                2, "Add temporary battery reserve and defer non-critical tasks"
            ),
            MitigationOption(3, "Increase dispatchable generation capacity"),
        ),
    )


def _isru_power_share_conflict(
    isru_load: float, effective_generation: float
) -> CrossDomainConflict:
    return CrossDomainConflict(
        conflict_id="coupling.isru_power_share.high",
        description=(
            "ISRU consumes a high share of effective generation "
            f"({isru_load:.2f} kW of {effective_generation:.2f} kW)."
        ),
        severity=ConflictSeverity.MEDIUM,
        impacted_subsystems=(Subsystem.ISRU, Subsystem.POWER),
        mitigations=(
            MitigationOption(1, "Shift ISRU processing to peak generation windows"),
            MitigationOption(2, "Improve ISRU reactor efficiency before scale-up"),
        ),
    )


def _power_margin_conflict(load_margin: float) -> CrossDomainConflict:
    return CrossDomainConflict(
        conflict_id="coupling.power_margin.low",
        description=(
            f"Power load margin is below resilience target ({load_margin:.2f} kW < 5.00 kW)."
        ),
        severity=ConflictSeverity.MEDIUM,
        impacted_subsystems=(Subsystem.POWER, Subsystem.ECLSS),
        mitigations=(
            MitigationOption(
                1, "Increase battery reserve to protect life-support continuity"
            ),
            MitigationOption(2, "Reduce discretionary habitat loads during storms"),
        ),
    )


def _power_gate_conflict() -> CrossDomainConflict:
    return CrossDomainConflict(
        conflict_id="coupling.power_gate.failed",
        description=(
            "Power module validation gate failed under current coupling assumptions."
        ),
        severity=ConflictSeverity.HIGH,
        impacted_subsystems=(Subsystem.POWER,),
        mitigations=(
            MitigationOption(1, "Re-run planning with lower ISRU feedstock demand"),
            MitigationOption(
                2, "Raise storage and generation reserves before operations"
            ),
        ),
    )


# ---------------------------------------------------------------------------
# CouplingChecker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CouplingChecker:
    """Detects cross-domain conflicts and proposes ranked mitigations."""

    def evaluate(
        self,
        eclss: ModuleResponse,
        isru: ModuleResponse,
        power: ModuleResponse,
        thermal: ModuleResponse | None = None,
    ) -> tuple[CrossDomainConflict, ...]:
        conflicts: list[CrossDomainConflict] = []

        eclss_load = eclss.get_metric("eclss_power_demand_kw").value.mean
        isru_load = isru.get_metric("isru_power_demand_kw").value.mean
        effective_generation = power.get_metric("effective_generation_kw").value.mean
        load_margin = power.get_metric("load_margin_kw").value.mean
        thermal_load = (
            thermal.get_metric("thermal_power_demand_kw").value.mean
            if thermal is not None
            else 0.0
        )

        combined = eclss_load + isru_load + thermal_load
        if thermal is not None and combined > effective_generation:
            conflicts.append(_thermal_shortfall_conflict(combined, effective_generation))

        total_critical_load = eclss_load + isru_load
        if total_critical_load > effective_generation:
            conflicts.append(_power_shortfall_conflict(total_critical_load, effective_generation))

        if isru_load > 0.6 * max(effective_generation, 1e-6):
            conflicts.append(_isru_power_share_conflict(isru_load, effective_generation))

        if load_margin < 5.0:
            conflicts.append(_power_margin_conflict(load_margin))

        if not power.gate.accepted:
            conflicts.append(_power_gate_conflict())

        return tuple(conflicts)

