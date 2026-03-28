"""Phase 8 MCP server runtime wrapping MarsMCPAdapter via FastMCP."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import cast
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from mars_agent.mcp.adapter import MarsMCPAdapter, MCPValue

_adapter = MarsMCPAdapter()
mcp = FastMCP("mars-colonization-specialist")
_TOOL_ERROR_SCHEMA_VERSION = "1.0"
_AUTH_ENABLED_ENV = "MARS_MCP_AUTH_ENABLED"
_AUTH_TOKEN_ENV = "MARS_MCP_AUTH_TOKEN"
_AUTH_PERMISSIONS_ENV = "MARS_MCP_AUTH_PERMISSIONS"
_ALL_TOOL_PERMISSIONS = {"plan", "simulate", "governance", "benchmark"}
_TOOL_PERMISSION_BY_NAME = {
    "mars.plan": "plan",
    "mars.simulate": "simulate",
    "mars.governance": "governance",
    "mars.benchmark": "benchmark",
}


def _as_object_dict(payload: dict[str, MCPValue]) -> dict[str, object]:
    return {key: cast(object, value) for key, value in payload.items()}


def _resolve_request_id(request_id: str | None) -> str:
    if request_id is not None and request_id.strip():
        return request_id.strip()
    return f"req-{uuid4().hex}"


def _tool_error_message(request_id: str, tool_name: str, code: str, message: str) -> str:
    return json.dumps(
        {
            "schema_version": _TOOL_ERROR_SCHEMA_VERSION,
            "tool": tool_name,
            "code": code,
            "request_id": request_id,
            "message": message,
        }
    )


def _parse_bool_env(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _auth_enabled() -> bool:
    return _parse_bool_env(os.getenv(_AUTH_ENABLED_ENV), default=False)


def _configured_auth_token() -> str | None:
    value = os.getenv(_AUTH_TOKEN_ENV)
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _configured_permissions() -> set[str]:
    value = os.getenv(_AUTH_PERMISSIONS_ENV)
    if value is None:
        return set(_ALL_TOOL_PERMISSIONS)
    allowed = {item.strip() for item in value.split(",") if item.strip()}
    return allowed if allowed else set()


def _request_meta_dict() -> dict[str, object]:
    try:
        context = mcp.get_context()
        meta = context.request_context.meta
    except Exception:  # noqa: BLE001
        return {}

    if meta is None:
        return {}

    return cast(dict[str, object], meta.model_dump(exclude_none=True))


def _extract_token_from_meta() -> str | None:
    meta = _request_meta_dict()
    raw = meta.get("authorization") or meta.get("auth_token") or meta.get("token")

    if not isinstance(raw, str):
        return None

    value = raw.strip()
    if not value:
        return None

    if value.lower().startswith("bearer "):
        bearer = value[7:].strip()
        return bearer if bearer else None

    return value


def _resolve_auth_token(auth_token: str | None) -> str | None:
    if auth_token is not None and auth_token.strip():
        return auth_token.strip()
    return _extract_token_from_meta()


def _raise_tool_error(request_id: str, tool_name: str, code: str, message: str) -> None:
    raise ToolError(
        _tool_error_message(
            request_id=request_id,
            tool_name=tool_name,
            code=code,
            message=message,
        )
    )


def _enforce_auth(tool_name: str, request_id: str, auth_token: str | None) -> None:
    if not _auth_enabled():
        return

    expected_token = _configured_auth_token()
    if expected_token is None:
        _raise_tool_error(
            request_id=request_id,
            tool_name=tool_name,
            code="unauthenticated",
            message=(
                f"Server auth misconfigured: {_AUTH_TOKEN_ENV} is required "
                "when auth is enabled."
            ),
        )

    presented_token = _resolve_auth_token(auth_token)
    if presented_token is None or presented_token != expected_token:
        _raise_tool_error(
            request_id=request_id,
            tool_name=tool_name,
            code="unauthenticated",
            message="Missing or invalid bearer token.",
        )

    required_permission = _TOOL_PERMISSION_BY_NAME.get(tool_name)
    allowed_permissions = _configured_permissions()
    if required_permission is not None and required_permission not in allowed_permissions:
        _raise_tool_error(
            request_id=request_id,
            tool_name=tool_name,
            code="forbidden",
            message=(
                f"Token is authenticated but lacks required permission '{required_permission}' "
                f"for tool '{tool_name}'."
            ),
        )


def _invoke_with_envelope(
    tool_name: str,
    arguments: Mapping[str, object],
    request_id: str | None,
    auth_token: str | None,
) -> dict[str, object]:
    resolved_request_id = _resolve_request_id(request_id)
    _enforce_auth(tool_name=tool_name, request_id=resolved_request_id, auth_token=auth_token)

    try:
        payload = _adapter.invoke(tool_name, arguments)
        response = _as_object_dict(payload)
        response["request_id"] = resolved_request_id
        response["ok"] = True
        return response
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(
            _tool_error_message(
                request_id=resolved_request_id,
                tool_name=tool_name,
                code="invalid_request",
                message=str(exc),
            )
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ToolError(
            _tool_error_message(
                request_id=resolved_request_id,
                tool_name=tool_name,
                code="internal_error",
                message=str(exc),
            )
        ) from exc


def mars_plan(
    goal: dict[str, object],
    evidence: list[dict[str, object]],
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """Generate a mission plan from goal and evidence inputs."""

    return _invoke_with_envelope(
        tool_name="mars.plan",
        arguments={"goal": goal, "evidence": evidence},
        request_id=request_id,
        auth_token=auth_token,
    )


def mars_simulate(
    plan_id: str,
    seed: int = 42,
    max_repair_attempts: int = 3,
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """Run deterministic simulation pipeline for a previously generated plan."""

    return _invoke_with_envelope(
        tool_name="mars.simulate",
        arguments={
            "plan_id": plan_id,
            "seed": seed,
            "max_repair_attempts": max_repair_attempts,
        },
        request_id=request_id,
        auth_token=auth_token,
    )


def mars_governance(
    plan_id: str,
    simulation_id: str,
    min_confidence: float = 0.85,
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """Evaluate governance gate using plan and simulation outputs."""

    return _invoke_with_envelope(
        tool_name="mars.governance",
        arguments={
            "plan_id": plan_id,
            "simulation_id": simulation_id,
            "min_confidence": min_confidence,
        },
        request_id=request_id,
        auth_token=auth_token,
    )


def mars_benchmark(
    plan_id: str,
    simulation_id: str,
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """Evaluate benchmark deltas using plan and simulation outputs."""

    return _invoke_with_envelope(
        tool_name="mars.benchmark",
        arguments={
            "plan_id": plan_id,
            "simulation_id": simulation_id,
        },
        request_id=request_id,
        auth_token=auth_token,
    )


mcp.tool(name="mars.plan")(mars_plan)
mcp.tool(name="mars.simulate")(mars_simulate)
mcp.tool(name="mars.governance")(mars_governance)
mcp.tool(name="mars.benchmark")(mars_benchmark)
