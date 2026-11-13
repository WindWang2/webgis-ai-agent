"""稳定要素身份 —— `_wfid` 确定性物化与目标解析（/goal 03）。

- 物化只在**首次编辑**发生（opt-in），把 ``wf1-<sha256[:16]>`` 写进每条
  要素 properties；id 由 (dataset_fingerprint, index) 决定性导出 —— 同一
  base 重物化得到同一批 id（幂等）。
- 物化前目标解析走与前后端一致的 FEATURE_ID_KEYS 链；物化后一律 WFID
  寻址。既有 id 键（feature.id / OBJECTID / ...）原样保留，绝不改写。
"""
from __future__ import annotations

from app.contracts.geojson import GeoJSONFeatureCollection, GeoJSONFeature

import hashlib
from typing import Any, Dict, List, Optional, Tuple, cast

from app.services.feature_edit.contracts import FEATURE_ID_KEYS, WFID_PROP

__all__ = [
    "materialize_wfids",
    "has_wfids",
    "index_by_wfid",
    "resolve_feature_ids",
    "wfid_for_index",
    "wfid_for_created",
]


def wfid_for_index(dataset_fingerprint: str, index: int) -> str:
    digest = hashlib.sha256(f"{dataset_fingerprint}:{index}".encode("utf-8")).hexdigest()[:16]
    return f"wf1-{digest}"


def wfid_for_created(plan_seed: str, op_index: int, child_index: int) -> str:
    """G14：create/split/merge 新生要素的服务端受控身份。

    派生自 (plan_seed=plan_id, op_index, child_index) —— 同一 plan 的
    preview 与 commit 重新求值得到同一批 id（幂等重放/redo 身份稳定，
    绝不按数组下标或时钟重铸）。plan_id 内嵌服务端 UUID，调用方（模型/
    前端）无法预知或碰撞既有 wf1- 身份（hash 前缀与物化空间一致，但
    输入域不同 —— 冲突概率按生日界 ≤2^32 个创建计划后 ~0.003%，且
    plan 校验显式查重后 fail-closed）。
    """
    digest = hashlib.sha256(f"create:{plan_seed}:{op_index}:{child_index}".encode("utf-8")).hexdigest()[:16]
    return f"wf1-{digest}"


def materialize_wfids(
    fc: GeoJSONFeatureCollection, dataset_fingerprint: str
) -> Tuple[GeoJSONFeatureCollection, int]:
    """给 FeatureCollection 的每条要素物化 WFID_PROP（copy-on-write）。

    已带 WFID_PROP 的要素不动（幂等）。返回 (new_fc, assigned_count)。
    """
    features = fc.get("features") or []
    out_features: List[GeoJSONFeature] = []
    assigned = 0
    dirty = False
    for index, feature in enumerate(features):
        props = feature.get("properties") if isinstance(feature, dict) else None
        if isinstance(props, dict) and isinstance(props.get(WFID_PROP), str):
            out_features.append(feature)
            continue
        new_feature = cast(GeoJSONFeature, dict(feature) if isinstance(feature, dict) else {"type": "Feature"})
        new_props = dict(new_feature.get("properties") or {})
        new_props[WFID_PROP] = wfid_for_index(dataset_fingerprint, index)
        new_feature["properties"] = new_props
        out_features.append(new_feature)
        assigned += 1
        dirty = True
    if not dirty:
        return fc, 0
    return {**fc, "features": out_features}, assigned


def has_wfids(fc: GeoJSONFeatureCollection) -> bool:
    """数据集是否已完成 WFID 物化（全量校验 —— R1 P3-9：部分物化的数据集
    返回 False，materialize_wfids 会确定性补齐缺失（fingerprint:index 稳定）。"""
    features = fc.get("features") or []
    if not features:
        return False
    for feature in features:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not (isinstance(props, dict) and isinstance(props.get(WFID_PROP), str)):
            return False
    return True


def index_by_wfid(fc: GeoJSONFeatureCollection) -> Dict[str, int]:
    """WFID → features 数组下标（内存索引，调用方自建不持久）。"""
    out: Dict[str, int] = {}
    for index, feature in enumerate(fc.get("features") or []):
        props = feature.get("properties") if isinstance(feature, dict) else None
        if isinstance(props, dict) and isinstance(props.get(WFID_PROP), str):
            out[str(props[WFID_PROP])] = index
    return out

def find_ambiguous_ids(fc: GeoJSONFeatureCollection, requested: List[str]) -> Dict[str, int]:
    """G24/P0：requested id → 命中要素数（>1 的才是歧义；wfids 不歧义）。

    重复自然键（OBJECTID/osm_id 等同值多条）在 first-wins 解析下会编辑到
    用户没点的对象 —— 调用方对歧义 id 出 typed finding（ambiguous_target），
    绝不静默选首个。WFID 寻址（唯一）不视为歧义。
    """
    # 与回查同语义：请求 id 可经**任一**链键（顶层 id / properties 任一
    # FEATURE_ID_KEYS / _wfid）命中要素 —— 歧义 = 该值可命中 >1 条要素。
    value_feature_counts: Dict[str, int] = {}
    wfid_values: set = set()
    for feature in fc.get("features") or []:
        if not isinstance(feature, dict):
            continue
        values: set = set()
        top = feature.get("id")
        if isinstance(top, (str, int)) and not isinstance(top, bool):
            values.add(str(top))
        props = feature.get("properties")
        if isinstance(props, dict):
            for key in FEATURE_ID_KEYS:
                v = props.get(key)
                if isinstance(v, (str, int)) and not isinstance(v, bool):
                    values.add(str(v))
            wfid = props.get(WFID_PROP)
            if isinstance(wfid, str):
                wfid_values.add(str(wfid))
        for value in values:
            value_feature_counts[value] = value_feature_counts.get(value, 0) + 1
    return {
        str(r): value_feature_counts[str(r)]
        for r in requested
        if str(r) not in wfid_values and value_feature_counts.get(str(r), 0) > 1
    }


def resolve_feature_id(feature: GeoJSONFeature) -> Optional[str]:
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


def resolve_feature_ids(fc: GeoJSONFeatureCollection, requested: List[str]) -> Dict[str, str]:
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
        found_index = id_to_index.get(str(rid))
        if found_index is not None and found_index in wfid_by_index:
            out[str(rid)] = wfid_by_index[found_index]
    return out


def natural_to_wfid_index(fc: GeoJSONFeatureCollection) -> Dict[str, str]:
    """物化 fc 的自然键 + WFID → wfid 索引（first-wins）。

    G24 把 `_wfid` 置 FEATURE_ID_KEYS 链首后 resolve_feature_ids 对物化
    数据的自然键不再命中；op 级目标限定（G32）需要自然键 ↔ 稳定身份的
    双向锚定 —— 物化不删除自然键，只是寻址优先级变化。
    """
    out: Dict[str, str] = {}
    for feature in fc.get("features") or []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        wfid = props.get(WFID_PROP) if isinstance(props, dict) else None
        if not isinstance(wfid, str):
            continue
        out.setdefault(wfid, wfid)
        natural = resolve_feature_id(feature)
        if natural is not None and natural != wfid:
            out.setdefault(natural, wfid)
    return out


def normalize_op_wfids(
    ops: List[Dict[str, Any]], mat_fc: GeoJSONFeatureCollection,
) -> List[Dict[str, Any]]:
    """op 级 wfids 自然 id → 稳定 wfid（deep copy，不改入参；G32）。

    求值的 target 交集在 wfid 空间上做；解析失败的 id 原样保留（由
    feature_not_found 形状校验诚实拒绝）。mat_fc 应为物化（预演）形态。
    """
    import copy as _copy

    raw_ids: List[str] = []
    for op in ops:
        if isinstance(op, dict):
            if isinstance(op.get("wfids"), list):
                raw_ids.extend(str(w) for w in op["wfids"] if isinstance(w, str))
            if isinstance(op.get("wfid"), str):
                raw_ids.append(op["wfid"])  # split_feature 单值目标
    if not raw_ids:
        return ops
    wfid_map = natural_to_wfid_index(mat_fc)
    normalized = _copy.deepcopy(ops)
    for op in normalized:
        if not isinstance(op, dict):
            continue
        if isinstance(op.get("wfids"), list):
            op["wfids"] = [wfid_map.get(str(w), str(w)) for w in op["wfids"]]
        if isinstance(op.get("wfid"), str):
            op["wfid"] = wfid_map.get(str(op["wfid"]), op["wfid"])
    return normalized
