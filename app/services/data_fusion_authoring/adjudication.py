"""确定性裁决 + 融合特征组装（纯函数核心，零 I/O）。

语义红线（reviewer 重点）：
- Agent/系统仅可应用计划声明的确定性规则或已授权人工决定；写入永远
  可追溯到 ``rule`` / ``manual``，绝不静默择一；
- ``unresolved`` 不是失败也不是零值：字段输出缺失 + 进审阅队列；
- 来源保真：每个输出字段保留两侧原始值于 provenance（``values``），
  冲突从未消失，只是被显式裁决；
- 几何绝不自动 dissolve：采纳侧由政策/人工决定，另一侧进 provenance；
- 组装幂等：同输入（记录+决定）同输出（稳定序、稳定 pair_key）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_fusion_authoring import contracts
from app.services.data_fusion_authoring.matching import (
    EntityRecord,
    PairCandidate,
)
from app.services.data_fusion_authoring.plan import FusionPlan


# ───────────────────────── pair 稳定键 ─────────────────────────

def entity_fingerprint(
    side: str, rec: EntityRecord, plan: FusionPlan, source_ref: str
) -> str:
    """实体内容指纹（增量刷新时人工锁定重挂靠的锚点）。

    键成分：源锚 + 侧 + 外部标识（若有）或归一化名称 + 几何坐标。
    同内容必同键；内容变 → 新键（旧决定不误挂）。
    """
    parts: List[str] = [source_ref, side]
    if rec.ext_id_present:
        parts.append(f"id:{rec.ext_id}")
    else:
        parts.append(f"name:{rec.name_norm}")
    if rec.geom is not None:
        try:
            parts.append(f"geom:{rec.geom.wkb_hex()}")
        except Exception:  # noqa: BLE001 — 几何不可序列化时降级到质心
            c = rec.geom.centroid
            parts.append(f"pt:{c.x:.9f},{c.y:.9f}")
    blob = "\x1f".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def pair_stable_key(
    rec_a: EntityRecord, rec_b: EntityRecord,
    plan: FusionPlan, ref_a: str, ref_b: str,
) -> str:
    """配对稳定键（两端实体指纹的有序拼接 —— 与快照 index 无关）。"""
    fa = entity_fingerprint("a", rec_a, plan, ref_a)
    fb = entity_fingerprint("b", rec_b, plan, ref_b)
    return f"fp:{fa}:{fb}"


# ───────────────────────── 字段裁决 ─────────────────────────

@dataclass
class FieldAdjudication:
    output_field: str
    outcome: str                    # contracts.ADJUDICATION_OUTCOMES
    value: Any                      # resolved 时为采纳值；unresolved = None
    rule: str                       # 声明规则（或 manual）
    comparison: str                 # contracts.FIELD_COMPARISONS
    side: Optional[str]             # 采纳侧（identical/单侧时也如实记录）
    values: Dict[str, Any]          # {"a": ..., "b": ...} 原始值保真

    def to_dict(self) -> Dict[str, Any]:
        return {
            "output_field": self.output_field,
            "outcome": self.outcome,
            "value": self.value,
            "rule": self.rule,
            "comparison": self.comparison,
            "side": self.side,
            "values": self.values,
        }


def _newest_side(
    rec_a: EntityRecord, rec_b: EntityRecord, plan: FusionPlan
) -> Optional[str]:
    ta, tb = rec_a.temporal, rec_b.temporal
    if ta is None or tb is None:
        return None  # 时间缺失 → 不能裁决（unresolved，绝不回退优先序）
    if ta == tb:
        return "a" if plan.source_a.priority < plan.source_b.priority else "b"
    return "a" if ta > tb else "b"


def adjudicate_fields(
    rec_a: EntityRecord, rec_b: EntityRecord,
    conflicts: Dict[str, str], plan: FusionPlan,
    manual_values: Optional[Dict[str, Any]] = None,
) -> List[FieldAdjudication]:
    """配对内逐输出字段裁决（规则确定性；人工值优先）。

    ``manual_values``：审阅决定给出的显式值（outcome=resolved_manual）。
    规则语义：
    - same_value：comparison=identical → 取同值；否则 unresolved；
    - trust_priority：identical → 同值；conflict → 高优先源
      （missing 一侧时取另一侧在场值，outcome 仍 resolved_rule ——
      「一侧缺失」不是冲突，取在场值即唯一确定）；
    - newest_temporal：identical → 同值；conflict → 最新侧；
      时间任一缺失 → unresolved（绝不静默回退 trust_priority）；
    - keep_multi：两侧在场 → 有序去重列表；单侧 → 单值列表；
    - 单侧映射字段：在场值直取（唯一来源，唯一确定）。
    """
    manual_values = manual_values or {}
    out: List[FieldAdjudication] = []
    for field_name, m in _union_fields(plan).items():
        comparison = conflicts.get(field_name, "missing_both")
        rule = m["rule"]
        va = rec_a.row.get(m["a"]) if m.get("a") else None
        vb = rec_b.row.get(m["b"]) if m.get("b") else None
        values = {"a": va if m.get("a") else None,
                  "b": vb if m.get("b") else None}
        if field_name in manual_values:
            out.append(FieldAdjudication(
                output_field=field_name, outcome="resolved_manual",
                value=manual_values[field_name], rule="manual",
                comparison=comparison, side=None, values=values))
            continue

        def _resolved(value: Any, side: Optional[str]) -> FieldAdjudication:
            return FieldAdjudication(
                output_field=field_name, outcome="resolved_rule",
                value=value, rule=rule, comparison=comparison,
                side=side, values=values)

        def _unresolved() -> FieldAdjudication:
            return FieldAdjudication(
                output_field=field_name, outcome="unresolved",
                value=None, rule=rule, comparison=comparison,
                side=None, values=values)

        # 单侧映射：唯一来源 → 唯一确定；源值为空 = 该字段在唯一来源
        # 缺失（missing 语义，输出缺失但不制造审阅项 —— 与 contracts
        # FIELD_COMPARISONS 的 missing_* 文档一致，独立审查 P3）
        if m.get("a") is None or m.get("b") is None:
            present = va if m.get("a") else vb
            side = "a" if m.get("a") else "b"
            if present is None or present == "":
                # 标签与 pair.field_conflicts 同口径：仅 a 映射而 a 空 =
                # b 侧无该字段 → missing_b（反向 missing_a）
                out.append(FieldAdjudication(
                    output_field=field_name, outcome="resolved_rule",
                    value=None, rule=rule,
                    comparison="missing_b" if m.get("a") else "missing_a",
                    side=None, values=values))
            else:
                out.append(_resolved(present, side))
            continue

        if comparison == "identical":
            out.append(_resolved(va, None))
        elif comparison == "missing_both":
            out.append(_unresolved())
        elif comparison in ("missing_a", "missing_b"):
            present, side = (vb, "b") if comparison == "missing_a" else (va, "a")
            out.append(_resolved(present, side))
        else:  # conflict —— 规则语义分派
            if rule == "same_value":
                out.append(_unresolved())  # 声明同值却冲突 → 需人审
            elif rule == "trust_priority":
                side = "a" if plan.source_a.priority < plan.source_b.priority \
                    else "b"
                out.append(_resolved(values[side], side))
            elif rule == "newest_temporal":
                side = _newest_side(rec_a, rec_b, plan)
                if side is None:
                    out.append(_unresolved())
                else:
                    out.append(_resolved(values[side], side))
            elif rule == "keep_multi":
                multi = [v for v in (va, vb) if v is not None and v != ""]
                # 稳定去重（保留首次出现序；不排序 —— 来源序即声明序）
                dedup: List[Any] = []
                seen_keys: set = set()
                for v in multi:
                    key = json.dumps(v, sort_keys=True, ensure_ascii=False,
                                     default=str)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        dedup.append(v)
                out.append(FieldAdjudication(
                    output_field=field_name, outcome="resolved_rule",
                    value=dedup, rule=rule, comparison=comparison,
                    side=None, values=values))
            else:  # manual-only 字段未给人工值 → unresolved
                out.append(_unresolved())
    return out


def _union_fields(plan: FusionPlan) -> Dict[str, Dict[str, Any]]:
    fields: Dict[str, Dict[str, Any]] = {}
    for side, src in (("a", plan.source_a), ("b", plan.source_b)):
        for fm in src.field_mappings:
            entry = fields.setdefault(fm.output_field, {
                "a": None, "b": None, "rule": fm.rule})
            entry[side] = fm.source_field
            entry["rule"] = fm.rule
    return fields


def unresolved_fields(adjs: List[FieldAdjudication]) -> List[str]:
    return [a.output_field for a in adjs if a.outcome == "unresolved"]


# ───────────────────────── 几何裁决 ─────────────────────────

def adjudicate_geometry(
    rec_a: EntityRecord, rec_b: EntityRecord, plan: FusionPlan,
    manual_side: Optional[str] = None,
) -> Tuple[Any, str, Optional[str]]:
    """输出几何采纳（绝不自动 dissolve）。返回 (geom, outcome, side)。

    - manual：人工侧缺失/非法 → unresolved（保持 A 侧原样输出但标记
      unresolved —— 绝不编造合并几何）；
    - newest_temporal：两侧时间齐备 → 最新侧；缺失 → unresolved；
    - primary_source：高优先源几何（缺几何时回落另一侧并如实记录 side）。
    未配对单源实体几何原样保留。
    """
    adoption = plan.geometry.adoption
    if manual_side is not None:
        side = manual_side if manual_side in ("a", "b") else None
        if side is None:
            return rec_a.geom, "unresolved", None
        geom = rec_a.geom if side == "a" else rec_b.geom
        if geom is None:
            return rec_a.geom, "unresolved", None
        return geom, "resolved_manual", side
    if adoption == "newest_temporal":
        side = _newest_side(rec_a, rec_b, plan)
        if side is None:
            return rec_a.geom, "unresolved", None
        geom = rec_a.geom if side == "a" else rec_b.geom
        if geom is None:
            other = "b" if side == "a" else "a"
            geom2 = rec_b.geom if side == "a" else rec_a.geom
            return (geom2, ("resolved_rule" if geom2 is not None
                            else "unresolved"), other if geom2 is not None
                    else None)
        return geom, "resolved_rule", side
    # primary_source
    side = "a" if plan.source_a.priority < plan.source_b.priority else "b"
    geom = rec_a.geom if side == "a" else rec_b.geom
    if geom is None:
        other = "b" if side == "a" else "a"
        geom2 = rec_b.geom if side == "a" else rec_a.geom
        if geom2 is None:
            return None, "unresolved", None
        return geom2, "resolved_rule", other
    return geom, "resolved_rule", side


# ───────────────────────── 特征组装 ─────────────────────────

def _source_entry(side: str, rec: EntityRecord, plan: FusionPlan,
                  ref: str) -> Dict[str, Any]:
    src = plan.source_a if side == "a" else plan.source_b
    return {
        "side": side,
        "ref": ref,
        "row_index": rec.index,
        "label": src.label,
        "priority": src.priority,
        "license": src.license,
        "attribution": src.attribution,
    }


def merged_feature(
    rec_a: EntityRecord, rec_b: EntityRecord, pair: PairCandidate,
    plan: FusionPlan, run_id: str, ref_a: str, ref_b: str,
    decisions: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[FieldAdjudication]]:
    """配对 → 融合特征（含完整 provenance；幂等稳定）。"""
    decisions = decisions or {}
    adjs = adjudicate_fields(
        rec_a, rec_b, pair.field_conflicts, plan,
        manual_values=decisions.get("field_values"))
    geom, geom_outcome, geom_side = adjudicate_geometry(
        rec_a, rec_b, plan, manual_side=decisions.get("geometry_side"))
    props: Dict[str, Any] = {
        a.output_field: a.value for a in adjs
    }
    props[contracts.PROVENANCE_KEY] = {
        "origin": "merged",
        "run_id": run_id,
        "pair_key": pair_stable_key(rec_a, rec_b, plan, ref_a, ref_b),
        "match": {
            "status": pair.status,
            "score": pair.score,
            "method_version": contracts.METHOD_VERSION,
            "hard_block": pair.hard_block,
            "evidence": [e.to_dict() for e in pair.evidence],
            "renormalized": pair.renormalized,
        },
        "sources": [
            _source_entry("a", rec_a, plan, ref_a),
            _source_entry("b", rec_b, plan, ref_b),
        ],
        "fields": {a.output_field: a.to_dict() for a in adjs},
        "geometry": {
            "adoption": plan.geometry.adoption,
            "outcome": geom_outcome,
            "side": geom_side,
            "evidence": dict(pair.geometry_evidence),
        },
    }
    feature = {
        "type": "Feature",
        "geometry": _geom_to_json(geom),
        "properties": props,
    }
    return feature, adjs


def single_feature(
    side: str, rec: EntityRecord, plan: FusionPlan, run_id: str, ref: str,
    *, withheld: bool = False, partner_key: Optional[str] = None,
    partner_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """未配对/未决单源特征（真实实体保留；未决审阅如实披露）。

    ``partner_keys``：同一实体卷入多个未决对时列出全部争抢键
    （审查 P3 —— 只发首个 pair 的两侧会漏披露其他待决实体）。
    """
    src = plan.source_a if side == "a" else plan.source_b
    props: Dict[str, Any] = {}
    for fm in src.field_mappings:
        props[fm.output_field] = rec.row.get(fm.source_field)
    origin = "withheld_review" if withheld else (
        "single_a" if side == "a" else "single_b")
    prov = {
        "origin": origin,
        "run_id": run_id,
        "sources": [_source_entry(side, rec, plan, ref)],
        "partner_key": partner_key or (
            (partner_keys or [None])[0] if partner_keys else None),
    }
    if partner_keys:
        prov["partner_keys"] = list(partner_keys)
    props[contracts.PROVENANCE_KEY] = prov
    return {
        "type": "Feature",
        "geometry": _geom_to_json(rec.geom),
        "properties": props,
    }


def _geom_to_json(geom: Any) -> Optional[Dict[str, Any]]:
    if geom is None:
        return None
    import shapely
    return json.loads(shapely.to_geojson(geom))
