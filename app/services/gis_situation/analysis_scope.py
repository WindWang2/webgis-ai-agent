"""AnalysisScope —— selection → 受控分析输入（ADR-0283 D1）。

任务书 E1「地图情境作为受控 Agent 输入」的执行面：把用户当前的地图选择
（IR ledger 投影 + pre-turn 快照 + canvas affordance 环）转成**经验证的**
分析 scope，供 agent 显式传参给真实分析工具。

两段分离（红线：前端上报不是可信事实本身，服务端验证 ref 所有权与 revision）：

- :func:`derive_claimed_scope` —— **纯函数**（零 IO）：合并 IR 投影 selection、
  pre-turn selected_feature、canvas affordance 环的最新几何动作，产出
  ClaimedScope（声称面，编译期可进 situation fact）；
- :func:`validate_analysis_scope` —— **IO 壳**（session store 只读）：五链验证
  （ref 所有权 → 宿主层在架 → revision 一致 → 要素存活 → 有界披露），全
  reason 封闭词表，失败不猜默认。

纪律：本模块不写任何权威状态；不向 ToolDispatchService 注入隐式 scope；
绝不内联 payload 进返回值（ids/摘要有界）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "derive_claimed_scope",
    "validate_analysis_scope",
    "MAX_LIVENESS_FEATURES",
    "MAX_SCOPE_FEATURE_IDS",
    "SCOPE_REASONS",
]

# ── 封闭 reason 词表（服务端验证结论；新增须同步 ADR-0283）──────────────

SCOPE_REASON_OK = "ok"
SCOPE_REASON_NO_SELECTION = "no_selection"
SCOPE_REASON_NO_DATASET_REF = "no_dataset_ref"
SCOPE_REASON_REF_UNRESOLVABLE = "ref_unresolvable"
SCOPE_REASON_LAYER_REMOVED = "layer_removed"
SCOPE_REASON_REVISION_DRIFTED = "revision_drifted"
SCOPE_REASON_FEATURES_MISSING = "features_missing"
SCOPE_REASONS = frozenset({
    SCOPE_REASON_OK, SCOPE_REASON_NO_SELECTION, SCOPE_REASON_NO_DATASET_REF,
    SCOPE_REASON_REF_UNRESOLVABLE, SCOPE_REASON_LAYER_REMOVED,
    SCOPE_REASON_REVISION_DRIFTED, SCOPE_REASON_FEATURES_MISSING,
})

#: 声称面 feature_ids 有界（与 selection-store MAX_SELECTED_IDS 对齐）。
MAX_SCOPE_FEATURE_IDS = 50
#: 要素存活逐点核对的数据集规模上限（超过即跳过并披露——不做全量扫描）。
MAX_LIVENESS_FEATURES = 20_000

_STATUS_USABLE = "usable"
_STATUS_STALE = "stale"
_STATUS_UNUSABLE = "unusable"


def _clip_ids(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    return [str(v)[:128] for v in raw[:MAX_SCOPE_FEATURE_IDS] if isinstance(v, (str, int))]


def derive_claimed_scope(
    interaction_snapshot: Optional[Dict[str, Any]],
    pre_turn_selected: Optional[Dict[str, Any]],
    *,
    affordance_ring: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """声称面（纯函数，零 IO）：合并三源 → ClaimedScope dict。

    优先级：IR ledger 投影 selection（server_seq 驱动、有 dataset_ref 语义）
    > pre-turn 前端快照（`_cartographic_context_observation.selected_feature`）
    > canvas affordance 环最新几何动作（AOI 面）。无任何证据 → kind="none"。
    """
    snapshot = interaction_snapshot if isinstance(interaction_snapshot, dict) else {}
    selection = snapshot.get("selection")
    selection = selection if isinstance(selection, dict) else None
    pre_turn = pre_turn_selected if isinstance(pre_turn_selected, dict) else None

    source = selection or pre_turn
    sources: List[str] = ["ir_ledger"] if selection is not None else (
        ["pre_turn"] if pre_turn is not None else []
    )
    if source is not None:
        multi = _clip_ids(source.get("selected_ids"))
        single = source.get("feature_id")
        single = str(single)[:128] if isinstance(single, (str, int)) else None
        feature_ids = multi or ([single] if single else [])
        claimed_seq = None
        if selection is not None:
            src = selection.get("source")
            if isinstance(src, dict) and isinstance(src.get("server_seq"), int):
                claimed_seq = src["server_seq"]
        claimed: Dict[str, Any] = {
            "kind": "feature_ids" if multi else ("feature_single" if single else "none"),
            "layer_id": (str(source.get("layer_id") or "")[:200] or None),
            "dataset_ref": (str(source.get("dataset_ref") or "")[:200] or None),
            "feature_ids": feature_ids or None,
            "claimed_at_seq": claimed_seq,
            "sources": sources,
        }
        if claimed["kind"] != "none":
            return claimed

    # AOI 面：canvas affordance 环最新带几何动作（框选/套索——几何本体在环内
    # 有界保留，见 canvas_affordance._sanitize_geometry）。
    for entry in reversed(affordance_ring or []):
        if not isinstance(entry, dict):
            continue
        geometry = entry.get("geometry")
        bbox = entry.get("bbox")
        if isinstance(geometry, dict) and isinstance(bbox, list) and len(bbox) == 4:
            return {
                "kind": "aoi",
                "layer_id": None,
                "dataset_ref": None,
                "feature_ids": None,
                "bbox": [float(v) for v in bbox],
                "geometry": geometry,
                "claimed_at_seq": entry.get("seq") if isinstance(entry.get("seq"), int) else None,
                "sources": ["canvas_affordance"],
            }
    return {
        "kind": "none", "layer_id": None, "dataset_ref": None,
        "feature_ids": None, "claimed_at_seq": None, "sources": [],
    }


def _spec_layer(spec: Dict[str, Any], layer_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not layer_id or not isinstance(spec.get("layers"), list):
        return None
    for layer in spec["layers"]:
        if isinstance(layer, dict) and str(layer.get("id")) == layer_id:
            return layer
    return None


def _feature_ids_in_payload(payload: Any, requested: List[str]) -> List[str]:
    """存活核对：payload（``{"geojson": FC}`` 信封或裸 FC）内按 FEATURE_ID_KEYS
    链解析每条要素 id（与 feature_edit 同链，不复制词表）。"""
    from app.services.feature_edit.identity import resolve_feature_id

    if isinstance(payload, dict) and isinstance(payload.get("geojson"), dict):
        payload = payload["geojson"]
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        return []
    wanted = set(requested)
    present: List[str] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        fid = resolve_feature_id(feature)  # type: ignore[arg-type]
        if fid and fid in wanted:
            present.append(fid)
            if len(present) == len(wanted):
                break
    return present


async def validate_analysis_scope(
    session_id: str,
    claimed: Dict[str, Any],
    *,
    store: Any = None,
    mapspec_store: Any = None,
) -> Dict[str, Any]:
    """验证面（IO 壳，只读）：ClaimedScope → ValidatedScope。

    reason 封闭词表；``status``: usable / stale / unusable。stale 语义＝可
    继续但必须向 agent/用户披露（部分要素缺失/数据版本漂移）；unusable＝
    不可作为分析输入。任何读失败 → unusable + 对应 reason（fail-closed）。
    """
    if store is None:
        from app.services.session_data import session_data_manager as store
    if mapspec_store is None:
        from app.services.mapspec.store import mapspec_store_instance as mapspec_store

    kind = str(claimed.get("kind") or "none")
    result: Dict[str, Any] = {
        "kind": kind,
        "status": _STATUS_UNUSABLE,
        "reason": SCOPE_REASON_NO_SELECTION,
        "dataset_ref": None,
        "layer_id": claimed.get("layer_id"),
        "feature_ids": None,
        "missing_ids": [],
        "content_revision": None,
        "crs": None,
        "bbox": None,
        "checks": {},
    }
    if kind == "none":
        return result

    if kind == "aoi":
        # AOI 面几何来自 canvas affordance 环（服务端摄入时已 sanitize）；
        # 无 dataset 所有权/存活问题，只回有界几何供分析参数使用。
        geometry = claimed.get("geometry")
        bbox = claimed.get("bbox")
        if not (isinstance(geometry, dict) and isinstance(bbox, list) and len(bbox) == 4):
            result["reason"] = SCOPE_REASON_NO_SELECTION
            return result
        result.update({
            "status": _STATUS_USABLE,
            "reason": SCOPE_REASON_OK,
            "bbox": [float(v) for v in bbox],
            "geometry": geometry,
            "checks": {
                "ownership": "not_applicable",
                "layer": "not_applicable",
                "revision": "not_applicable",
                "features": "not_applicable",
            },
        })
        return result

    # ── feature_ids / feature_single：五链验证 ──────────────────────────
    dataset_ref = claimed.get("dataset_ref")
    if not dataset_ref:
        result["reason"] = SCOPE_REASON_NO_DATASET_REF
        return result
    result["dataset_ref"] = dataset_ref

    getter = getattr(store, "get", None)
    payload: Any = None
    if callable(getter):
        try:
            payload = await getter(session_id, dataset_ref)
        except Exception:  # noqa: BLE001 — 读失败 = 不可验证（fail-closed）
            logger.warning("[analysis_scope] ref read failed for %s", dataset_ref)
            payload = None
    if payload is None:
        # 所有权：ref 只在本 session 的 store 内可解析（跨会话 ref 拒绝）。
        result["reason"] = SCOPE_REASON_REF_UNRESOLVABLE
        return result
    result["checks"]["ownership"] = "ok"

    descriptor: Dict[str, Any] = {}
    desc_getter = getattr(store, "get_ref_descriptor", None)
    if callable(desc_getter):
        try:
            descriptor = await desc_getter(session_id, dataset_ref) or {}
        except Exception:  # noqa: BLE001 — 描述符缺席按空处理（显式 unknown）
            descriptor = {}
    if not isinstance(descriptor, dict):
        descriptor = {}
    content_revision = descriptor.get("content_revision")
    result["content_revision"] = (
        int(content_revision) if isinstance(content_revision, (int, float)) else None
    )
    result["crs"] = descriptor.get("crs") if isinstance(descriptor.get("crs"), str) else None
    result["bbox"] = (
        [float(v) for v in descriptor["bbox"]]
        if isinstance(descriptor.get("bbox"), list) and len(descriptor["bbox"]) == 4
        else None
    )

    # 宿主层在架（desired spec 只读）。
    try:
        spec = await mapspec_store.get_mapspec(session_id)
    except Exception:  # noqa: BLE001 — spec 读失败按 unknown 披露，不阻断
        spec = {}
    spec = spec if isinstance(spec, dict) else {}
    layer_id = claimed.get("layer_id")
    layer = _spec_layer(spec, layer_id)
    if layer_id and isinstance(spec.get("layers"), list) and layer is None:
        result["reason"] = SCOPE_REASON_LAYER_REMOVED
        return result
    result["checks"]["layer"] = "ok" if layer is not None else "unknown"

    # revision 一致（层 provenance 盖章 vs descriptor；无盖章 → 诚实 unavailable）。
    provenance = layer.get("provenance") if isinstance(layer, dict) else None
    stamped = (
        provenance.get("content_revision")
        if isinstance(provenance, dict)
        else None
    )
    if isinstance(stamped, (int, float)) and result["content_revision"] is not None:
        if int(stamped) != result["content_revision"]:
            result["reason"] = SCOPE_REASON_REVISION_DRIFTED
            result["status"] = _STATUS_STALE
            result["checks"]["revision"] = "drifted"
            return result
        result["checks"]["revision"] = "ok"
    else:
        result["checks"]["revision"] = "unavailable"

    # 要素存活（≤ MAX_LIVENESS_FEATURES 才逐点核对；超限跳过披露）。
    requested = _clip_ids(claimed.get("feature_ids"))
    feature_count = descriptor.get("feature_count")
    if not requested:
        result["checks"]["features"] = "not_applicable"
        result["status"] = _STATUS_USABLE
        result["reason"] = SCOPE_REASON_OK
        return result
    if isinstance(feature_count, (int, float)) and int(feature_count) > MAX_LIVENESS_FEATURES:
        result["checks"]["features"] = "skipped_large_dataset"
        result["feature_ids"] = requested
        result["status"] = _STATUS_USABLE
        result["reason"] = SCOPE_REASON_OK
        return result
    present = _feature_ids_in_payload(payload, requested)
    missing = [fid for fid in requested if fid not in set(present)]
    result["missing_ids"] = missing
    if not present:
        result["reason"] = SCOPE_REASON_FEATURES_MISSING
        result["status"] = _STATUS_STALE
        result["checks"]["features"] = "all_missing"
        return result
    result["feature_ids"] = present
    result["checks"]["features"] = "partial_missing" if missing else "ok"
    result["status"] = _STATUS_USABLE
    result["reason"] = SCOPE_REASON_OK
    return result
