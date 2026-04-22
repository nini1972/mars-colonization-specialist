"""Agent registry — runtime catalogue of specialist instances."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, cast

from mars_agent.specialists.contracts import (
    ModuleRequest,
    ModuleResponse,
    SpecialistCapability,
    Subsystem,
    TradeoffProposal,
    TradeoffReview,
)


class _HasCapabilitiesAndAnalyze(Protocol):
    def capabilities(self) -> SpecialistCapability: ...
    def analyze(self, request: ModuleRequest) -> ModuleResponse: ...
    def propose_tradeoffs(self, conflict_ids: tuple[str, ...]) -> tuple[TradeoffProposal, ...]: ...
    def review_peer_proposals(
        self,
        proposals: tuple[TradeoffProposal, ...],
        conflict_ids: tuple[str, ...],
    ) -> tuple[TradeoffReview, ...]: ...


@dataclass
class SpecialistRegistry:
    """Runtime catalogue of specialist instances, keyed by subsystem.

    Specialists can be added and removed without touching ``CentralPlanner``
    or any other orchestration code — addressing gap #6 from issue #13.
    """

    _specialists: dict[Subsystem, _HasCapabilitiesAndAnalyze] = field(
        default_factory=dict, repr=False
    )

    def register(self, specialist: _HasCapabilitiesAndAnalyze) -> None:
        """Register a specialist by its declared subsystem.

        Raises:
            ValueError: if a specialist for that subsystem is already registered.
        """
        subsystem = specialist.capabilities().subsystem
        if subsystem in self._specialists:
            raise ValueError(
                f"A specialist for subsystem '{subsystem}' is already registered. "
                "Deregister it first or pass a custom registry."
            )
        self._specialists[subsystem] = specialist

    def get(self, subsystem: Subsystem) -> _HasCapabilitiesAndAnalyze:
        """Return the specialist for *subsystem*.

        Raises:
            KeyError: if no specialist is registered for that subsystem.
        """
        try:
            return self._specialists[subsystem]
        except KeyError:
            raise KeyError(
                f"No specialist registered for subsystem '{subsystem}'."
            ) from None

    def all_capabilities(self) -> tuple[SpecialistCapability, ...]:
        """Return the capability descriptors of all registered specialists."""
        return tuple(s.capabilities() for s in self._specialists.values())

    def subsystems(self) -> tuple[Subsystem, ...]:
        """Return the subsystem keys of all registered specialists."""
        return tuple(self._specialists.keys())

    @classmethod
    def default(cls) -> SpecialistRegistry:
        """Construct a registry pre-populated with the four canonical specialists."""
        from mars_agent.specialists.eclss import ECLSSSpecialist
        from mars_agent.specialists.habitat_thermodynamics import HabitatThermodynamicsSpecialist
        from mars_agent.specialists.isru import ISRUSpecialist
        from mars_agent.specialists.power import PowerSpecialist

        registry = cls()
        registry.register(cast(_HasCapabilitiesAndAnalyze, ECLSSSpecialist()))
        registry.register(cast(_HasCapabilitiesAndAnalyze, ISRUSpecialist()))
        registry.register(cast(_HasCapabilitiesAndAnalyze, PowerSpecialist()))
        registry.register(
            cast(_HasCapabilitiesAndAnalyze, HabitatThermodynamicsSpecialist())
        )
        return registry
