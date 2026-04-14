"""Typed models for Phase 4 orchestration and mission-phase planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from mars_agent.reasoning.models import EvidenceReference
from mars_agent.specialists.contracts import ModuleResponse, Subsystem


class MissionPhase(StrEnum):
    """Mission phases tracked by the orchestrator state machine."""

    TRANSIT = "transit"
    LANDING = "landing"
    EARLY_OPERATIONS = "early_operations"
    SCALING = "scaling"


class ConflictSeverity(StrEnum):
    """Severity labels for cross-domain coupling conflicts."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class MitigationOption:
    """A ranked mitigation option for a detected conflict."""

    rank: int
    action: str


@dataclass(frozen=True, slots=True)
class CrossDomainConflict:
    """Conflict detected across subsystem outputs."""

    conflict_id: str
    description: str
    severity: ConflictSeverity
    impacted_subsystems: tuple[Subsystem, ...]
    mitigations: tuple[MitigationOption, ...]


@dataclass(frozen=True, slots=True)
class MissionGoal:
    """Top-level mission planning goal provided to the central planner."""

    mission_id: str
    crew_size: int
    horizon_years: float
    current_phase: MissionPhase
    solar_generation_kw: float
    battery_capacity_kwh: float
    dust_degradation_fraction: float
    hours_without_sun: float
    desired_confidence: float = 0.9


@dataclass(frozen=True, slots=True)
class ReplanEvent:
    """Captures a replanning iteration and why it happened."""

    attempt: int
    reason: str


@dataclass(frozen=True, slots=True)
class SpecialistTiming:
    """Per-specialist latency and gate result captured during one planning run."""

    subsystem_name: str
    latency_ms: float
    gate_accepted: bool
    failed: bool = False
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PlanResult:
    """Integrated output from one planning cycle."""

    mission_id: str
    started_phase: MissionPhase
    next_phase: MissionPhase
    subsystem_responses: tuple[ModuleResponse, ...]
    conflicts: tuple[CrossDomainConflict, ...]
    replan_events: tuple[ReplanEvent, ...] = ()
    specialist_timings: tuple[SpecialistTiming, ...] = ()
    degraded: bool = False


@dataclass(slots=True)
class PlannerSettings:
    """Planner control settings for deterministic replanning behavior."""

    max_replan_attempts: int = 2
    replan_feedstock_reduction: float = 0.2
    conflict_knob_overrides: dict[str, tuple[float, int, float]] = field(default_factory=dict)
    negotiation_store_path: str | None = None


@dataclass(frozen=True, slots=True)
class ReadinessSignals:
    """Signals used by the state machine to transition mission phases."""

    landing_site_validated: bool
    surface_power_stable: bool
    life_support_stable: bool
    resource_surplus_ratio: float
    additional_signals: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanningContext:
    """Planning context wrapper with shared evidence inputs."""

    goal: MissionGoal
    evidence: tuple[EvidenceReference, ...]
