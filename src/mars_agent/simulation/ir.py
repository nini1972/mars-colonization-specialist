"""Intermediate representation (IR) for simulation generation in Phase 5."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VariableSpec:
    """Typed variable definition used by generated simulations."""

    name: str
    unit: str
    initial_value: float


@dataclass(frozen=True, slots=True)
class EquationSpec:
    """Equation specification compiled to executable simulation code."""

    output: str
    expression: str


@dataclass(frozen=True, slots=True)
class InterfaceSpec:
    """Declares external interface variables exposed by a simulation model."""

    inputs: tuple[str, ...]
    outputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Complete model specification for one generated simulation."""

    model_id: str
    variables: tuple[VariableSpec, ...]
    equations: tuple[EquationSpec, ...]
    assumptions: tuple[str, ...]
    interface: InterfaceSpec


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    """Scenario with multiplicative modifiers applied before simulation."""

    name: str
    description: str
    modifiers: dict[str, float]
