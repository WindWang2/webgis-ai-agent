"""候选求值 —— 提案 ops 在给定数据形态上的确定性重放（G32 WP04）。

复用 G14 纯求值路径（derive_changes），seed = 提案持久的 op_seed：同一候选
跨重载/重预览/提交重求值得到**同一批 child `_wfid`**（wfid_for_created 的
决定性派生），不依赖即将过期的 plan 缓存。候选可重算验证（digest 一致），
几何大载荷不外发 —— 差异证据经有界投影（field before/after 全量、几何摘要）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.feature_edit.identity import has_wfids, materialize_wfids
from app.services.feature_edit.plan import derive_changes, extract_fc
from app.services.feature_edit.wire_types import FeatureDelta


def _natural_id_of(feature: Dict[str, Any]) -> Optional[str]:
    """自然键（跳过 `_wfid` 的 FEATURE_ID_KEYS 链；顶层 id 优先）。"""
    from app.services.feature_edit.contracts import FEATURE_ID_KEYS

    if not isinstance(feature, dict):
        return None
    top = feature.get("id")
    if isinstance(top, (str, int)) and not isinstance(top, bool):
        return str(top)
    props = feature.get("properties")
    if isinstance(props, dict):
        for key in FEATURE_ID_KEYS:
            if key == "_wfid":
                continue
            value = props.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return str(value)
    return None


# G14 层共享的身份工具（R1-P3-5 分层：feature_edit 不依赖 feature_proposals）。
from app.services.feature_edit.identity import (  # noqa: F401,E402
    natural_to_wfid_index,
    normalize_op_wfids,
)


def resolve_ids_on_payload(
    payload: Any,
    ids: List[str],
    dataset_fingerprint: str,
) -> Tuple[Dict[str, str], Any]:
    """自然 id / WFID → 稳定 wfid（对未物化形态按 index 映射物化预演身份）。

    G24 语义：物化后 `_wfid` 置 FEATURE_ID_KEYS 链首，自然键不再寻址。
    未物化数据在**原始形态**上解析自然 id（WFID 键不存在），再经
    materialize_wfids 的确定性顺序映射到 wfid（同 index 同 wfid —— 这就是
    「冻结 base 上确定性物化生成只读 candidate 映射」的语义）。已物化数据
    用自然键 + WFID 双锚定索引。返回 ({requested: wfid}, materialized_fc)。
    """

    fc = extract_payload_fc(payload)
    if fc is None:
        return {}, None
    mat, needed = materialize_preview(fc, dataset_fingerprint)
    if not needed:
        index = natural_to_wfid_index(mat)
        return {str(r): index[str(r)] for r in ids if str(r) in index}, mat
    id_to_index: Dict[str, int] = {}
    for index, feature in enumerate(fc.get("features") or []):
        if not isinstance(feature, dict):
            continue
        rid = _natural_id_of(feature)
        if rid is not None and rid not in id_to_index:
            id_to_index[rid] = index
    mat_feats = mat.get("features") or []
    out: Dict[str, str] = {}
    for rid in ids:
        key = str(rid)
        idx = id_to_index.get(key)
        if idx is not None and idx < len(mat_feats):
            props = mat_feats[idx].get("properties")
            wfid = props.get("_wfid") if isinstance(props, dict) else None
            if isinstance(wfid, str):
                out[key] = wfid
                continue
        # WFID 直接寻址（调用方可能已传 wfid 形态）。
        for feature in mat_feats:
            props = feature.get("properties")
            if isinstance(props, dict) and props.get("_wfid") == key:
                out[key] = key
                break
    return out, mat


def resolve_candidate_targets(
    payload: Any,
    ops: List[Dict[str, Any]],
    *,
    dataset_fingerprint: str,
    requested_ids: Optional[List[str]] = None,
) -> Tuple[List[str], List[Dict[str, Any]], Any]:
    """解析提案目标（显式 ids + op 级 wfids + split wfid 并集）→ 稳定 wfid。

    返回 (ordered_wfids, missing_findings, materialized_fc)。
    """
    ids: List[str] = list(requested_ids or [])
    for op in ops:
        if not isinstance(op, dict):
            continue
        if isinstance(op.get("wfid"), str):
            ids.append(op["wfid"])
        raw = op.get("wfids")
        if isinstance(raw, list):
            ids.extend(str(w) for w in raw if isinstance(w, str))
    ids = list(dict.fromkeys(i for i in ids if i))[:400]
    wfid_map, mat_fc = resolve_ids_on_payload(payload, ids, dataset_fingerprint)
    missing = [i for i in ids if i not in wfid_map]
    findings: List[Dict[str, Any]] = []
    if missing:
        findings.append(
            {"code": "feature_not_found", "message": "ids not resolvable", "detail": {"ids": missing[:8]}}
        )
    ordered = list(dict.fromkeys(wfid_map[i] for i in ids if i in wfid_map))
    return ordered, findings, mat_fc


def materialize_preview(fc: Any, dataset_fingerprint: str) -> Tuple[Any, bool]:
    """未物化形态的确定性物化预演（同 G14 build_plan 路径）。

    返回 (fc_after, was_materialized_needed)。
    """
    if fc is None:
        return None, False
    if has_wfids(fc):
        return fc, False
    materialized, _assigned = materialize_wfids(fc, dataset_fingerprint)
    return materialized, True


def evaluate_candidate(
    fc: Any,
    ops: List[Dict[str, Any]],
    ordered_targets: List[str],
    *,
    op_seed: str,
    target_payloads: Optional[Dict[str, Any]] = None,
    geometry_type: Optional[str] = None,
) -> Tuple[List[FeatureDelta], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """对给定（物化后）fc 重放提案 ops。

    返回 (deltas, findings, lineage_events) —— deltas 含 birth/retire/
    字段/几何 before-after 全量证据（G14 同构）。findings 非空 = 候选在
    该数据形态上不可求值（校验失败）。op 级 wfids 在求值前归一化到
    wfid 空间（自然 id → 稳定身份）。
    """
    normalized = normalize_op_wfids(ops, fc)
    deltas, _schema_ops, findings, _summaries, lineage = derive_changes(
        fc, normalized, ordered_targets, target_payloads or {},
        plan_seed=op_seed, geometry_type=geometry_type,
    )
    return deltas, findings, lineage


def candidate_digest(
    ops: List[Dict[str, Any]],
    resolutions: List[Dict[str, Any]],
    *,
    base_fingerprint: str,
    base_revision: int,
    op_seed: str,
) -> str:
    """候选内容摘要 —— 批准绑定与失效判定的输入。

    覆盖 ops + 裁决 + base 证据 + op_seed：任何一项变化（内容/裁决/基础/
    出生身份种子）都产生新 digest，旧批准失效（不存在“点过一次批准、后续
    Agent 换内容仍可提交”的通道）。
    """
    import hashlib
    import json

    from app.lib.data.fingerprints import canonical_dumps

    try:
        payload = json.loads(canonical_dumps({
            "ops": ops,
            "resolutions": resolutions or [],
            "base_fingerprint": base_fingerprint,
            "base_revision": base_revision,
            "op_seed": op_seed,
        }))
    except (TypeError, ValueError):
        payload = {"ops": str(ops)[:2048], "unserializable": True}
    canonical = canonical_dumps(payload)
    return "fpd-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def delta_field_writes(delta: FeatureDelta) -> Dict[str, Dict[str, Any]]:
    """delta 的字段写集投影 {field: {before, after, before_absent}}。"""
    return dict(delta.get("fields") or {})


def geometry_digest(geometry: Optional[Dict[str, Any]]) -> Optional[str]:
    """几何的确定性摘要（canonical JSON sha256 前 24 位；None = 无几何）。"""
    if geometry is None:
        return None
    import hashlib

    from app.lib.data.fingerprints import canonical_dumps

    try:
        return hashlib.sha256(canonical_dumps(geometry).encode("utf-8")).hexdigest()[:24]
    except (TypeError, ValueError):
        return "unserializable"


def fc_index_by_wfid(fc: Any) -> Dict[str, Dict[str, Any]]:
    """物化 fc → {wfid: feature}（重复 wfid 以 first 出现为准并记录歧义另查）。"""
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(fc, dict):
        return out
    for feature in fc.get("features") or []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        wfid = props.get("_wfid") if isinstance(props, dict) else None
        if isinstance(wfid, str):
            out.setdefault(wfid, feature)
    return out


def fc_duplicate_wfids(fc: Any) -> Dict[str, int]:
    """重复 _wfid 计数（身份歧义防线）。"""
    counts: Dict[str, int] = {}
    if not isinstance(fc, dict):
        return counts
    for feature in fc.get("features") or []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        wfid = props.get("_wfid") if isinstance(props, dict) else None
        if isinstance(wfid, str):
            counts[wfid] = counts.get(wfid, 0) + 1
    return {w: c for w, c in counts.items() if c > 1}


def extract_payload_fc(payload: Any) -> Any:
    """载荷 → FC（G14 同源归一）。"""
    return extract_fc(payload)
