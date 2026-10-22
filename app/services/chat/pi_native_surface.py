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
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

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

#: per-turn 激活面 schema 字节预算（ADR-0253 D2）。口径 = registry.schema_size
#: （#1062 缓存，``len(json.dumps(schema, ensure_ascii=False))`` 字符数），
#: 与 V2 ToolCatalog 的 24KB 预算同量纲。默认 32KB：native7 前门实测
#: ≈17.3K、中位 schema ≈528 —— 32KB 保留 ADR-0103 典型 30 工具 turn 不变，
#: 只裁大 schema 病理尾；``PI_SURFACE_BYTE_BUDGET=0`` 关闭（回归旧行为）。
_DEFAULT_SURFACE_BYTE_BUDGET = 32 * 1024
#: webgis_execute 扩展侧 schema 的近似字符数（proxy 不在 registry，无法
#: schema_size；index.mjs 的 Type.Object 定义实测 ≈0.7K —— 常量入账，
#: 让预算总量接近模型真实可见面）。
_PROXY_SCHEMA_BYTES = 720


def _surface_byte_budget() -> int:
    try:
        raw = os.getenv("PI_SURFACE_BYTE_BUDGET")
        if raw is None or raw == "":
            return _DEFAULT_SURFACE_BYTE_BUDGET
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_SURFACE_BYTE_BUDGET


def apply_surface_byte_budget(
    registry: Any,
    names: Sequence[str],
    *,
    disclosure: Optional[Dict[str, Any]] = None,
) -> tuple[list[str], Dict[str, Any]]:
    """激活名单 schema 字节预算（ADR-0253 D2）：只影响「本轮激活谁」。

    - native 前门（NATIVE_TOOL_NAMES）+ proxy 常量先入账、**恒保留**——
      前门不可失（fail-open 到 native 面，绝不因预算吞掉 map_intent/status）；
    - 动态候选按传入序（V3 选择器已分数降序、tie 按名）贪心装入，
      超预算丢弃并记因 ``byte_budget``；
    - ``schema_size`` 缺席（未注册/缓存未建）按 0 入账（防御性：预算
      绝不因度量缺席而拒绝工具）。

    返回 ``(kept, info)``；``info = {budget, bytes_used, dropped}`` 供
    披露面与 metrics 消费。预算关闭（0）时原样返回、info.budget=0。
    """
    budget = _surface_byte_budget()
    info: Dict[str, Any] = {"budget": budget, "bytes_used": 0, "dropped": []}
    if budget <= 0:
        return list(names), info

    name_set = set(NATIVE_TOOL_NAME_SET)
    front = [n for n in names if n in name_set]
    dynamic = [n for n in names if n not in name_set]

    used = _PROXY_SCHEMA_BYTES
    for n in front:
        used += registry.schema_size(n) or 0
    kept: list[str] = list(front)
    dropped: list[str] = []
    for n in dynamic:
        size = registry.schema_size(n) or 0
        if used + size > budget:
            dropped.append(n)
            continue
        used += size
        kept.append(n)
    info["bytes_used"] = used
    info["dropped"] = dropped
    if dropped and disclosure is not None:
        disclosure["surface_budget"] = budget
        disclosure["surface_bytes"] = used
        disclosure["budget_dropped"] = dropped
    return kept, info


def registered_surface_names(registry: Any) -> list[str]:
    """spawn 超集：全部 model-visible、非 tier-3、非 external_unavailable 工具。

    ADR-0224（Goal 04）：``PI_TOOL_ABI`` 开启时追加两条**逐出**规则（dump 与
    dispatch 分类共用本函数 —— 分类集 ≡ 扩展注册现实的不变量保持）：
    - DESTRUCTIVE 副作用类（低 tier 同样）不进 native 注册面 —— 红线
      「side-effect tool 不得经 native path 绕过确认」；proxy 长尾通道
      不变，dispatch tier 闸仍守；
    - per-tool schema 超 ``PI_SPEC_MAX_SCHEMA_BYTES``（registry.schema_size
      口径，#1062 缓存）逐出 —— fail-honest：放不下逐工具，绝不裁 schema。
    """
    from app.tools.descriptor import SideEffectClass, ToolStatus

    from app.services.chat.pi_tool_spec import (
        spec_max_schema_bytes,
        tool_abi_enabled,
    )

    abi_on = tool_abi_enabled()
    per_tool_budget = spec_max_schema_bytes() if abi_on else 0
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
        if abi_on and desc.side_effect is SideEffectClass.DESTRUCTIVE:
            continue
        if abi_on and per_tool_budget:
            if (registry.schema_size(name) or 0) > per_tool_budget:
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
    """spawn dump：注册超集 + default active（渐进式动态机制 Phase 3）。

    - v2（``PI_TOOL_ABI=0``）：``{version:2, tools, default_active, execute_proxy}``
      —— master 行为原样（兼容回退 / kill switch）；
    - v3（默认，ADR-0224）：v2 形状**加性**扩展（v2 解析方继续工作）——
      ``abi``（版本 + 策略）、``registry_generation``（runtime manifest 指纹，
      来源锚）、``surface_fingerprint``（dump 超集的 spec 级指纹）、
      per-tool ``spec``（PiToolSpecV1 序列化：tier/side-effect/资源分级/
      凭证类/指纹）。扩展注册仍只用 name/label/description/parameters；
      spec 是 fingerprint echo 与诊断面的契约，不是第二注册中心。
    - ``tools``: 注册超集（native 7 带 promptSnippet；其余为完整注册 schema
      + ``dormant`` 标记）；
    - ``default_active``: 冻结 native 面（兼容 Phase 1 行为）。
    扩展注册全部 tools，随后 setActiveTools(default_active)。
    """
    from app.services.chat.pi_tool_spec import (
        PI_TOOL_SPEC_ABI_VERSION,
        PI_TOOL_SPEC_POLICY,
        compile_tool_spec,
        current_registry_generation,
        surface_fingerprint,
        tool_abi_enabled,
    )

    native = native_tools_for_pi(registry)  # fail-fast：native 缺失即 abort spawn
    native_names = {t["name"] for t in native}
    superset = registered_surface_names(registry)
    abi_on = tool_abi_enabled()
    generation = current_registry_generation() if abi_on else ""
    specs_by_name: dict[str, Any] = {}
    if abi_on:
        for name in [*NATIVE_TOOL_NAMES, *(n for n in superset if n not in native_names)]:
            try:
                spec = compile_tool_spec(registry, name, registry_generation=generation)
            except Exception:  # noqa: BLE001 — spec 是投影；单工具失败不 abort spawn
                logger.warning("[PiNativeSurface] spec compile failed for %s", name, exc_info=True)
                spec = None
            if spec is not None:
                specs_by_name[name] = spec
    tools: list[dict[str, Any]] = list(native)
    for name in superset:
        if name in native_names:
            continue
        definition = _pi_tool_definition(registry, name, dormant=True)
        if definition is not None:
            tools.append(definition)
    payload: dict[str, Any] = {
        "version": 2,
        "tools": tools,
        "default_active": list(NATIVE_TOOL_NAMES),
        "execute_proxy": EXECUTE_PROXY_NAME,
    }
    if not abi_on:
        return payload
    # dump v3：加性字段（v2 keys 保留 —— 扩展 v2 解析继续工作）。
    for tool in payload["tools"]:
        spec = specs_by_name.get(tool.get("name", ""))
        if spec is not None:
            tool["spec"] = spec.to_dump_dict()
    payload["version"] = 3
    payload["abi"] = {
        "version": PI_TOOL_SPEC_ABI_VERSION,
        "policy": PI_TOOL_SPEC_POLICY,
    }
    payload["registry_generation"] = generation
    payload["surface_fingerprint"] = surface_fingerprint(
        [specs_by_name[t["name"]] for t in payload["tools"] if t["name"] in specs_by_name],
        registry_generation=generation,
    )
    return payload


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


def _session_artifact_types_snapshot() -> list[str]:
    """当前会话的 artifact 语义类型快照（V4 rerank 证据；有界 ≤12）。

    来源：RuntimeContext.session_id → ArtifactRegistry 记录的
    ``artifact_type``。任何缺席/失败 → 空列表（= 零贡献 = V3 行为）。
    """
    session_id = ""
    try:
        from app.lib.runtime.context import current_runtime_context

        ctx = current_runtime_context()
        session_id = str(getattr(ctx, "session_id", "") or "") if ctx else ""
    except Exception:  # noqa: BLE001
        return []
    if not session_id:
        return []
    try:
        from app.services.artifact_registry import list_artifacts

        types: list[str] = []
        for rec in list_artifacts(session_id):
            t = str(getattr(rec, "artifact_type", "") or "")
            if t and t not in types:
                types.append(t)
            if len(types) >= 12:
                break
        return types
    except Exception:  # noqa: BLE001 — 证据缺席 = 空贡献
        return []


def _continuation_tools_snapshot(active_capabilities: Sequence[str]) -> list[str]:
    """续跑工具快照：当前未完成 capability 的候选工具（有界 ≤8）。

    来源：AlgorithmRegistry.capability_tool_map（单一事实源）。这些工具
    在 rerank 中获得小幅 continuation 加成 —— 上一 turn 正在做的事优先
    浮出，但不改变硬门/契约过滤。
    """
    if not active_capabilities:
        return []
    try:
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        cap_map = get_algorithm_registry().capability_tool_map()
        tools: list[str] = []
        for cap in active_capabilities:
            for t in cap_map.get(cap, ())[:4]:
                if t not in tools:
                    tools.append(t)
                if len(tools) >= 8:
                    return tools
        return tools
    except Exception:  # noqa: BLE001 — 证据缺席 = 空贡献
        return []


def compute_turn_active_tools(
    message: str,
    *,
    active_capabilities: Sequence[str] = (),
    workflow_stage: str = "",
    k_max: int = 30,
    role: str = "execution",
    disclosure: Optional[Dict[str, Any]] = None,
    situation: Optional[Mapping[str, Any]] = None,
    session_id: str = "",
    turn_id: str = "",
) -> list[str]:
    """per-turn 动態工具面（Phase 3）：SelectionContext → 激活名单。

    ``role`` 透传给 V3 选择器的角色副作用策略（ROLE_SIDE_EFFECT_POLICY）；
    Pi 主循环固定 execution（不受限），受限子代理/评测方传入对应角色名。

    ADR-0224（Goal 04）：``PI_TOOL_ABI`` 开启时，安全双检之后叠加
    **situation 资格投影**（credentials/offline/durable-worker 阳性缺席
    事实 → 逐出 + reason code），并按 (session_id, turn_id) 铸
    SurfaceReceipt（candidates/selected/dropped/bytes/fingerprint ——
    回调 fingerprint echo 的对照真相）。dispatch 期闸仍是唯一执行权威。

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
        # V4 Wave 4 生产装配（ADR-0104 决策 5）：会话 artifact 语义类型 +
        # 近期工具结果 + 续跑工具三路上下文证据（缺席 = 空贡献 = V3 行为
        # 不变；所有来源只读、有界、任何失败静默降级）。
        try:
            ctx.session_artifact_types = tuple(
                _session_artifact_types_snapshot()
            )  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — 证据缺席 = 空贡献
            pass
        try:
            from app.services.chat.tool_surface_v3 import recent_failure_hints

            ctx.recent_tool_outcomes = recent_failure_hints()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — 证据缺席 = 空贡献
            pass
        try:
            ctx.continuation_tools = tuple(
                _continuation_tools_snapshot(active_capabilities)
            )  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — 证据缺席 = 空贡献
            pass
        selection = DynamicToolSurface(registry).select(ctx)
        # V6（ADR-0119 D2）低置信度弃权：检索无法确证动态面与 query 的
        # 匹配时**不注入**低置信度工具面（拒绝派发语义）—— native 前门
        # （含 list_available_tools 两跳通道）保持，不静默乱选；弃权与
        # 置信度入链披露，消费方可据此触发 deepen_profile / 语义编译。
        if getattr(selection, "abstained", False):
            try:
                logger.warning(
                    "[PiNativeSurface] tool retrieval abstained (conf=%.2f, %s)"
                    " — dynamic surface withheld, native front door kept",
                    float(selection.confidence),
                    str(selection.abstain_reason)[:80],
                )
            except Exception:  # noqa: BLE001 — 披露面绝不阻断
                pass
            try:
                from app.lib.runtime.chain_emitters import emit_chain_once
                from app.lib.runtime.gis_trace import Stage

                emit_chain_once(
                    Stage.TOOL_SURFACE,
                    abstained=True,
                    confidence=float(selection.confidence),
                    abstain_reason=str(selection.abstain_reason)[:96],
                    dynamic_count=0,
                    workflow_stage=str(workflow_stage or "")[:48],
                )
            except Exception:  # noqa: BLE001 — 记录面绝不阻断 turn
                pass
            if disclosure is not None:
                disclosure.update({
                    "abstained": True,
                    "confidence": float(selection.confidence),
                    "reason": str(selection.abstain_reason)[:96],
                })
            _record_abstained_receipt(registry, session_id, turn_id)
            return list(NATIVE_TOOL_NAMES)
        if disclosure is not None:
            disclosure.update({
                "abstained": False,
                "confidence": float(getattr(selection, "confidence", 1.0) or 1.0),
            })
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
        # ADR-0224：situation 资格投影（PI_TOOL_ABI=0 时整体跳过 —— master
        # 行为逐位一致）。receipt 在字节预算**之后**铸造（见下）—— 捕获最终
        # 激活面与全部逐出原因（资格 + 预算）。
        qualification: Optional[Dict[str, Any]] = None
        try:
            qualification = _apply_surface_qualification(
                registry,
                safe,
                situation=situation,
            )
        except Exception:  # noqa: BLE001 — 资格投影是增强，失败不裁名单
            logger.debug("[PiNativeSurface] qualification failed", exc_info=True)
            qualification = None
        if qualification is not None:
            safe = list(qualification["selected"])
            if disclosure is not None and qualification["dropped"]:
                disclosure["qualification_dropped"] = qualification["dropped"][:12]
        # ADR-0253 D2：激活面 schema 字节预算 —— 只裁动态候选，native 前门
        # 恒保留；裁剪记因（disclosure + 链发射 + metrics），绝不阻断 turn。
        try:
            safe, budget_info = apply_surface_byte_budget(
                registry, safe, disclosure=disclosure
            )
        except Exception:  # noqa: BLE001 — 预算是增强，度量失败不裁名单
            budget_info = None
        # ADR-0224：铸造 SurfaceReceipt（最终激活面 + 资格/预算全部逐出因；
        # 同 turn 幂等 —— 首次铸造后本 turn 内 surface 不可变）。
        _turn_receipt = None
        if qualification is not None and session_id and turn_id:
            try:
                from app.services.chat.pi_surface_receipt import (
                    SurfaceDrop,
                    record_turn_surface,
                )

                budget_drops = [
                    SurfaceDrop(n, "byte_budget")
                    for n in ((budget_info or {}).get("dropped") or [])
                ]
                _turn_receipt = record_turn_surface(
                    [qualification["specs_by_name"][n] for n in safe
                     if n in qualification["specs_by_name"]],
                    session_id=session_id,
                    turn_id=turn_id,
                    candidates=names,
                    selected=safe,
                    drops=[*(qualification["drops"]), *budget_drops],
                    byte_budget=int((budget_info or {}).get("budget") or 0),
                )
                if disclosure is not None:
                    disclosure["surface_fingerprint"] = _turn_receipt.surface_fingerprint
            except Exception:  # noqa: BLE001 — receipt 是披露/校验面，绝不阻断 turn
                logger.debug(
                    "[PiNativeSurface] surface receipt mint failed", exc_info=True
                )
        # V4 Wave 8：证据链阶段 7（TOOL_SURFACE）——per-turn 动态面裁决入链
        # （emit-once：每 turn 一条；上下文缺席静默跳过）。ADR-0253 追加
        # surface_bytes/budget 披露字段（additive，观测面）。
        try:
            from app.lib.runtime.chain_emitters import emit_chain_once
            from app.lib.runtime.gis_trace import Stage

            _emit_kwargs: Dict[str, Any] = {
                "dynamic_count": len(selection.names),
                "confidence": float(getattr(selection, "confidence", 1.0) or 1.0),
                "workflow_stage": str(workflow_stage or "")[:48],
            }
            if budget_info is not None and budget_info.get("budget"):
                _emit_kwargs["surface_bytes"] = int(budget_info["bytes_used"])
                _emit_kwargs["surface_budget"] = int(budget_info["budget"])
                _emit_kwargs["budget_dropped"] = len(budget_info["dropped"])
            if _turn_receipt is not None:
                _emit_kwargs["surface_fingerprint"] = _turn_receipt.surface_fingerprint
            emit_chain_once(Stage.TOOL_SURFACE, **_emit_kwargs)
        except Exception:  # noqa: BLE001 — 记录面绝不阻断 turn
            pass
        # ADR-0253 D5：面投影快照进 pi_surface_metrics（last-wins，观测面）。
        try:
            from app.services.chat.pi_surface_metrics import record_surface_projection

            _bi = budget_info or {}
            record_surface_projection(
                surface_bytes=int(_bi.get("bytes_used") or 0),
                budget=int(_bi.get("budget") or 0),
                dropped=len(_bi.get("dropped") or ()),
                dynamic_count=len(selection.names),
            )
        except Exception:  # noqa: BLE001 — 指标绝不阻断 turn
            pass
        return safe
    except Exception:  # noqa: BLE001 — 动态面是增强，绝不阻断 turn
        return []


def _apply_surface_qualification(
    registry: Any,
    names: Sequence[str],
    *,
    situation: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """situation 资格投影（ADR-0224；PI_TOOL_ABI=0 → None = master 行为）。

    输入是 tier 双检后的候选序（native 前门 + 选择器序）。逐出只依据
    **阳性缺席事实**（credentials_present[id]=False / offline=True /
    durable_worker=False）—— 无来源 = unknown = 不排除；dispatch 期
    capability bind 仍是唯一执行权威，本层只是 turn 侧投影。
    返回 ``{selected, drops, dropped, specs_by_name}``；receipt 由调用方
    在字节预算后铸造（捕获最终激活面）。
    """
    from app.services.chat.pi_tool_spec import (
        compile_tool_spec,
        current_registry_generation,
        tool_abi_enabled,
    )
    from app.services.chat.pi_surface_receipt import qualify_surface

    if not tool_abi_enabled():
        return None
    generation = current_registry_generation()
    specs_by_name: Dict[str, Any] = {}
    ordered_specs = []
    compile_failed: list[str] = []
    for name in names:
        try:
            spec = compile_tool_spec(registry, name, registry_generation=generation)
        except Exception:  # noqa: BLE001 — 单工具编译失败不裁候选（review P2-1）
            logger.debug("[PiNativeSurface] spec compile skipped for %s", name, exc_info=True)
            spec = None
        if spec is None:
            # 瞬态编译失败 / schema 缺席：**不逐出**（资格层失败不缩面 ——
            # dispatch 闸仍是权威；native 前门绝不因此消失）。该工具无指纹
            # → receipt.tool_fingerprints 缺席 → echo 校验对其跳过。
            compile_failed.append(name)
            continue
        specs_by_name[name] = spec
        ordered_specs.append(spec)
    selected, drops = qualify_surface(ordered_specs, situation=situation)
    if compile_failed:
        logger.warning(
            "[PiNativeSurface] spec compile failed for %d candidate(s) "
            "(kept unqualified, dispatch gates unchanged): %s",
            len(compile_failed), ", ".join(compile_failed[:8]),
        )
    # 未编译成功的候选保持原序保留（master 行为）；已编译候选按资格裁决。
    selected_with_unqualified = [
        name for name in names
        if name in selected or name in compile_failed
    ]
    dropped = [d.to_dict() for d in drops]
    if dropped:
        logger.info(
            "[PiNativeSurface] qualification dropped %d candidate(s): %s",
            len(dropped),
            ", ".join(f"{d['tool']}({d['code']})" for d in dropped[:8]),
        )
    return {
        "selected": selected_with_unqualified,
        "drops": list(drops),
        "dropped": dropped,
        "specs_by_name": specs_by_name,
    }


def _record_abstained_receipt(registry: Any, session_id: str, turn_id: str) -> None:
    """V6 弃权 turn 的 receipt：native 前门 only + abstained 标记。

    回调 fingerprint echo 在弃权 turn 也要有对照真相（native 7 的 spec
    指纹）。session/turn 缺席或 ABI 关闭时跳过。
    """
    if not session_id or not turn_id:
        return
    try:
        from app.services.chat.pi_tool_spec import (
            compile_tool_spec,
            current_registry_generation,
            tool_abi_enabled,
        )
        from app.services.chat.pi_surface_receipt import record_turn_surface

        if not tool_abi_enabled():
            return
        generation = current_registry_generation()
        specs = []
        for name in NATIVE_TOOL_NAMES:
            try:
                spec = compile_tool_spec(registry, name, registry_generation=generation)
            except Exception:  # noqa: BLE001
                spec = None
            if spec is not None:
                specs.append(spec)
        record_turn_surface(
            specs,
            session_id=session_id,
            turn_id=turn_id,
            candidates=list(NATIVE_TOOL_NAMES),
            selected=list(NATIVE_TOOL_NAMES),
            drops=[],
            abstained=True,
        )
    except Exception:  # noqa: BLE001 — receipt 是披露/校验面，绝不阻断 turn
        logger.debug("[PiNativeSurface] abstained receipt mint failed", exc_info=True)
