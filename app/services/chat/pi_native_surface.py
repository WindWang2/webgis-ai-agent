"""Pi native GIS tool surface (wrap-only, live registry schemas).

The model-facing kinds are native | execute | reject. Unknown bare names
reject at both the model surface and the HTTP dispatch boundary — the long
tail is reachable only through the execute proxy. Native parameter schemas
are generated from ToolRegistry — never a handwritten second catalog.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

NATIVE_TOOL_NAMES: tuple[str, ...] = (
    "webgis_map_intent",
    "webgis_map_product",
    "webgis_component_update",
    "webgis_cartography_status",
    "query_local_poi",
    "get_local_admin_boundary",
    "list_available_tools",
)
NATIVE_TOOL_NAME_SET = frozenset(NATIVE_TOOL_NAMES)
EXECUTE_PROXY_NAME = "webgis_execute"
STATUS_TOOL = "webgis_cartography_status"

_PASSTHROUGH_KEYS = frozenset({"session_id"})

_LABELS = {
    "webgis_map_intent": "Map Intent",
    "webgis_map_product": "Map Product",
    "webgis_component_update": "Component Update",
    "webgis_cartography_status": "Cartography Status",
    "query_local_poi": "Local POI",
    "get_local_admin_boundary": "Admin Boundary",
    "list_available_tools": "List Tools",
}

_SNIPPETS = {
    "webgis_map_intent": "First GIS step for 分布/密度. Pass {query: the user text}.",
    "webgis_map_product": "Assemble the map after data tools return. Same SessionPlan envelope.",
    "webgis_component_update": "Restyle one map component. Does not start a new city analysis.",
    "webgis_cartography_status": "Zero-argument verdict pull AFTER the map changed. Call with {}.",
    "query_local_poi": "China POI. district + subtype, e.g. 成都市 + 小学.",
    "get_local_admin_boundary": "China admin boundary. name e.g. 成都市.",
    "list_available_tools": "Discover long-tail GIS names by domain, then call them via webgis_execute.",
}

ResolvedKind = Literal["native", "execute", "reject", "passthrough"]


@dataclass(frozen=True)
class ResolvedPiCall:
    kind: ResolvedKind
    name: str
    arguments: dict[str, Any]
    error: str = ""


def _analysis_extras(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Extras beyond the passthrough set. Key-sensitive: a hallucinated
    analysis argument stays hallucinated when its value is null or empty —
    extra keys fail closed regardless of value."""
    return {k: v for k, v in arguments.items() if k not in _PASSTHROUGH_KEYS}


def resolve_pi_tool_call(
    name: str,
    arguments: Mapping[str, Any] | None,
    *,
    allow_passthrough: bool = False,
) -> ResolvedPiCall:
    """Classify a Pi-facing tool call.

    Unknown bare names reject by default: both the model surface and the HTTP
    dispatch boundary point the caller at ``list_available_tools`` followed by
    ``webgis_execute``. ``allow_passthrough=True`` is an explicit opt-in
    (currently test-only — no production caller resolves names ahead of the
    registry existence check) kept so the classifier's total behavior stays
    observable.
    """
    args = dict(arguments or {})
    if name == EXECUTE_PROXY_NAME:
        inner = args.get("toolName") or args.get("name")
        inner_args = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
        if not isinstance(inner_args, dict):
            inner_args = {}
        if not inner or not isinstance(inner, str):
            return ResolvedPiCall(
                kind="reject",
                name=name,
                arguments=args,
                error="webgis_execute requires toolName",
            )
        if inner in NATIVE_TOOL_NAME_SET:
            return ResolvedPiCall(
                kind="reject",
                name=name,
                arguments=args,
                error=(
                    f"do not wrap native tool {inner} inside webgis_execute; "
                    "call it directly"
                ),
            )
        return ResolvedPiCall(kind="execute", name=inner, arguments=dict(inner_args))

    if name == STATUS_TOOL:
        extras = _analysis_extras(args)
        if extras:
            keys = ", ".join(sorted(extras))
            return ResolvedPiCall(
                kind="reject",
                name=name,
                arguments=args,
                error=(
                    f"{STATUS_TOOL} does not accept analysis arguments: {keys}. "
                    "Call it with {{}} after the map changes; use webgis_map_intent "
                    "for distribution queries."
                ),
            )
        return ResolvedPiCall(kind="native", name=name, arguments=args)

    if name in NATIVE_TOOL_NAME_SET:
        return ResolvedPiCall(kind="native", name=name, arguments=args)

    if allow_passthrough:
        return ResolvedPiCall(kind="passthrough", name=name, arguments=args)
    return ResolvedPiCall(
        kind="reject",
        name=name,
        arguments=args,
        error=(
            f"unknown tool {name}; discover via list_available_tools, "
            "then call webgis_execute"
        ),
    )


def _pi_parameters(function_schema: dict[str, Any]) -> dict[str, Any]:
    params = function_schema.get("parameters") or {"type": "object", "properties": {}}
    properties = dict(params.get("properties") or {})
    properties.pop("session_id", None)
    required = [key for key in (params.get("required") or []) if key != "session_id"]
    out: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    if "$defs" in params:
        out["$defs"] = params["$defs"]
    if "definitions" in params:
        out["definitions"] = params["definitions"]
    return out


def native_tools_for_pi(registry: Any) -> list[dict[str, Any]]:
    """Live-registry schemas in Pi ``registerTool`` shape.

    A native name missing from the registry raises rather than degrading to
    an empty schema — the dump is the only source of Pi's native surface and
    must never silently drift from the registry
    (specs/pi-as-agent-host.md, user story 35)."""
    schemas = {
        item.get("function", {}).get("name"): item.get("function", {})
        for item in registry.get_schemas_subset(set(NATIVE_TOOL_NAMES))
    }
    missing = [name for name in NATIVE_TOOL_NAMES if not schemas.get(name)]
    if missing:
        raise ValueError(
            f"native tool(s) missing from live registry: {', '.join(missing)}"
        )
    dumped: list[dict[str, Any]] = []
    for name in NATIVE_TOOL_NAMES:
        fn = schemas[name]
        dumped.append(
            {
                "name": name,
                "label": _LABELS.get(name, name),
                "description": fn.get("description") or name,
                "parameters": _pi_parameters(fn),
                "promptSnippet": _SNIPPETS.get(name, ""),
            }
        )
    return dumped


def write_native_tools_file(registry: Any, path: Path) -> Path:
    """Dump native schemas atomically (tmp + ``os.replace``).

    The extension's reader treats an unparseable dump as a loud-but-degraded
    empty surface; a torn write from a crash mid-``write_text`` would reopen
    that hole on every spawn until the next successful dump. The rename is
    atomic, so readers see either the previous complete dump or the new one —
    never a partial file. The tmp name carries the pid so concurrent workers
    spawning Pi never rename each other's in-flight tmp away.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = native_tools_for_pi(registry)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def dump_native_tools(path: Path) -> Path:
    """Spawn-time dump of native schemas from the live registry. Fail-fast.

    A Pi spawned without its native surface silently loses ``webgis_map_intent``
    (execute-wrapping natives is rejected), so a missing registry or an
    unwritable dump must abort the spawn — the API then falls back to
    ChatEngine instead of running a crippled GeoAgent.
    """
    from app.agent_pi_bridge import get_tool_registry
    return write_native_tools_file(get_tool_registry(), path)
