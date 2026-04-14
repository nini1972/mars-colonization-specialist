import json

import anyio

from mars_agent.mcp import server
from mars_agent.specialists import Subsystem
from tests.unit.test_central_planner import _registry_with_failing

server._adapter.planner.registry = _registry_with_failing(Subsystem.ECLSS)

async def main() -> None:
    response = await server.mars_plan(
        goal={
            "mission_id": "mars-degraded-demo",
            "crew_size": 100,
            "horizon_years": 5.0,
            "current_phase": "early_operations",
            "solar_generation_kw": 1200.0,
            "battery_capacity_kwh": 24000.0,
            "dust_degradation_fraction": 0.2,
            "hours_without_sun": 20.0,
            "desired_confidence": 0.9,
        },
        evidence=[
            {
                "source_id": "nasa-std",
                "doc_id": "nasa-phase4-001",
                "title": "Phase 4 planner evidence",
                "url": "https://example.org/nasa-phase4-001",
                "published_on": "2026-02-10",
                "tier": 1,
                "relevance_score": 0.94,
            }
        ],
        request_id="req-degraded-demo",
    )
    print(json.dumps(response, indent=2, default=str))

anyio.run(main)
