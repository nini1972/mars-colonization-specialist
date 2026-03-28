"""Audit trail exporter for mission review boards."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mars_agent.governance.models import BenchmarkReport, GovernanceReport
from mars_agent.orchestration.models import PlanResult
from mars_agent.simulation.pipeline import SimulationReport


@dataclass(slots=True)
class AuditTrailExporter:
    """Builds and exports deterministic audit records for governance review."""

    def build_record(
        self,
        plan: PlanResult,
        simulation: SimulationReport,
        governance: GovernanceReport,
        benchmark: BenchmarkReport,
    ) -> dict[str, object]:
        decisions: list[dict[str, object]] = []
        for response in plan.subsystem_responses:
            decisions.append(
                {
                    "subsystem": response.subsystem.value,
                    "recommendation": response.decision.recommendation,
                    "confidence": response.decision.confidence,
                    "evidence": [
                        {
                            "source_id": item.source_id,
                            "doc_id": item.doc_id,
                            "title": item.title,
                            "url": item.url,
                        }
                        for item in response.decision.evidence
                    ],
                    "gate_accepted": response.gate.accepted,
                }
            )

        scenarios = [
            {
                "name": item.scenario_name,
                "passed": item.passed,
                "issues": [issue.code for issue in item.issues],
                "remediation": list(item.remediation),
            }
            for item in simulation.scenarios
        ]

        return {
            "mission_id": plan.mission_id,
            "started_phase": plan.started_phase.value,
            "next_phase": plan.next_phase.value,
            "replan_events": [item.reason for item in plan.replan_events],
            "conflicts": [item.conflict_id for item in plan.conflicts],
            "decisions": decisions,
            "simulation": {
                "model_id": simulation.model_id,
                "seed": simulation.seed,
                "attempts": simulation.attempts,
                "scenarios": scenarios,
            },
            "governance": {
                "accepted": governance.accepted,
                "checked_items": governance.checked_items,
                "violations": [item.code for item in governance.violations],
            },
            "benchmark": {
                "passed": benchmark.passed,
                "deltas": [
                    {
                        "metric": item.metric,
                        "observed": item.observed,
                        "target": item.target,
                        "delta": item.delta,
                        "within_tolerance": item.within_tolerance,
                        "source": item.source,
                    }
                    for item in benchmark.deltas
                ],
            },
        }

    def export(
        self,
        record: dict[str, object],
        path: Path,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(record, indent=2, sort_keys=True)
        path.write_text(payload, encoding="utf-8")
