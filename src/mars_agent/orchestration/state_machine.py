"""Mission-phase state machine for transit, landing, early ops, and scaling."""

from __future__ import annotations

from dataclasses import dataclass

from mars_agent.orchestration.models import MissionPhase, ReadinessSignals


@dataclass(slots=True)
class MissionPhaseStateMachine:
    """Determines the next mission phase based on readiness signals."""

    def next_phase(self, current: MissionPhase, readiness: ReadinessSignals) -> MissionPhase:
        if current is MissionPhase.TRANSIT:
            if readiness.landing_site_validated:
                return MissionPhase.LANDING
            return MissionPhase.TRANSIT

        if current is MissionPhase.LANDING:
            if readiness.surface_power_stable and readiness.life_support_stable:
                return MissionPhase.EARLY_OPERATIONS
            return MissionPhase.LANDING

        if current is MissionPhase.EARLY_OPERATIONS:
            if (
                readiness.surface_power_stable
                and readiness.life_support_stable
                and readiness.resource_surplus_ratio >= 1.2
            ):
                return MissionPhase.SCALING
            return MissionPhase.EARLY_OPERATIONS

        return MissionPhase.SCALING
