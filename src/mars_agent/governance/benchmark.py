"""Benchmark harness comparing outputs against selected NASA/ESA references."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mars_agent.config import load_release_policy_config
from mars_agent.governance.models import (
    BenchmarkDelta,
    BenchmarkReference,
    BenchmarkReport,
)
from mars_agent.orchestration.models import PlanResult
from mars_agent.simulation.pipeline import SimulationReport

_DEFAULT_RELEASE_POLICY_PATH = Path("configs/knowledge_release_policy.toml")


def default_references() -> tuple[BenchmarkReference, ...]:
    """Default benchmark references and tolerances for mission review."""

    return (
        BenchmarkReference(
            metric="load_margin_kw",
            target=15.0,
            tolerance=15.0,
            source="NASA Mars Habitat Power Margin Guidance",
        ),
        BenchmarkReference(
            metric="resource_surplus_ratio",
            target=1.2,
            tolerance=0.35,
            source="ESA Exploration Readiness Baseline",
        ),
        BenchmarkReference(
            metric="storage_cover_hours",
            target=20.0,
            tolerance=6.0,
            source="NASA Dust-Storm Operations Envelope",
        ),
    )


@dataclass(slots=True)
class BenchmarkHarness:
    """Computes metric deltas against benchmark references."""

    profile: str = "nasa-esa-mission-review"
    policy_version: str = "2026.03"
    policy_source: str = "NASA/ESA mission review benchmark bundle"
    references: tuple[BenchmarkReference, ...] = field(default_factory=default_references)

    @classmethod
    def from_policy_profile(
        cls,
        profile: str | None = None,
        *,
        policy_path: Path = _DEFAULT_RELEASE_POLICY_PATH,
    ) -> BenchmarkHarness:
        """Build a harness from a named benchmark policy profile in config."""

        config = load_release_policy_config(policy_path)
        selected_name = profile or config.default_benchmark_profile
        for item in config.benchmark_profiles:
            if item.name != selected_name:
                continue
            return cls(
                profile=item.name,
                policy_version=item.policy_version,
                policy_source=item.policy_source,
                references=tuple(
                    BenchmarkReference(
                        metric=reference.metric,
                        target=reference.target,
                        tolerance=reference.tolerance,
                        source=reference.source,
                    )
                    for reference in item.references
                ),
            )

        raise ValueError(f"Unknown benchmark profile: {selected_name}")

    def _observed_metrics(self, plan: PlanResult, simulation: SimulationReport) -> dict[str, float]:
        observed: dict[str, float] = {}

        for scenario in simulation.scenarios:
            if scenario.scenario_name == "nominal":
                observed.update(scenario.outputs)
                break

        for response in plan.subsystem_responses:
            for metric in response.metrics:
                observed[metric.name] = metric.value.mean

        return observed

    def run(self, plan: PlanResult, simulation: SimulationReport) -> BenchmarkReport:
        observed = self._observed_metrics(plan, simulation)
        deltas: list[BenchmarkDelta] = []

        for reference in self.references:
            value = observed.get(reference.metric, float("nan"))
            if value != value:
                deltas.append(
                    BenchmarkDelta(
                        metric=reference.metric,
                        observed=value,
                        target=reference.target,
                        delta=float("inf"),
                        within_tolerance=False,
                        source=reference.source,
                    )
                )
                continue

            delta = value - reference.target
            within_tolerance = abs(delta) <= reference.tolerance
            deltas.append(
                BenchmarkDelta(
                    metric=reference.metric,
                    observed=value,
                    target=reference.target,
                    delta=delta,
                    within_tolerance=within_tolerance,
                    source=reference.source,
                )
            )

        passed = all(item.within_tolerance for item in deltas)
        return BenchmarkReport(
            passed=passed,
            profile=self.profile,
            policy_version=self.policy_version,
            policy_source=self.policy_source,
            deltas=tuple(deltas),
        )
