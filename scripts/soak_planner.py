from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Final

import anyio

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from mars_agent.mcp import server as mcp_server

_PLANNER_ASYNC_ENV: Final[str] = "MARS_MCP_PLANNER_ASYNC"
_PERSISTENCE_BACKEND_ENV: Final[str] = "MARS_MCP_PERSISTENCE_BACKEND"
_PERSISTENCE_SQLITE_PATH_ENV: Final[str] = "MARS_MCP_PERSISTENCE_SQLITE_PATH"

_BASE_EVIDENCE: Final[list[dict[str, object]]] = [
    {
        "source_id": "ops-runbook",
        "doc_id": "phase10-soak-001",
        "title": "Async planner soak baseline evidence",
        "url": "https://example.org/phase10-soak-001",
        "published_on": "2026-04-18",
        "tier": 1,
        "relevance_score": 0.95,
    }
]

_SCENARIO_GOALS: Final[tuple[dict[str, object], ...]] = (
    {
        "mission_id": "soak-early-balanced",
        "crew_size": 90,
        "horizon_years": 5.0,
        "current_phase": "early_operations",
        "solar_generation_kw": 1150.0,
        "battery_capacity_kwh": 23000.0,
        "dust_degradation_fraction": 0.18,
        "hours_without_sun": 18.0,
        "desired_confidence": 0.9,
    },
    {
        "mission_id": "soak-early-dusty",
        "crew_size": 100,
        "horizon_years": 5.0,
        "current_phase": "early_operations",
        "solar_generation_kw": 1200.0,
        "battery_capacity_kwh": 24000.0,
        "dust_degradation_fraction": 0.2,
        "hours_without_sun": 20.0,
        "desired_confidence": 0.9,
    },
    {
        "mission_id": "soak-early-low-storage",
        "crew_size": 95,
        "horizon_years": 5.0,
        "current_phase": "early_operations",
        "solar_generation_kw": 1050.0,
        "battery_capacity_kwh": 21000.0,
        "dust_degradation_fraction": 0.24,
        "hours_without_sun": 22.0,
        "desired_confidence": 0.88,
    },
    {
        "mission_id": "soak-early-peak-night",
        "crew_size": 110,
        "horizon_years": 5.0,
        "current_phase": "early_operations",
        "solar_generation_kw": 1250.0,
        "battery_capacity_kwh": 25000.0,
        "dust_degradation_fraction": 0.28,
        "hours_without_sun": 24.0,
        "desired_confidence": 0.9,
    },
)


@dataclass(slots=True)
class InvocationResult:
    latency_ms: float
    ok: bool
    degraded: bool
    error: str | None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic soak tests for mars.plan sync/async runtime modes.",
    )
    parser.add_argument("--mode", choices=("sync", "async", "both"), default="both")
    parser.add_argument("--requests", type=int, default=240)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument(
        "--persistence-backend",
        choices=("memory", "sqlite"),
        default="memory",
    )
    parser.add_argument(
        "--sqlite-path",
        default=".mars_mcp_runtime.soak.sqlite3",
        help="SQLite file used when --persistence-backend=sqlite",
    )
    parser.add_argument("--output", default="")
    parser.add_argument("--max-error-rate", type=float, default=-1.0)
    parser.add_argument("--reset-runtime-state", action="store_true")
    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> None:
    if args.requests <= 0:
        raise ValueError("--requests must be greater than zero")
    if args.concurrency <= 0:
        raise ValueError("--concurrency must be greater than zero")
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be greater than zero")
    if args.max_error_rate > 1.0:
        raise ValueError("--max-error-rate must be <= 1.0")


def _set_persistence(backend: str, sqlite_path: Path) -> None:
    os.environ[_PERSISTENCE_BACKEND_ENV] = backend
    if backend == "sqlite":
        os.environ[_PERSISTENCE_SQLITE_PATH_ENV] = str(sqlite_path)
    else:
        os.environ.pop(_PERSISTENCE_SQLITE_PATH_ENV, None)


def _set_async_mode(async_enabled: bool) -> None:
    if async_enabled:
        os.environ[_PLANNER_ASYNC_ENV] = "true"
    else:
        os.environ.pop(_PLANNER_ASYNC_ENV, None)


def _reset_runtime_state(*, sqlite_path: Path, persistence_backend: str) -> None:
    if persistence_backend == "sqlite" and sqlite_path.exists():
        sqlite_path.unlink()

    mcp_server._COMPLETED_REQUESTS.clear()
    mcp_server._IN_FLIGHT_REQUESTS.clear()
    mcp_server._PLAN_CORRELATION_BY_ID.clear()
    mcp_server._SIMULATION_CORRELATION_BY_ID.clear()
    mcp_server._TOOL_METRICS.clear()
    mcp_server._adapter._plans.clear()
    mcp_server._adapter._simulations.clear()


def _scenario_goal(index: int) -> dict[str, object]:
    base = _SCENARIO_GOALS[index % len(_SCENARIO_GOALS)]
    goal = dict(base)
    goal["mission_id"] = f"{base['mission_id']}-{index:06d}"
    return goal


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])

    ordered = sorted(values)
    rank = (percentile / 100.0) * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(ordered[lower])

    lower_weight = upper - rank
    upper_weight = rank - lower
    return float((ordered[lower] * lower_weight) + (ordered[upper] * upper_weight))


async def _invoke_plan(
    *,
    request_id: str,
    goal: dict[str, object],
    timeout_seconds: float,
) -> InvocationResult:
    started = perf_counter()
    try:
        with anyio.fail_after(timeout_seconds):
            payload = await mcp_server.mars_plan(
                goal=goal,
                evidence=_BASE_EVIDENCE,
                request_id=request_id,
            )
    except TimeoutError:
        return InvocationResult(
            latency_ms=(perf_counter() - started) * 1000.0,
            ok=False,
            degraded=False,
            error="timeout",
        )
    except Exception as exc:  # noqa: BLE001
        return InvocationResult(
            latency_ms=(perf_counter() - started) * 1000.0,
            ok=False,
            degraded=False,
            error=type(exc).__name__,
        )

    ok_value = bool(payload.get("ok", False))
    degraded = bool(payload.get("degraded", False))
    return InvocationResult(
        latency_ms=(perf_counter() - started) * 1000.0,
        ok=ok_value,
        degraded=degraded,
        error=None if ok_value else "tool_error",
    )


async def _run_mode(
    *,
    mode_label: str,
    total_requests: int,
    concurrency: int,
    timeout_seconds: float,
) -> dict[str, object]:
    semaphore = anyio.Semaphore(concurrency)
    latencies: list[float] = []
    errors: list[str] = []
    degraded_count = 0
    success_count = 0

    runtime_before = mcp_server.telemetry_plan_runtime_metrics()
    started = perf_counter()

    async def _run_single(index: int) -> None:
        nonlocal degraded_count
        nonlocal success_count

        request_id = f"soak-{mode_label}-{index:06d}"
        goal = _scenario_goal(index)

        async with semaphore:
            result = await _invoke_plan(
                request_id=request_id,
                goal=goal,
                timeout_seconds=timeout_seconds,
            )

        latencies.append(result.latency_ms)
        if result.ok:
            success_count += 1
        else:
            errors.append(result.error or "unknown_error")
        if result.degraded:
            degraded_count += 1

    async with anyio.create_task_group() as tg:
        for idx in range(total_requests):
            tg.start_soon(_run_single, idx)

    elapsed_seconds = perf_counter() - started
    runtime_after = mcp_server.telemetry_plan_runtime_metrics()

    throughput = 0.0 if elapsed_seconds == 0 else float(total_requests / elapsed_seconds)
    avg_latency = float(mean(latencies)) if latencies else 0.0
    error_rate = float(len(errors) / total_requests)
    degraded_rate = float(degraded_count / total_requests)

    return {
        "mode": mode_label,
        "requests": total_requests,
        "concurrency": concurrency,
        "elapsed_seconds": round(elapsed_seconds, 4),
        "throughput_rps": round(throughput, 4),
        "successes": success_count,
        "failures": len(errors),
        "error_rate": round(error_rate, 6),
        "degraded_count": degraded_count,
        "degraded_rate": round(degraded_rate, 6),
        "latency_ms": {
            "avg": round(avg_latency, 4),
            "p50": round(_percentile(latencies, 50.0), 4),
            "p95": round(_percentile(latencies, 95.0), 4),
            "p99": round(_percentile(latencies, 99.0), 4),
            "max": round(max(latencies) if latencies else 0.0, 4),
        },
        "runtime_counter_delta": {
            "sync_calls": float(runtime_after["sync_calls"] - runtime_before["sync_calls"]),
            "async_calls": float(runtime_after["async_calls"] - runtime_before["async_calls"]),
        },
        "error_types": {name: errors.count(name) for name in sorted(set(errors))},
    }


def _print_mode_summary(mode_result: dict[str, object]) -> None:
    latency = mode_result["latency_ms"]
    assert isinstance(latency, dict)
    print(
        (
            f"[{mode_result['mode']}] req={mode_result['requests']} conc={mode_result['concurrency']} "
            f"ok={mode_result['successes']} fail={mode_result['failures']} "
            f"err_rate={mode_result['error_rate']} degraded={mode_result['degraded_count']} "
            f"rps={mode_result['throughput_rps']} "
            f"lat(ms): p50={latency['p50']} p95={latency['p95']} p99={latency['p99']}"
        )
    )


async def _main_async(args: argparse.Namespace) -> dict[str, object]:
    sqlite_path = Path(args.sqlite_path)
    _set_persistence(args.persistence_backend, sqlite_path)

    if args.reset_runtime_state:
        _reset_runtime_state(
            sqlite_path=sqlite_path,
            persistence_backend=args.persistence_backend,
        )

    mcp_server._initialize_persistence_backend()

    modes = [args.mode] if args.mode != "both" else ["sync", "async"]
    mode_results: list[dict[str, object]] = []

    for mode in modes:
        _set_async_mode(async_enabled=(mode == "async"))
        mode_result = await _run_mode(
            mode_label=mode,
            total_requests=args.requests,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout_seconds,
        )
        mode_results.append(mode_result)
        _print_mode_summary(mode_result)

    output: dict[str, object] = {
        "schema_version": "1.0",
        "config": {
            "mode": args.mode,
            "requests": args.requests,
            "concurrency": args.concurrency,
            "timeout_seconds": args.timeout_seconds,
            "persistence_backend": args.persistence_backend,
            "sqlite_path": str(sqlite_path),
            "reset_runtime_state": bool(args.reset_runtime_state),
        },
        "results": mode_results,
        "telemetry_overview": mcp_server.telemetry_overview(),
        "plan_runtime_metrics": mcp_server.telemetry_plan_runtime_metrics(),
    }

    if args.max_error_rate >= 0.0:
        for result in mode_results:
            if float(result["error_rate"]) > args.max_error_rate:
                raise RuntimeError(
                    (
                        f"Mode {result['mode']} exceeded max error rate "
                        f"({result['error_rate']} > {args.max_error_rate})"
                    )
                )

    return output


def main() -> None:
    args = _parse_args()
    _validate_args(args)
    output = anyio.run(_main_async, args)

    json_text = json.dumps(output, indent=2, sort_keys=True)
    print(json_text)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json_text, encoding="utf-8")
        print(f"wrote soak report: {output_path}")


if __name__ == "__main__":
    main()
