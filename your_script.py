from uuid import uuid4

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main() -> None:
    run_id = uuid4().hex[:8]
    async with streamable_http_client("http://localhost:8000/mcp") as (
        read_stream,
        write_stream,
        get_session_id,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            print("session:", get_session_id())

            tools = await session.list_tools()
            print("tools:", [tool.name for tool in tools.tools])

            plan_result = await session.call_tool(
                "mars.plan",
                {
                    "goal": {
                        "mission_id": "mars-phase8-transport",
                        "crew_size": 100,
                        "horizon_years": 5.0,
                        "current_phase": "early_operations",
                        "solar_generation_kw": 1200.0,
                        "battery_capacity_kwh": 24000.0,
                        "dust_degradation_fraction": 0.2,
                        "hours_without_sun": 20.0,
                        "desired_confidence": 0.9,
                    },
                    "evidence": [
                        {
                            "source_id": "nasa-std",
                            "doc_id": "nasa-phase8-001",
                            "title": "Phase 8 MCP transport evidence",
                            "url": "https://example.org/nasa-phase8-001",
                            "published_on": "2026-03-28",
                            "tier": 1,
                            "relevance_score": 0.97,
                        }
                    ],
                    "request_id": f"req-{run_id}-plan",
                },
            )
            print("plan:", plan_result.structuredContent)

            plan_id = plan_result.structuredContent["plan_id"]

            sim_result = await session.call_tool(
                "mars.simulate",
                {
                    "plan_id": plan_id,
                    "seed": 42,
                    "max_repair_attempts": 2,
                    "request_id": f"req-{run_id}-sim",
                },
            )
            print("simulate:", sim_result.structuredContent)

            simulation_id = sim_result.structuredContent["simulation_id"]

            gov_result = await session.call_tool(
                "mars.governance",
                {
                    "plan_id": plan_id,
                    "simulation_id": simulation_id,
                    "min_confidence": 0.75,
                    "request_id": f"req-{run_id}-gov",
                },
            )
            print("governance:", gov_result.structuredContent)

            bench_result = await session.call_tool(
                "mars.benchmark",
                {
                    "plan_id": plan_id,
                    "simulation_id": simulation_id,
                    "request_id": f"req-{run_id}-bench",
                },
            )
            print("benchmark:", bench_result.structuredContent)


if __name__ == "__main__":
    anyio.run(main)