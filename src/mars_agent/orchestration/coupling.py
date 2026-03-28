"""Cross-domain coupling checks for specialist module outputs."""

from __future__ import annotations

from dataclasses import dataclass

from mars_agent.orchestration.models import (
    ConflictSeverity,
    CrossDomainConflict,
    MitigationOption,
)
from mars_agent.specialists.contracts import ModuleResponse, Subsystem


@dataclass(slots=True)
class CouplingChecker:
    """Detects cross-domain conflicts and proposes ranked mitigations."""

    def evaluate(
        self,
        eclss: ModuleResponse,
        isru: ModuleResponse,
        power: ModuleResponse,
    ) -> tuple[CrossDomainConflict, ...]:
        conflicts: list[CrossDomainConflict] = []

        eclss_load = eclss.get_metric("eclss_power_demand_kw").value.mean
        isru_load = isru.get_metric("isru_power_demand_kw").value.mean
        effective_generation = power.get_metric("effective_generation_kw").value.mean
        load_margin = power.get_metric("load_margin_kw").value.mean

        total_critical_load = eclss_load + isru_load
        if total_critical_load > effective_generation:
            conflicts.append(
                CrossDomainConflict(
                    conflict_id="coupling.power_balance.shortfall",
                    description=(
                        "Combined ECLSS+ISRU demand exceeds effective power generation "
                        f"({total_critical_load:.2f} kW > {effective_generation:.2f} kW)."
                    ),
                    severity=ConflictSeverity.HIGH,
                    impacted_subsystems=(Subsystem.ECLSS, Subsystem.ISRU, Subsystem.POWER),
                    mitigations=(
                        MitigationOption(
                            1,
                            "Reduce ISRU throughput during low-generation windows",
                        ),
                        MitigationOption(
                            2,
                            "Add temporary battery reserve and defer non-critical tasks",
                        ),
                        MitigationOption(3, "Increase dispatchable generation capacity"),
                    ),
                )
            )

        if isru_load > 0.6 * max(effective_generation, 1e-6):
            conflicts.append(
                CrossDomainConflict(
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
            )

        if load_margin < 5.0:
            conflicts.append(
                CrossDomainConflict(
                    conflict_id="coupling.power_margin.low",
                    description=(
                        "Power load margin is below resilience target "
                        f"({load_margin:.2f} kW < 5.00 kW)."
                    ),
                    severity=ConflictSeverity.MEDIUM,
                    impacted_subsystems=(Subsystem.POWER, Subsystem.ECLSS),
                    mitigations=(
                        MitigationOption(
                            1,
                            "Increase battery reserve to protect life-support continuity",
                        ),
                        MitigationOption(2, "Reduce discretionary habitat loads during storms"),
                    ),
                )
            )

        if not power.gate.accepted:
            conflicts.append(
                CrossDomainConflict(
                    conflict_id="coupling.power_gate.failed",
                    description=(
                        "Power module validation gate failed under current coupling assumptions."
                    ),
                    severity=ConflictSeverity.HIGH,
                    impacted_subsystems=(Subsystem.POWER,),
                    mitigations=(
                        MitigationOption(1, "Re-run planning with lower ISRU feedstock demand"),
                        MitigationOption(
                            2,
                            "Raise storage and generation reserves before operations",
                        ),
                    ),
                )
            )

        return tuple(conflicts)
