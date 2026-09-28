"""Legacy 工具调用 → GISAction 投影适配层（H10 / ADR-0217）。

把既有 ToolRegistry 工具调用（LLM 直拼的工具名+参数）确定性投影为
GISAction，使**每一条** legacy direct 路径都先有 typed IR —— 这是
"兼容层 + 逐步收敛指标"的收敛面本身：

- kind / side_effect / idempotency / resource 全部来自 descriptor 事实
  （单一权威），绝不从工具名猜语义；
- params 只收**小标量 token**：超预算键降级为指纹占位（数据本体永不
  入 IR —— inline GeoJSON 在此被结构性挡住）；
- ``ref:`` 形态参数 → inputs 引用 + data_ref_alive 前提；
- 使用遥测：进程内有界计数器（direct vs plan 路由、blocking 命中），
  即 legacy 收敛指标 —— 不落盘、不进存储（权威遥测仍是 tool_metrics）。
"""
from __future__ import annotations

import threading
from collections import Counter
from typing import Any, Dict, List, Mapping

from app.lib.gis.action_ir import (
    ACTION_IR_VERSION,
    Compensation,
    GISAction,
    GISActionPlan,
    IODescriptor,
    Precondition,
    _canon,
    compute_plan_id,
    digest_of,
)

_STR_MAX = 256

__all__ = [
    "project_tool_call_to_action",
    "project_tool_call_to_plan",
    "record_usage", "usage_snapshot", "reset_usage_counters",
]

# ── 词表映射（descriptor 事实 → ActionKind；顺序即优先级）───────────────

_MUTATION_NAME_PREFIXES = (
    "webgis_layer_upsert", "webgis_layer_remove", "webgis_layout_set",
    "webgis_view_set", "webgis_project_init", "webgis_apply_composition",
    "webgis_checkpoint", "webgis_rollback", "webgis_plan_component_replace",
    "update_layer_appearance", "display_layer", "remove_layer",
    "reorder_layer", "alias_layer", "apply_layer_filter", "switch_base_layer",
    "set_layer_status", "set_map_view", "finalize_display",
    "apply_mutation", "set_map_layout",
)
_EXPORT_NAME_PREFIXES = ("export", "report", "generate_report")
_OBSERVE_NAME_PREFIXES = (
    "webgis_validate", "webgis_runtime_validate", "webgis_cartography_status",
    "visual", "render_observe", "capture",
)
_CARTOGRAPH_NAME_PREFIXES = (
    "webgis_compile_map_plan", "webgis_compile_maplibre", "thematic",
    "symbology",
)
_ACQUIRE_NAME_PREFIXES = (
    "ingest", "upload", "query_dataset", "aggregate_dataset",
    "connect_data_source", "materialize_dataset", "query_federated_data",
    "plan_data_query", "search_spatial_catalog", "search_datasets",
    "profile_dataset", "list_datasets", "describe_artifact",
    "find_artifacts_by_role", "get_lineage", "deep_explore", "geocode",
    "osm", "local_admin",
)
_ANALYZE_NAME_PREFIXES = (
    "hotspot", "local_moran", "h3_", "geodetector", "gwr_", "kde_",
    "join_count", "bivariate_", "spatial_", "grid_", "buffer_", "clip_",
    "interpolate", "kriging", "morans_", "ols_", "flow_", "point_pattern",
    "dasymetric", "run_analysis", "execute_analysis",
)
_TRANSFORM_NAME_PREFIXES = ("chart", "contour_from", "derive")

_KIND_BY_SIDE_EFFECT: Dict[str, str] = {
    # descriptor side_effect 是 mutating 语义唯一权威。
    "state_mutation": "mutate_presentation",
}


def _kind_for(tool_name: str, meta: Mapping[str, Any]) -> str:
    """descriptor 事实 → ActionKind（确定性；默认保守 inspect）。"""
    side_effect = str(meta.get("side_effect") or "")
    if _KIND_BY_SIDE_EFFECT.get(side_effect) == "mutate_presentation":
        # export 例外优先：外部副作用+导出语义 ≠ 地图 mutation。
        if not tool_name.startswith(_EXPORT_NAME_PREFIXES):
            return "mutate_presentation"
    for prefixes, kind in (
        (_EXPORT_NAME_PREFIXES, "export"),
        (_OBSERVE_NAME_PREFIXES, "observe"),
        (_CARTOGRAPH_NAME_PREFIXES, "cartograph"),
        (_ACQUIRE_NAME_PREFIXES, "data_acquire"),
        (_ANALYZE_NAME_PREFIXES, "analyze"),
        (_TRANSFORM_NAME_PREFIXES, "transform"),
    ):
        if tool_name.startswith(prefixes):
            return kind
    return "inspect"


def _idempotency_for(meta: Mapping[str, Any]) -> str:
    side_effect = str(meta.get("side_effect") or "")
    if side_effect in ("external_side_effect", "destructive"):
        return "non_idempotent"
    if side_effect == "state_mutation":
        return "duplicate_safe"   # 引擎 CAS + client mutation id 去重
    if bool(meta.get("idempotent")):
        return "idempotent"
    return "idempotent" if side_effect in (
        "pure", "deterministic_compute", "cacheable_read") else "non_idempotent"


def _side_effect_class_for(meta: Mapping[str, Any]) -> str:
    return {
        "pure": "pure",
        "deterministic_compute": "pure",
        "cacheable_read": "pure",
        "state_mutation": "session_state",
        "artifact_creation": "derived",
        "external_side_effect": "external_io",
        "destructive": "session_state",
    }.get(str(meta.get("side_effect") or ""), "pure")


def _sampled_digest(value: Any) -> str:
    """O(1) 结构指纹（type+size+str 头部采样）—— 超预算键的占位 token。

    非完整性指纹：占位的目的只是"此处曾有数据、不适合入 IR"，跨值唯一性
    不作承诺（dedup 语义在 dispatch 层按全参 key，不经此 token）。
    对 2MB+ payload 也绝不付 canonical JSON 的 O(payload) 成本。
    """
    if isinstance(value, str):
        basis = f"str:{len(value)}:{value[:48]}"
    elif isinstance(value, (dict, list, tuple, set, frozenset)):
        basis = f"{type(value).__name__}:{len(value)}"
    else:
        basis = f"{type(value).__name__}:{str(value)[:48]}"
    import hashlib

    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _node_count(value: Any, cap: int = 64) -> int:
    """廉价结构规模估计（节点数，到 cap 即止 —— O(min(n, cap))）。

    顶层键少的深嵌套大对象（FC = {type, features:[20k]}）不能只看
    len(top) —— canonical 计费是 O(节点) 的，必须先做结构闸。
    """
    if isinstance(value, dict):
        n = len(value)
        if n > cap:
            return n
        for v in value.values():
            n += _node_count(v, cap - n)
            if n > cap:
                return n
        return n
    if isinstance(value, (list, tuple, set, frozenset)):
        n = len(value)
        if n > cap:
            return n
        for v in value:
            n += _node_count(v, cap - n)
            if n > cap:
                return n
        return n
    return 1


def _params_projection(args: Mapping[str, Any]) -> Dict[str, Any]:
    """小标量 token 收敛：超预算键 → 指纹占位（数据本体结构性出局）。

    计费口径与 IR 强制口径一致（canonical JSON 字节，非 str 长度 ——
    转义/Unicode 会使 str 长度低估真实入账 bytes）；canonical 计费前先
    过 O(1) 结构闸，大对象绝不进 canonical（热路径 O(payload) 禁止）。
    """
    from app.lib.gis.action_ir import _PARAMS_KEYS_MAX, _PARAMS_BYTES_MAX

    def _cost(token: Any) -> int:
        return len(_canon(token).encode("utf-8"))

    out: Dict[str, Any] = {}
    budget = _PARAMS_BYTES_MAX
    for key in sorted(args.keys(), key=str)[:_PARAMS_KEYS_MAX]:
        value = args[key]
        if isinstance(value, (str, int, float, bool)) or value is None:
            token = value
        elif isinstance(value, (dict, list)) and len(value) <= 32 \
                and _node_count(value) <= 64 and _cost(value) <= 256:
            token = value
        else:
            # 大对象（inline GeoJSON 等）：只留 O(1) 结构指纹占位。
            out[f"{str(key)[:40]}__sha"] = _sampled_digest(value)
            continue
        cost = _cost(token)
        if cost > budget:
            out[f"{str(key)[:40]}__sha"] = _sampled_digest(value)
            continue
        out[str(key)[:48]] = token
        budget -= cost
        if budget <= 0:
            break
    return out


def _ref_inputs(args: Mapping[str, Any]) -> List[IODescriptor]:
    """``ref:`` 形态参数 → 输入引用 + data_ref_alive 前提原料。"""
    inputs: List[IODescriptor] = []
    for key in sorted(args.keys(), key=str):
        value = args[key]
        if isinstance(value, str) and value.startswith("ref:") \
                and len(inputs) < 8:
            inputs.append(IODescriptor(
                name=str(key)[:64], ref=value[:128]))
        # layer dict 里的 source ref 也算（webgis_layer_upsert 形态）。
        elif isinstance(value, dict) and isinstance(value.get("source"), str) \
                and value["source"].startswith("ref:") and len(inputs) < 8:
            inputs.append(IODescriptor(
                name=f"{str(key)[:40]}.source", ref=value["source"][:128]))
    return inputs


def _compensation_for(tool_name: str, args: Mapping[str, Any],
                      kind: str) -> Compensation:
    """可逆面声明（目标可从 args 确定性取出才声明；否则 none）。

    只声明 executor 可合成的补偿：upsert 层的逆 = remove_layer；
    style patch 无 prior 快照不可合成 —— 绝不虚报 restore_layer_style
    （虚报 = 回滚时 100% UNCOMPENSATED，污染 receipt）。
    """
    if kind != "mutate_presentation":
        return Compensation()
    layer = args.get("layer") if isinstance(args.get("layer"), dict) else {}
    layer_id = str(args.get("layer_id") or layer.get("id") or "")
    if not layer_id:
        return Compensation()
    if tool_name.startswith("webgis_layer_upsert"):
        return Compensation(kind="remove_layer", target=layer_id[:128])
    return Compensation()


def project_tool_call_to_action(
    tool_name: str,
    args: Mapping[str, Any],
    meta: Mapping[str, Any],
    *,
    action_id: str = "",
) -> GISAction:
    """一次工具调用 → GISAction（确定性；meta = registry.metadata 投影）。"""
    kind = _kind_for(tool_name, meta)
    inputs = _ref_inputs(args)
    preconditions = [
        Precondition(kind="data_ref_alive", target=i.ref)
        for i in inputs
    ]
    params = _params_projection(args)
    # action_id 只 digest 投影 token（非全量原始 args —— 大 payload 下
    # canonical JSON + sha256 是 O(payload) 热路径成本，见 ADR-0217）。
    return GISAction(
        action_id=action_id
        or f"act-{digest_of({'tool': tool_name, 'params': params})[:12]}",
        kind=kind,  # type: ignore[arg-type]
        tool=tool_name[:96],
        title=str(meta.get("summary") or "")[:_STR_MAX],
        inputs=inputs,
        outputs=[IODescriptor(
            name="result",
            semantic_type=str(meta.get("output_semantic_type") or "")[:64],
        )],
        preconditions=preconditions,
        params=params,
        side_effect=_side_effect_class_for(meta),  # type: ignore[arg-type]
        idempotency=_idempotency_for(meta),  # type: ignore[arg-type]
        failure="fail_closed",
        resource_class={
            dim: str(meta.get(f"{dim}_class") or "")[:24]
            for dim in ("latency", "memory", "scale")
            if meta.get(f"{dim}_class")
        },
        compensation=_compensation_for(tool_name, args, kind),
        reason_codes=["LEGACY_ADAPTER_V1"],
    )


def project_tool_call_to_plan(
    tool_name: str,
    args: Mapping[str, Any],
    meta: Mapping[str, Any],
) -> GISActionPlan:
    """单工具调用 → 单动作计划（dispatch 投影形态；origin=tool_call）。"""
    action = project_tool_call_to_action(tool_name, args, meta)
    body = {
        "plan_version": ACTION_IR_VERSION,
        "revision": 1,
        "origin": "tool_call",
        "actions": [action.model_dump()],
    }
    return GISActionPlan(
        plan_id=compute_plan_id(body),
        origin="tool_call",
        actions=[action],
    )


# ── 使用遥测（进程内有界；legacy 收敛指标）────────────────────────────────

_LOCK = threading.Lock()
_CHANNELS = ("direct_routed", "plan_routed", "compile_blocked", "compile_hint")
_USAGE: Dict[str, Counter] = {ch: Counter() for ch in _CHANNELS}


def record_usage(channel: str, kind: str) -> None:
    """计数（channel ∈ _CHANNELS；kind = ActionKind）。未知值忽略。"""
    bucket = _USAGE.get(channel)
    if bucket is None:
        return
    with _LOCK:
        bucket[str(kind)[:32]] += 1


def usage_snapshot() -> Dict[str, Dict[str, int]]:
    """有界快照（channel → kind → count；排序保证确定性投影）。"""
    with _LOCK:
        return {
            ch: dict(sorted(c.items()))
            for ch, c in _USAGE.items()
        }


def reset_usage_counters() -> None:
    """测试专用清零（生产不调用）。"""
    with _LOCK:
        for c in _USAGE.values():
            c.clear()
