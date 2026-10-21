"""Dataset Semantics Preflight —— 多数据联接的 metadata-only 兼容预检（G01/WP-C）。

多数据任务（主分析图层 + 行政分区 + 分母 + 参照 + 时间对比）在进入
规划/资格前，先回答「这些数据能不能联到一起」—— 判据**只读 descriptor
元数据**（CRS / bbox / 时间覆盖 / 字段单位 / measurement kind），
**绝不扫描数据**（禁止每 turn 大表扫描；深度检查走既有后台任务）。

输出 ``PreflightReport``：
- verdict ∈ compatible（可联）/ gaps（有结构化缺口）/ unknown（证据
  不足以下结论 —— unknown ≠ pass）；
- gaps：稳定机读码 + Agent 可消费的最小澄清（typed alternatives：
  需要什么数据 / 可行的替代路径），供缺口回路（澄清或自动修复选项）
  消费 —— 绝不把错误变成伪制图成果。

判据词表与既有单一事实源对齐：单位冲突复用 descriptor 字段级 unit；
密度vs数量复用 measurement_kind / DENOMINATOR_FIELD_HINTS 纪律。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 预检判据码（稳定契约）。
PREFLIGHT_CHECKS = (
    "crs_compatibility",       # CRS 可比性（同投影或可重投影）
    "spatial_overlap",         # bbox 两两相交
    "temporal_overlap",        # 时间覆盖重叠
    "unit_consistency",        # 同名字段单位一致
    "density_denominator",     # 密度语义需要分母
    "role_coverage",           # 期望角色已绑定
)

#: 缺口码（typed gap；Agent 澄清回路的输入）。
CODE_CRS_MISMATCH = "PREFLIGHT_CRS_MISMATCH"
CODE_CRS_UNKNOWN = "PREFLIGHT_CRS_UNKNOWN"
CODE_SPATIAL_DISJOINT = "PREFLIGHT_SPATIAL_DISJOINT"
CODE_TEMPORAL_DISJOINT = "PREFLIGHT_TEMPORAL_DISJOINT"
CODE_TEMPORAL_PARTIAL = "PREFLIGHT_TEMPORAL_PARTIAL"
CODE_UNIT_CONFLICT = "PREFLIGHT_UNIT_CONFLICT"
CODE_UNIT_UNVERIFIED = "PREFLIGHT_UNIT_UNVERIFIED"
CODE_DENSITY_NEEDS_DENOMINATOR = "PREFLIGHT_DENSITY_NEEDS_DENOMINATOR"
CODE_ROLE_MISSING = "PREFLIGHT_ROLE_MISSING"

#: 预检输入上限（与角色绑定/durable facts 同界）。
MAX_PREFLIGHT_DATASETS = 16
#: 报告 gaps 上限（披露面有界）。
MAX_PREFLIGHT_GAPS = 12

#: 已知等价 CRS 组（不做投影数据库查询；仅显式等价对，如 EPSG:4326 与
#: CRS84 同为经纬度 WGS84 —— 保守起步，未列出的不同 CRS 一律按可重投影
#: 缺口披露而非硬失败）。
_CRS_EQUIV_CLASSES: Tuple[Tuple[str, ...], ...] = (
    ("epsg:4326", "crs84", "wgs84"),
)


@dataclass
class TypedAlternative:
    """Agent 可消费的最小澄清项：做什么 / 需要什么。"""

    action: str                 # provide_denominator / reproject / switch_to_count / …
    needs: str = ""             # 人类可读所需数据/操作（≤160）

    def to_bounded_dict(self) -> Dict[str, str]:
        return {"action": str(self.action)[:48],
                "needs": str(self.needs)[:160]}


@dataclass
class PreflightGap:
    """一个结构化缺口（码 + 涉及数据 + 澄清选项）。"""

    code: str
    detail: str = ""
    refs: List[str] = field(default_factory=list)   # 涉及的输入键（≤4）
    alternatives: List[TypedAlternative] = field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "code": str(self.code)[:64],
            "detail": str(self.detail)[:256],
            "refs": [str(r)[:64] for r in self.refs[:4]],
            "alternatives": [
                a.to_bounded_dict() for a in self.alternatives[:3]],
        }


@dataclass
class PreflightReport:
    """预检结论（compatible/gaps/unknown + 有界证据）。"""

    verdict: str = "unknown"    # ⊆ ("compatible", "gaps", "unknown")
    checks: List[Dict[str, Any]] = field(default_factory=list)
    gaps: List[PreflightGap] = field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "checks": self.checks[: len(PREFLIGHT_CHECKS)],
            "gaps": [g.to_bounded_dict() for g in self.gaps[:MAX_PREFLIGHT_GAPS]],
        }


# ── 判据实现（全部确定性、metadata-only）───────────────────────────────

def _crs_class(crs: str) -> str:
    low = str(crs or "").strip().lower()
    for i, equiv in enumerate(_CRS_EQUIV_CLASSES):
        if low in equiv:
            return f"ll-wgs84-{i}"
    return low or ""


def _bbox_overlap(a: Optional[List[float]], b: Optional[List[float]]) -> Optional[bool]:
    """两 bbox 是否相交（None = 任一 bbox 未知 → 不可判）。"""
    if not a or not b or len(a) != 4 or len(b) != 4:
        return None
    return not (a[0] > b[2] or b[0] > a[2] or a[1] > b[3] or b[1] > a[3])


def _coverage_point(cov: str) -> Optional[Tuple[int, Optional[int]]]:
    """覆盖串 → (年, 月|None)（保守解析；失败 None）。

    月粒度（YYYY-MM…）可比到月；仅年粒度按整年保守展开。open-ended
    （如 "present"）→ None。
    """
    s = str(cov or "").strip()
    if len(s) >= 4 and s[:4].isdigit():
        month: Optional[int] = None
        if len(s) >= 7 and s[4] == "-" and s[5:7].isdigit():
            m = int(s[5:7])
            if 1 <= m <= 12:
                month = m
        return int(s[:4]), month
    return None


def _interval(d: Any) -> Optional[Tuple[int, int, bool]]:
    """descriptor 覆盖 → (起值, 止值, 是否月精度全端点)；无起点 → None。

    值域 = 年×12+月（无月按整年保守展开：起点=1 月，止点=12 月）。
    止点不可解析（open-ended）→ 以起点单点参与且精度降级（不猜端点）。
    """
    t = getattr(d, "temporal", None)
    start_s = str(getattr(t, "coverage_start", "") or "")
    end_s = str(getattr(t, "coverage_end", "") or "")
    ps = _coverage_point(start_s)
    if ps is None:
        return None
    pe = _coverage_point(end_s) if end_s else None
    start_val = ps[0] * 12 + (ps[1] - 1 if ps[1] else 0)
    if pe is None:
        # end 缺席/open-ended：区间未知，止点保守取同起点（不外推）。
        return start_val, start_val, False
    end_val = pe[0] * 12 + (pe[1] - 1 if pe[1] else 11)
    month_precise = ps[1] is not None and pe[1] is not None
    return start_val, end_val, month_precise


def _has_temporal_evidence(descriptor: Any) -> bool:
    """descriptor 是否携带时间语义证据（has_time_field/coverage_start）。"""
    t = getattr(descriptor, "temporal", None)
    return bool(
        getattr(t, "has_time_field", False)
        or getattr(t, "coverage_start", ""))


# 任务关心时间对齐的角色（这些角色在场时，单侧时间证据必须按 unknown 披露）。
_TEMPORAL_ROLES = ("baseline", "target_time", "comparison_time")


def _temporal_overlap(d1: Any, d2: Any) -> str:
    """两 descriptor 时间关系：overlap / disjoint / partial / unknown。

    review P1/P2：end 不可解析不外推端点（防假 disjoint）；仅年粒度或
    端点不全时结果降 partial（同年不同月不得假 pass，review P2-6）。
    """
    i1, i2 = _interval(d1), _interval(d2)
    if i1 is None or i2 is None:
        return "unknown" if (i1 is None and i2 is None) else "partial"
    disjoint = i1[0] > i2[1] or i2[0] > i1[1]
    full_precision = i1[2] and i2[2]
    if disjoint:
        return "disjoint" if full_precision else "partial"
    return "overlap" if full_precision else "partial"


def _unit_facts(descriptor: Any) -> Dict[str, str]:
    """{字段名 → canonical unit}（"" = 未知单位；仅数值字段）。"""
    out: Dict[str, str] = {}
    for f in (getattr(descriptor, "fields", None) or []):
        name = str(getattr(f, "name", "") or "")
        unit = str(getattr(f, "unit", "") or "")
        kind = str(getattr(f, "measurement_kind", "") or "")
        if name and (unit or kind):
            out.setdefault(name, unit)
    return out


def _has_denominator_evidence(descriptor: Any) -> bool:
    """分母证据：字段名命中 DENOMINATOR_FIELD_HINTS（单一事实源词表）。"""
    from app.services.gis_harness.workflow_schema import DENOMINATOR_FIELD_HINTS

    for f in (getattr(descriptor, "fields", None) or []):
        name = str(getattr(f, "name", "") or "").lower()
        if any(h in name for h in DENOMINATOR_FIELD_HINTS):
            return True
    return False


def _has_polygon_geometry(descriptor: Any) -> bool:
    kinds = set(getattr(descriptor, "geometry_types", None) or [])
    return bool(kinds & {"Polygon", "MultiPolygon"})


def preflight_multi_dataset(
    datasets: Dict[str, Any],
    *,
    expected_roles: Sequence[str] = (),
    analysis_kind: str = "",
) -> PreflightReport:
    """多数据联接 metadata-only 预检（确定性纯函数，零数据扫描）。

    ``datasets``：{角色/标签 → GISDatasetDescriptor}（通常来自
    ``role_qualification_evidence`` 的绑定投影；≤16 项）。
    ``expected_roles``：任务声明的必需角色（如 ("subject", "boundary",
    "denominator")）；缺席 → ROLE_MISSING 缺口（typed：需要什么数据）。
    ``analysis_kind``："density" 时启用密度-分母判据（数量字段不是密度
    字段 —— 缺分母不得伪装密度成果）。
    """
    report = PreflightReport()
    # 形状归一：接受 {标签 → descriptor} 或 role_qualification_evidence 的
    # 产物形状 {角色 → {descriptor, expected_descriptor_fingerprint}}。
    items: List[Tuple[str, Any]] = []
    for k, v in list(datasets.items())[:MAX_PREFLIGHT_DATASETS]:
        if isinstance(v, dict) and v.get("descriptor") is not None:
            v = v.get("descriptor")
        if v is not None:
            items.append((str(k)[:64], v))
    if not items:
        report.verdict = "unknown"
        report.checks.append({"check": "role_coverage", "result": "unknown",
                              "detail": "无可预检的数据集"})
        return report

    gaps = report.gaps

    # ── role_coverage：期望角色绑定面（按归一后的非 None 输入判）───────
    bound_keys = {k for k, _ in items}
    missing_roles = [str(r)[:32] for r in expected_roles if r not in bound_keys]
    report.checks.append({
        "check": "role_coverage",
        "result": "pass" if not missing_roles else "fail",
        "missing_roles": missing_roles[:8],
    })
    for role in missing_roles[:4]:
        gaps.append(PreflightGap(
            code=CODE_ROLE_MISSING,
            detail=f"任务必需的 {role} 角色未绑定到任何数据集",
            alternatives=[TypedAlternative(
                action="provide_role_data",
                needs=_role_needs_text(role))],
        ))

    # ── crs_compatibility ────────────────────────────────────────────
    crs_by_key = {k: str(getattr(d, "crs", "") or "") for k, d in items}
    known = {k: c for k, c in crs_by_key.items() if c}
    unknown_crs = [k for k, c in crs_by_key.items() if not c]
    classes = {_crs_class(c): [] for c in known.values()}  # type: ignore[arg-type]
    for k, c in known.items():
        classes[_crs_class(c)].append(k)
    distinct = {cls: keys for cls, keys in classes.items() if keys}
    crs_result = "pass"
    if len(distinct) > 1:
        crs_result = "fail"
        gaps.append(PreflightGap(
            code=CODE_CRS_MISMATCH,
            detail="数据集 CRS 不一致：" + "；".join(
                f"{cls}←{','.join(keys[:3])}" for cls, keys in
                list(distinct.items())[:3]),
            refs=[k for keys in distinct.values() for k in keys[:2]][:4],
            alternatives=[TypedAlternative(
                action="reproject",
                needs="统一投影到分析 CRS（如本地米制投影）后联接")],
        ))
    if unknown_crs:
        crs_result = "unknown" if crs_result == "pass" else crs_result
        gaps.append(PreflightGap(
            code=CODE_CRS_UNKNOWN,
            detail="以下数据集 CRS 未知（不可比，不虚构可联结论）："
                   + ",".join(unknown_crs[:4]),
            refs=unknown_crs[:4],
            alternatives=[TypedAlternative(
                action="clarify_crs",
                needs="提供/探查该数据集的空间参考（.prj/元数据）")],
        ))
    report.checks.append({"check": "crs_compatibility", "result": crs_result})

    # ── spatial_overlap（两两 bbox；O(n²) 但 n≤16 且纯元数据）────────
    spatial_result = "pass"
    spatial_unknown = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            k1, d1 = items[i]
            k2, d2 = items[j]
            ov = _bbox_overlap(
                getattr(d1, "bbox", None), getattr(d2, "bbox", None))
            if ov is None:
                spatial_unknown += 1
            elif ov is False:
                spatial_result = "fail"
                if not any(g.code == CODE_SPATIAL_DISJOINT for g in gaps):
                    gaps.append(PreflightGap(
                        code=CODE_SPATIAL_DISJOINT,
                        detail=f"{k1} 与 {k2} 空间范围不相交（bbox 无重叠）",
                        refs=[k1, k2],
                        alternatives=[
                            TypedAlternative(
                                action="provide_overlapping_data",
                                needs="提供空间范围相交的数据，或调整分析范围"),
                            TypedAlternative(
                                action="clip_to_intersection",
                                needs="如有交集区域数据可裁剪后联接"),
                        ],
                    ))
    if spatial_result == "pass" and spatial_unknown:
        spatial_result = "unknown"
    report.checks.append({
        "check": "spatial_overlap", "result": spatial_result,
        "unverifiable_pairs": spatial_unknown,
    })

    # ── temporal_overlap（只评双方都有时间证据的对；静态数据不误伤）──
    # review 追加修正：无时间语义的静态联接不受时间判据拖累
    # （not_applicable ≠ unknown）；单侧证据只在任务声明时间角色时
    # 按 unknown 披露（证据不足不虚构兼容）。
    temporal_task = any(str(r) in _TEMPORAL_ROLES for r in expected_roles)
    temporal_result = "not_applicable"
    temporal_partial = 0
    temporal_one_sided = 0
    both_sided_evaluated = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            k1, d1 = items[i]
            k2, d2 = items[j]
            e1, e2 = _has_temporal_evidence(d1), _has_temporal_evidence(d2)
            if not (e1 and e2):
                if e1 != e2:
                    temporal_one_sided += 1
                continue
            both_sided_evaluated += 1
            rel = _temporal_overlap(d1, d2)
            if rel == "disjoint":
                temporal_result = "fail"
                if not any(g.code == CODE_TEMPORAL_DISJOINT for g in gaps):
                    gaps.append(PreflightGap(
                        code=CODE_TEMPORAL_DISJOINT,
                        detail=f"{k1} 与 {k2} 时间覆盖不重叠",
                        refs=[k1, k2],
                        alternatives=[TypedAlternative(
                            action="provide_matching_period",
                            needs="提供时间覆盖重叠的同期数据（如 2020 与 "
                                  "2025 各一期）")],
                    ))
            elif rel in ("partial", "unknown"):
                temporal_partial += 1
    if both_sided_evaluated:
        if temporal_result == "not_applicable":
            temporal_result = "pass"
        if temporal_result == "pass" and temporal_partial:
            temporal_result = "unknown"
    elif temporal_one_sided and temporal_task:
        temporal_result = "unknown"
    report.checks.append({
        "check": "temporal_overlap", "result": temporal_result,
        "partial_pairs": temporal_partial,
        "one_sided_pairs": temporal_one_sided,
        "partial_code": CODE_TEMPORAL_PARTIAL,  # 证据不足对（不虚构兼容）
    })

    # ── unit_consistency（同名字段单位）───────────────────────────────
    unit_result = "pass"
    units_by_field: Dict[str, List[Tuple[str, str]]] = {}
    for k, d in items:
        for fname, unit in _unit_facts(d).items():
            units_by_field.setdefault(fname[:64], []).append((k, unit))
    unit_gaps = 0
    for fname, entries in sorted(units_by_field.items()):
        declared = [(k, u) for k, u in entries if u]
        units = {u for _, u in declared}
        if len(declared) >= 2 and len(units) > 1:
            unit_result = "fail"
            if unit_gaps < 2:  # review P3：每码缺口上限（披露面有界）
                unit_gaps += 1
                gaps.append(PreflightGap(
                    code=CODE_UNIT_CONFLICT,
                    detail=f"字段 {fname} 在多个数据集单位冲突："
                           + "；".join(f"{k}={u}" for k, u in declared[:3]),
                    refs=[k for k, _ in declared[:4]],
                    alternatives=[TypedAlternative(
                        action="clarify_units",
                        needs=f"统一 {fname} 的单位或提供换算关系")],
                ))
        elif len(entries) >= 2 and len(declared) < len(entries):
            if unit_result == "pass":
                unit_result = "unknown"
            if not any(g.code == CODE_UNIT_UNVERIFIED and fname in g.detail
                       for g in gaps):
                gaps.append(PreflightGap(
                    code=CODE_UNIT_UNVERIFIED,
                    detail=f"字段 {fname} 在部分数据集单位未知，不可比",
                    refs=[k for k, _ in entries[:4]],
                    alternatives=[TypedAlternative(
                        action="clarify_units",
                        needs=f"提供 {fname} 的单位证据（元数据/字段语义）")],
                ))
    report.checks.append({"check": "unit_consistency", "result": unit_result})

    # ── density_denominator（analysis_kind=density）──────────────────
    if analysis_kind == "density":
        has_denominator = any(_has_denominator_evidence(d) for _, d in items)
        has_polygon = any(_has_polygon_geometry(d) for _, d in items)
        density_result = "pass" if (has_denominator or has_polygon) else "fail"
        report.checks.append({
            "check": "density_denominator", "result": density_result,
            "denominator_field_evidence": has_denominator,
            "polygon_area_denominator": has_polygon,
        })
        if density_result == "fail":
            gaps.append(PreflightGap(
                code=CODE_DENSITY_NEEDS_DENOMINATOR,
                detail="数量字段不是密度字段：当前数据无分母证据"
                       "（人口/面积），不得产出密度成果",
                alternatives=[
                    TypedAlternative(
                        action="provide_denominator",
                        needs="提供分母数据（人口/户数）或含面积的面单元"
                              "（行政区）"),
                    TypedAlternative(
                        action="switch_to_count",
                        needs="改用数量分布/分级制图（不声称密度）"),
                ],
            ))

    # ── verdict 收敛（review P1：unknown ≠ pass 是本模块的契约）────────
    # gaps（有结构化缺口）> unknown（判据证据不足，不下兼容结论）>
    # compatible（全部判据 pass 且无缺口）。
    if gaps:
        report.verdict = "gaps"
    elif any(c.get("result") == "unknown" for c in report.checks):
        report.verdict = "unknown"
    else:
        report.verdict = "compatible"
    return report


def _role_needs_text(role: str) -> str:
    """角色 → 所需数据的人读描述（复用 DATA_ROLES 语义，不另造词表）。"""
    mapping = {
        "boundary": "行政/自然边界数据（面状分区，如区县边界）",
        "denominator": "归一化分母数据（人口/户数/面积单元）",
        "baseline": "基期数据（如 2020 年同期）",
        "target_time": "目标期数据（如 2025 年同期）",
        "comparison_time": "对比期数据",
        "network": "路网/管网数据",
        "elevation": "DEM/高程数据",
        "population": "人口数据",
    }
    return mapping.get(str(role), f"角色 {role} 对应的数据集")


__all__ = [
    "PREFLIGHT_CHECKS",
    "MAX_PREFLIGHT_DATASETS",
    "MAX_PREFLIGHT_GAPS",
    "TypedAlternative",
    "PreflightGap",
    "PreflightReport",
    "preflight_multi_dataset",
]
