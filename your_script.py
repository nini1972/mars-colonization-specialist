from uuid import uuid4

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def _payload(result: object) -> dict[str, object] | None:
    structured = getattr(result, "structuredContent", None)
    return structured if isinstance(structured, dict) else None


def _describe_error(result: object) -> str:
    if not getattr(result, "isError", False):
        return ""
    content = getattr(result, "content", None)
    return str(content) if content is not None else "unknown tool error"


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
            plan_payload = _payload(plan_result)
            print("plan:", plan_payload)
            if plan_payload is None:
                print("plan returned no structured payload", _describe_error(plan_result))
                return

            if bool(plan_payload.get("degraded", False)):
                print("plan is degraded; skipping simulate/governance/benchmark")
                print("faults:", plan_payload.get("specialist_faults", []))
                return

            plan_id = plan_payload.get("plan_id")
            if not isinstance(plan_id, str):
                print("plan_id missing from plan payload")
                return

            sim_result = await session.call_tool(
                "mars.simulate",
                {
                    "plan_id": plan_id,
                    "seed": 42,
                    "max_repair_attempts": 2,
                    "request_id": f"req-{run_id}-sim",
                },
            )
            sim_payload = _payload(sim_result)
            print("simulate:", sim_payload)
            if sim_payload is None:
                print("simulate returned no structured payload", _describe_error(sim_result))
                return

            simulation_id = sim_payload.get("simulation_id")
            if not isinstance(simulation_id, str):
                print("simulation_id missing from simulation payload")
                return

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