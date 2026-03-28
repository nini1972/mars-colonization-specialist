"""Phase 5 simulation generation package."""

from mars_agent.simulation.compiler import SimulationArtifact, compile_model
from mars_agent.simulation.ir import (
    EquationSpec,
    InterfaceSpec,
    ModelSpec,
    ScenarioSpec,
    VariableSpec,
)
from mars_agent.simulation.pipeline import (
    ScenarioRunResult,
    SimulationPipeline,
    SimulationReport,
)
from mars_agent.simulation.scenarios import default_scenarios
from mars_agent.simulation.validation import ValidationIssue

__all__ = [
    "EquationSpec",
    "InterfaceSpec",
    "ModelSpec",
    "ScenarioRunResult",
    "ScenarioSpec",
    "SimulationArtifact",
    "SimulationPipeline",
    "SimulationReport",
    "ValidationIssue",
    "VariableSpec",
    "compile_model",
    "default_scenarios",
]
