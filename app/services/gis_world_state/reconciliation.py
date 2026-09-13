"""Desired vs observed reconciliation —— 地图状态对账（方向 8 / ADR-0183 U7）。

对账四方（priorities，与任务书 U7 对齐）：

    1. server MapSpec（磁盘+Redis 权威 spec）
    2. runtime layer registry（map_state ``layers``，渲染面镜像）
    3. frontend rendered map（客户端，本函数不直接可见 —— 经 observation）
    4. pending queue（客户端在途删除压制，经 ``pending_removed`` 传入）

本模块是**纯函数层**：输入快照、输出有界 anomaly 列表（确定性排序），不发
IO、不加锁。生产接线：``ws_collab`` sync 应答附带 bounded anomalies（只读
投影，失败不阻断 sync 主语义）；调用方（修复环/审计）也可独立消费。

Anomaly codes（优先级序，见 precedence.reconcile_priority）：
- ``USER_HIDDEN_BUT_VISIBLE``：spec 中 presentation_owner=="user" 且隐藏的
  层在 runtime 可见 —— user-wins 被破坏，最高优先处置。
- ``ZOMBIE_RUNTIME_LAYER``：runtime 有、spec 无的层（僵尸复活形态；ws_service
  遗留直写通道的主要暴露面）。
- ``SPEC_LAYER_MISSING_RUNTIME``：spec 有、runtime 无 —— 渲染面缺层。
- ``VISIBILITY_MISMATCH``：两侧都在但可见性不一致（非 user-owned）。
- ``STALE_PENDING_REMOVED``：pending_removed 引用的层在 spec 中已不存在
  （压制已无必要，提示清理）。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.services.gis_world_state.precedence import reconcile_priority

# anomaly 列表输出上限（有界披露：sync 载荷不随偏差数无界膨胀）。
MAX_ANOMALIES = 32


def _layer_family_members(layer_id: str, ids: Iterable[str]) -> List[str]:
    """层族成员（与引擎删层谓词一致的 -/__ 后缀族语义）。"""
    return [
        x for x in ids
        if x == layer_id
        or x.startswith(f"{layer_id}-")
        or x.startswith(f"{layer_id}__")
        or (layer_id.startswith(f"{x}-") or layer_id.startswith(f"{x}__"))
    ]


def _visible(layer: Dict[str, Any]) -> bool:
    layout = layer.get("layout") if isinstance(layer.get("layout"), dict) else {}
    if layout.get("visibility", "visible") == "none":
        return False
    return layer.get("visible", True) is not False


def _user_owned_hidden(layer: Dict[str, Any]) -> bool:
    intent = (
        layer.get("cartographic_intent")
        if isinstance(layer.get("cartographic_intent"), dict) else {}
    )
    return (
        intent.get("presentation_owner") == "user"
        and intent.get("expected_visible") is False
    )


def reconcile_map_state(
    mapspec: Optional[Dict[str, Any]],
    runtime_layers: Optional[List[Dict[str, Any]]],
    *,
    pending_removed: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """四方对账 → 确定性 anomaly 列表（优先级升序，上限 MAX_ANOMALIES）。

    输入均为快照（调用方负责一致性读 —— 引擎锁内或 map_state 单读）；
    同输入同输出（排序键：优先级 → code → layer_id，全序可重放）。
    """
    spec_layers = [
        dict(layer) for layer in (mapspec or {}).get("layers", []) or []
        if isinstance(layer, dict) and layer.get("id")
    ]
    runtime = [
        dict(layer) for layer in runtime_layers or []
        if isinstance(layer, dict) and layer.get("id")
    ]
    spec_ids = {str(layer["id"]) for layer in spec_layers}
    runtime_ids = {str(layer["id"]) for layer in runtime}
    spec_by_id = {str(layer["id"]): layer for layer in spec_layers}
    runtime_by_id = {str(layer["id"]): layer for layer in runtime}

    anomalies: List[Dict[str, Any]] = []

    for layer_id, layer in runtime_by_id.items():
        spec_layer = spec_by_id.get(layer_id)
        if spec_layer is None:
            # 层族语义：物理/别名变体（-label、__x）按族归并，避免 N 条噪音。
            if _layer_family_members(layer_id, spec_ids):
                continue
            anomalies.append({
                "code": "ZOMBIE_RUNTIME_LAYER",
                "layer_id": layer_id,
                "detail": {"runtime_visible": _visible(layer)},
            })
            continue
        if _user_owned_hidden(spec_layer) and _visible(layer):
            anomalies.append({
                "code": "USER_HIDDEN_BUT_VISIBLE",
                "layer_id": layer_id,
                "detail": {"owner": "user"},
            })
        elif _visible(spec_layer) != _visible(layer):
            anomalies.append({
                "code": "VISIBILITY_MISMATCH",
                "layer_id": layer_id,
                "detail": {"spec": _visible(spec_layer), "runtime": _visible(layer)},
            })

    for layer_id in sorted(spec_ids - runtime_ids):
        if _layer_family_members(layer_id, runtime_ids):
            continue
        anomalies.append({
            "code": "SPEC_LAYER_MISSING_RUNTIME",
            "layer_id": layer_id,
            "detail": {"spec_visible": _visible(spec_by_id[layer_id])},
        })

    for layer_id in dict.fromkeys(pending_removed or []):
        if not layer_id:
            continue
        if not _layer_family_members(layer_id, spec_ids):
            anomalies.append({
                "code": "STALE_PENDING_REMOVED",
                "layer_id": layer_id,
                "detail": {},
            })

    anomalies.sort(key=lambda a: (
        reconcile_priority(str(a["code"])),
        str(a["code"]),
        str(a["layer_id"]),
    ))
    return anomalies[:MAX_ANOMALIES]
