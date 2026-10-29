"""稳定要素身份 —— `_wfid` 确定性物化与目标解析（/goal 03）。

- 物化只在**首次编辑**发生（opt-in），把 ``wf1-<sha256[:16]>`` 写进每条
  要素 properties；id 由 (dataset_fingerprint, index) 决定性导出 —— 同一
  base 重物化得到同一批 id（幂等）。
- 物化前目标解析走与前后端一致的 FEATURE_ID_KEYS 链；物化后一律 WFID
  寻址。既有 id 键（feature.id / OBJECTID / ...）原样保留，绝不改写。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple

from app.services.feature_edit.contracts import FEATURE_ID_KEYS, WFID_PROP

__all__ = [
    "materialize_wfids",
    "has_wfids",
    "index_by_wfid",
    "resolve_feature_ids",
    "wfid_for_index",
]


def wfid_for_index(dataset_fingerprint: str, index: int) -> str:
    digest = hashlib.sha256(f"{dataset_fingerprint}:{index}".encode("utf-8")).hexdigest()[:16]
    return f"wf1-{digest}"


def materialize_wfids(
    fc: Dict[str, Any], dataset_fingerprint: str
) -> Tuple[Dict[str, Any], int]:
    """给 FeatureCollection 的每条要素物化 WFID_PROP（copy-on-write）。

    已带 WFID_PROP 的要素不动（幂等）。返回 (new_fc, assigned_count)。
    """
    features = fc.get("features") or []
    out_features: List[Dict[str, Any]] = []
    assigned = 0
    dirty = False
    for index, feature in enumerate(features):
        props = feature.get("properties") if isinstance(feature, dict) else None
        if isinstance(props, dict) and isinstance(props.get(WFID_PROP), str):
            out_features.append(feature)
            continue
        new_feature = dict(feature) if isinstance(feature, dict) else {"type": "Feature"}
        new_props = dict(new_feature.get("properties") or {})
        new_props[WFID_PROP] = wfid_for_index(dataset_fingerprint, index)
        new_feature["properties"] = new_props
        out_features.append(new_feature)
        assigned += 1
        dirty = True
    if not dirty:
        return fc, 0
    return {**fc, "features": out_features}, assigned


def has_wfids(fc: Dict[str, Any]) -> bool:
    """数据集是否已完成 WFID 物化（以首条要素为准——物化是全量原子动作）。"""
    features = fc.get("features") or []
    if not features:
        return False
    props = features[0].get("properties") if isinstance(features[0], dict) else None
    return isinstance(props, dict) and isinstance(props.get(WFID_PROP), str)


def index_by_wfid(fc: Dict[str, Any]) -> Dict[str, int]:
    """WFID → features 数组下标（内存索引，调用方自建不持久）。"""
    out: Dict[str, int] = {}
    for index, feature in enumerate(fc.get("features") or []):
        props = feature.get("properties") if isinstance(feature, dict) else None
        if isinstance(props, dict) and isinstance(props.get(WFID_PROP), str):
            out[props[WFID_PROP]] = index
    return out


def resolve_feature_id(feature: Dict[str, Any]) -> Optional[str]:
    """单要素 → 既有稳定 id（FEATURE_ID_KEYS 链；顶层 feature.id 优先）。

    None = 无可用 id（物化前的裸数据集）。
    """
    if not isinstance(feature, dict):
        return None
    top = feature.get("id")
    if isinstance(top, (str, int)) and not isinstance(top, bool):
        return str(top)
    props = feature.get("properties")
    if isinstance(props, dict):
        for key in FEATURE_ID_KEYS:
            value = props.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return str(value)
    return None


def resolve_feature_ids(fc: Dict[str, Any], requested: List[str]) -> Dict[str, str]:
    """requested（前端/模型给的既有 id 集）→ {requested_id: WFID}。

    仅对已物化数据集使用：WFID 索引 + 既有 id → WFID 的映射。未命中的
    requested id 不出现在结果里（调用方对缺失项出 feature_not_found）。
    """
    features = fc.get("features") or []
    id_to_index: Dict[str, int] = {}
    wfid_by_index: Dict[int, str] = {}
    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        wfid = props.get(WFID_PROP) if isinstance(props, dict) else None
        if isinstance(wfid, str):
            wfid_by_index[index] = wfid
        existing = resolve_feature_id(feature)
        if existing is not None and existing not in id_to_index:
            id_to_index[existing] = index
        if isinstance(wfid, str):
            # WFID 本身也是合法寻址键（前端提案卡/回填直接带 WFID）
            if wfid not in id_to_index:
                id_to_index[wfid] = index
    out: Dict[str, str] = {}
    for rid in requested:
        index = id_to_index.get(str(rid))
        if index is not None and index in wfid_by_index:
            out[str(rid)] = wfid_by_index[index]
    return out
