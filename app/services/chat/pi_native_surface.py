"""Pi native GIS tool surface (dynamic projection over live registry schemas).

The model-facing kinds are native | execute | reject. Bare names fall in two
classes: the frozen NATIVE_TOOL_NAMES (native semantics, wrap-rejected) and
the dynamic registered surface (spawn superset, ADR-0103) whose members
dispatch straight through the ToolRegistry like ``webgis_execute`` does —
the registry stays the single execution truth, the Pi surface is only a
projection. Names outside the dump still reject with the discover-via-
``list_available_tools`` guidance.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

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
    registered_surface: Sequence[str] | None = None,
) -> ResolvedPiCall:
    """Classify a Pi-facing tool call.

    Unknown bare names reject by default: both the model surface and the HTTP
    dispatch boundary point the caller at ``list_available_tools`` followed by
    ``webgis_execute``. ADR-0103: names in ``registered_surface``（动态注册
    超集，spawn dump 与扩展注册一致）按 ``execute`` 语义直派 ToolRegistry ——
    与 webgis_execute 同一条执行管线（单一执行真相），tier/确认闸在 dispatch
    侧不变。``allow_passthrough=True`` is an explicit opt-in (currently
    test-only) kept so the classifier's total behavior stays observable.
    """
    args = dict(arguments or {})
    registered = set(registered_surface) if registered_surface is not None else None
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

    if registered is not None and name in registered:
        # ADR-0103：动态注册面上的直呼 —— 与 webgis_execute(inner) 同管线。
        # dispatch 侧仍有 registry 存在性/tier/确认闸；此处只做分类。
        return ResolvedPiCall(kind="execute", name=name, arguments=args)

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
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = native_tools_for_pi(registry)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        prefix=f".{path.name}.tmp-",
    )
    try:
        temp_file.write(content)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file.close()
        os.replace(temp_file.name, path)
    except Exception:
        try:
            os.unlink(temp_file.name)
        except OSError:
            pass
        raise
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


# ---------------------------------------------------------------------------
# Dynamic surface（ADR-0103）：spawn 注册超集 + default-active + per-turn 激活。
#
# 不变式：ToolRegistry 是唯一执行真相 —— dump 的每一份 schema 都从活注册表
# 现取；default_active 只是**投影决策**。扩展侧注册全部超集工具，
# `pi.setActiveTools` 决定每轮真正进入模型请求的子集（vendor RPC 语义：
# 「Changes take effect on the next agent turn」，零 vendor 修改）。
# ---------------------------------------------------------------------------

#: dormant 工具 schema 保持注册原样（模型可见面由 setActiveTools 治理，
#: 压缩会偏离 registry 真相 —— 不做）。dormant 标记仅供诊断。
#: per-turn 动态面开关（默认开；PI_DYNAMIC_TOOL_SURFACE=0 退回冻结 7 工具 + proxy）
_PI_DYNAMIC_TOOL_SURFACE = os.getenv("PI_DYNAMIC_TOOL_SURFACE", "1") != "0"


def registered_surface_names(registry: Any) -> list[str]:
    """spawn 超集：全部 model-visible、非 tier-3、非 external_unavailable 工具。"""
    from app.tools.descriptor import ToolStatus

    names: list[str] = []
    for name in registry.list_tools():
        try:
            desc = registry.descriptor(name)
        except KeyError:
            continue
        if not desc.model_visible:
            continue
        if desc.status is ToolStatus.EXTERNAL_UNAVAILABLE:
            continue
        if int(desc.tier) >= 3 or desc.effective_security_tier >= 3:
            continue
        names.append(name)
    return sorted(names)


def _pi_tool_definition(registry: Any, name: str, *, dormant: bool) -> dict[str, Any] | None:
    subset = registry.get_schemas_subset({name})
    if not subset:
        return None
    fn = subset[0].get("function", {})
    definition: dict[str, Any] = {
        "name": name,
        "label": _LABELS.get(name, name),
        "description": fn.get("description") or name,
        "parameters": _pi_parameters(subset[0]),
    }
    if name in NATIVE_TOOL_NAME_SET:
        definition["promptSnippet"] = _SNIPPETS.get(name, "")
    if dormant:
        definition["dormant"] = True
    return definition
    # 注：dormant 不压缩 —— 压缩后的 schema 会在激活时以非注册形态呈现给
    # 模型，违反「schema 从 registry 现取」的单一真相纪律。


def pi_surface_for_spawn(registry: Any) -> dict[str, Any]:
    """spawn dump v2：注册超集 + default active（渐进式动态机制 Phase 3）。

    - ``tools``: 注册超集（native 7 带 promptSnippet；其余为完整注册 schema
      + ``dormant`` 标记）；
    - ``default_active``: 冻结 native 面（兼容 Phase 1 行为）。
    扩展注册全部 tools，随后 setActiveTools(default_active)。
    """
    native = native_tools_for_pi(registry)  # fail-fast：native 缺失即 abort spawn
    native_names = {t["name"] for t in native}
    tools: list[dict[str, Any]] = list(native)
    for name in registered_surface_names(registry):
        if name in native_names:
            continue
        definition = _pi_tool_definition(registry, name, dormant=True)
        if definition is not None:
            tools.append(definition)
    return {
        "version": 2,
        "tools": tools,
        "default_active": list(NATIVE_TOOL_NAMES),
        "execute_proxy": EXECUTE_PROXY_NAME,
    }


def write_surface_file(registry: Any, path: Path) -> Path:
    """spawn dump（v2 形状，原子写）—— write_native_tools_file 的超集替代。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = pi_surface_for_spawn(registry)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        prefix=f".{path.name}.tmp-",
    )
    try:
        temp_file.write(content)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file.close()
        os.replace(temp_file.name, path)
    except Exception:
        try:
            os.unlink(temp_file.name)
        except OSError:
            pass
        raise
    return path


def dump_surface_file(path: Path) -> Path:
    """Spawn-time dump of the dynamic surface from the live registry. Fail-fast."""
    from app.agent_pi_bridge import get_tool_registry
    return write_surface_file(get_tool_registry(), path)


def compute_turn_active_tools(
    message: str,
    *,
    active_capabilities: Sequence[str] = (),
    workflow_stage: str = "",
    k_max: int = 30,
    role: str = "execution",
) -> list[str]:
    """per-turn 动态工具面（Phase 3）：SelectionContext → 激活名单。

    ``role`` 透传给 V3 选择器的角色副作用策略（ROLE_SIDE_EFFECT_POLICY）；
    Pi 主循环固定 execution（不受限），受限子代理/评测方传入对应角色名。

    任何失败 → 返回空列表（调用方不注入 marker，扩展保持上轮/default 面）。
    名单恒含 NATIVE_TOOL_NAMES（前门不可失），绝不包含 tier-3。
    """
    if not _PI_DYNAMIC_TOOL_SURFACE:
        return []
    try:
        from app.services.chat.tool_surface_v3 import DynamicToolSurface, ToolSelectionContext

        from app.agent_pi_bridge import get_tool_registry

        registry = get_tool_registry()

        ctx = ToolSelectionContext(
            user_message=message or "",
            active_capabilities=tuple(active_capabilities),
            workflow_stage=workflow_stage or "",
            k_max=max(k_max, len(NATIVE_TOOL_NAMES)),
            role=role,
        )
        selection = DynamicToolSurface(registry).select(ctx)
        names = list(dict.fromkeys([*NATIVE_TOOL_NAMES, *selection.names]))
        safe: list[str] = []
        for name in names:
            try:
                desc = registry.descriptor(name)
            except KeyError:
                continue
            # 末道安全双检（review m1）：tier 与生效安全层都查
            if int(desc.tier) >= 3 or desc.effective_security_tier >= 3:
                continue
            safe.append(name)
        return safe
    except Exception:  # noqa: BLE001 — 动态面是增强，绝不阻断 turn
        return []
