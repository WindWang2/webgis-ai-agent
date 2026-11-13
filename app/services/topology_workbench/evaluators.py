"""Topology Workbench —— 规则求值器（WP05/WP06）。

纯函数：``PreparedSource``（已在统一**关系 CRS**）× 规则 → ``EvalFinding``
列表 + 逐规则结果（维度分离）。五类语义（DEDUP_MATRIX #4–#8）：

1. ``polygons_no_overlap`` —— 同源面两两内部重叠（面积 > 阈值；每连通分量
   一条 finding；嵌套包含 = 交叠全覆盖，被发现而非漏报，T04）；
2. ``area_coverage`` —— AOI − ∪coverage − ∪exclusions 的连通缺口分量
   （面积 > 阈值，T01/T02/T03）；
3. ``objects_covered_by_target`` —— checked 对象被 target 并集覆盖
   （covered/within 边界政策；越界分量按几何族用面积/长度/距离阈值，T06）；
4. ``cross_layer_no_overlap`` —— 两个面集合间的重叠分量（面积 > 阈值）；
5. ``line_endpoints_connected`` —— 线端点到连接对象的距离（≤ tolerance_m
   通过；出口区豁免；超阈值 finding，witness=端点→最近候选连线，T10/T11）。

**CRS 语义（关键，T07/T08）**：

- **关系运算**（交/差/并）在**关系 CRS**（源声明 CRS 的统一空间，通常
  EPSG:4326）进行 —— 同一物理边界的坐标逐点一致，差集不会产生投影弦化
  伪缺口；
- **度量**（面积/长度/距离）把结果分量变换到**度量 CRS**
  （recommend_metric_crs 的 UTM/UPS，平面米制）后计算 —— 地理坐标上的
  米制容差绝不做赤道固定换算；
- witness 几何存储为 WGS84（地图展示帧），度量方法与两 CRS 都披露。

预算纪律（WP06）：候选对 / finding 数 / wall-clock 触顶 → computation=partial
+ 计数披露（绝不把截断当 pass）；批间经 ``should_cancel`` 查询取消。
空间索引（STRtree）缓冲只是候选搜索技术，不是业务容差（任务书 §六）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from shapely.geometry import LineString, Point
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union
from shapely.strtree import STRtree

from app.services.topology_workbench.contracts import (
    MAX_CANDIDATE_PAIRS_PER_RULE,
    MAX_FINDINGS_PER_RULE,
    RULE_WALL_CLOCK_S,
)
from app.services.topology_workbench.geometry_core import PreparedSource

logger = logging.getLogger(__name__)

SquareM = "m2"
Meter = "m"


# ─── 度量空间（关系 CRS ⇄ 度量 CRS ⇄ WGS84 展示） ──────────────────────


class MeasureSpace:
    """关系 CRS 内做拓扑、度量 CRS 内做测量的统一入口。

    - ``rel_crs``：关系运算空间（所有 PreparedSource 已在其中）；
    - ``measure_crs``：米制度量空间（UTM/UPS）；
    - ``to_measure(geom)`` / ``to_display(geom)``：分量级变换（小几何，开销可接受）。
    变换器惰性构建；任一变换失败 → ValueError（调用方按 unknown 处理，绝不静默）。
    """

    def __init__(self, rel_crs: str, measure_crs: str):
        self.rel_crs = rel_crs
        self.measure_crs = measure_crs
        self._rel2m: Optional[Any] = None
        self._m2rel: Optional[Any] = None
        self._rel2w: Optional[Any] = None
        self._rel_is_wgs84 = str(rel_crs).upper() in ("EPSG:4326", "WGS84")

    def _t_rel2m(self):
        if self._rel2m is None:
            from pyproj import Transformer

            self._rel2m = Transformer.from_crs(
                self.rel_crs, self.measure_crs, always_xy=True
            ).transform
        return self._rel2m

    def _t_rel2w(self):
        if self._rel2w is None:
            from pyproj import Transformer

            self._rel2w = Transformer.from_crs(
                self.rel_crs, "EPSG:4326", always_xy=True
            ).transform
        return self._rel2w

    def to_measure(self, geom: Any) -> Any:
        if self.rel_crs == self.measure_crs:
            return geom
        return shp_transform(self._t_rel2m(), geom)

    def to_display(self, geom: Any) -> Any:
        """witness 存储/展示几何（WGS84）。"""
        if self._rel_is_wgs84:
            return geom
        return shp_transform(self._t_rel2w(), geom)

    def area_m2(self, geom: Any) -> float:
        return float(self.to_measure(geom).area)

    def length_m(self, geom: Any) -> float:
        return float(self.to_measure(geom).length)

    def distance_m(self, g1: Any, g2: Any) -> float:
        """两点/点线距离：在度量空间计算（小几何，逐次变换可接受）。"""
        if self.rel_crs == self.measure_crs:
            return float(g1.distance(g2))
        return float(self.to_measure(g1).distance(self.to_measure(g2)))

    def crs_disclosure(self) -> Dict[str, str]:
        return {
            "relation_crs": self.rel_crs,
            "measurement_crs": self.measure_crs,
            "witness_crs": "EPSG:4326",
        }


@dataclass
class EvalFinding:
    """求值器产出的单条问题（落库前形状；witness 已是 WGS84）。"""

    rule_id: str
    rule_type: str
    severity: str
    message: str
    witness_geom: Any                     # shapely 几何（WGS84 展示帧）
    witness_digest: str                   # witness 分量 canonical digest
    metric: Dict[str, Any]                # {value, threshold, unit, comparison, method}
    locators: List[Dict[str, Any]]        # 相关对象 locator（含 identity）
    geometry_type: str = "Polygon"

    def key(self, ruleset_digest: str) -> str:
        """稳定 finding key：规则集 digest + 规则 id + 排序 locator 身份 + witness digest。"""
        locator_ids = sorted(
            f"{loc['role']}:{loc['identity']['kind']}:{loc['identity']['value']}"
            for loc in  self.locators
        )
        material = json.dumps(
            [ruleset_digest, self.rule_id, locator_ids, self.witness_digest],
            ensure_ascii=False, separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:48]


@dataclass
class RuleEvalOutput:
    """单规则的求值结果（运行状态与质量结论分离）。"""

    rule_id: str
    rule_type: str
    applicability: str = "applicable"         # applicable | blocked
    blocked_code: str = ""
    blocked_message: str = ""
    input_completeness: str = "full"          # full | partial
    computation: str = "full"                 # full | partial
    outcome: str = ""                         # violations_found | no_violations | unknown
    findings: List[EvalFinding] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    duration_ms: int = 0

    def blocked(self, code: str, message: str) -> "RuleEvalOutput":
        self.applicability = "blocked"
        self.blocked_code = code
        self.blocked_message = message[:300]
        self.outcome = "unknown"
        return self


class _Budget:
    """单规则预算追踪（候选对 / finding / wall-clock）。"""

    def __init__(self, deadline_s: float = RULE_WALL_CLOCK_S):
        self.started = time.monotonic()
        self.deadline_s = deadline_s
        self.candidate_pairs = 0
        self.findings = 0
        self.truncated_findings = False
        self.truncated_pairs = False
        self.timed_out = False

    def tick_time(self) -> bool:
        if time.monotonic() - self.started > self.deadline_s:
            self.timed_out = True
            return False
        return True

    def count_pair(self) -> bool:
        self.candidate_pairs += 1
        if self.candidate_pairs > MAX_CANDIDATE_PAIRS_PER_RULE:
            self.truncated_pairs = True
            return False
        return True

    def room_for_finding(self) -> bool:
        if self.findings >= MAX_FINDINGS_PER_RULE:
            self.truncated_findings = True
            return False
        self.findings += 1
        return True

    def exhausted(self) -> bool:
        return self.truncated_findings or self.truncated_pairs or self.timed_out


def _digest_geom(geom: Any, precision: int = 7) -> str:
    """几何分量 canonical digest（round 后 GeoJSON —— 稳定且与坐标序无关）。"""
    from app.services.topology_workbench.geometry_core import geometry_to_geojson

    gj = geometry_to_geojson(geom, precision)
    canonical = json.dumps(gj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _locator(source: PreparedSource, idx: int) -> Dict[str, Any]:
    p = source.features[idx]
    return {
        "role": source.role,
        "kind": source.source_kind,
        "ref": source.ref,
        "content_revision": source.content_revision,
        "fingerprint": source.fingerprint,
        "identity": p.identity,
        "feature_index": idx,
    }


def _excluded_note(source: PreparedSource) -> Optional[str]:
    total = source.excluded_total()
    if total <= 0:
        return None
    reasons = ", ".join(f"{k}={v}" for k, v in sorted(source.excluded.items()))
    return (
        f"角色 {source.role}：{total}/{source.total_input} 个要素未进入关系运算"
        f"（{reasons}）—— 相关区域的规则结论为 unknown 范围"
    )


def _wkb_type(geom: Any) -> str:
    return getattr(geom, "geom_type", "Geometry")


def _iter_pieces(geom: Any) -> List[Tuple[Any, str]]:
    """几何 → 不连通分量列表 [(geom, digest)]（同对要素多片不互相覆盖，任务书 §六）。"""
    flat: List[Any] = []

    def _flatten(g: Any) -> None:
        gtype = _wkb_type(g)
        if gtype in ("MultiPolygon", "MultiLineString", "MultiPoint", "GeometryCollection"):
            for sub in g.geoms:
                _flatten(sub)
        else:
            flat.append(g)

    _flatten(geom)
    out: List[Tuple[Any, str]] = []
    seen: set = set()
    for g in flat:
        if g.is_empty:
            continue
        d = _digest_geom(g)
        if d not in seen:
            seen.add(d)
            out.append((g, d))
    return out


def _finalize(out: RuleEvalOutput, budget: _Budget, should_cancel: Callable[[], bool]) -> None:
    out.counts["candidate_pairs"] = budget.candidate_pairs
    out.counts["findings_emitted"] = len(out.findings)
    out.duration_ms = int((time.monotonic() - budget.started) * 1000)
    if budget.exhausted():
        out.computation = "partial"
        if budget.truncated_findings:
            out.notes.append(
                f"finding 数触顶 {MAX_FINDINGS_PER_RULE} —— 计数为部分结果，"
                "可能存在未发现的问题"
            )
        if budget.truncated_pairs:
            out.notes.append(
                f"候选对触顶 {MAX_CANDIDATE_PAIRS_PER_RULE} —— 未检查的候选对"
                "不计为通过"
            )
        if budget.timed_out:
            out.notes.append(f"规则 wall-clock 超过 {budget.deadline_s:.0f}s")
    if should_cancel():
        out.outcome = "unknown"
        out.notes.append("规则被取消 —— 结果不完整")
        return
    # 结论判定：发现即违规；无发现且计算完整 → 通过；无发现但计算 partial 或
    # 求值器显式标注 unknown（AOI 无有效几何等）→ unknown（绝不假绿）。
    if out.findings:
        out.outcome = "violations_found"
    elif out.outcome == "unknown" or budget.exhausted():
        out.outcome = "unknown"
    else:
        out.outcome = "no_violations"


# ─── 1. polygons_no_overlap ─────────────────────────────────────────────


def eval_polygons_no_overlap(
    *, rule: Dict[str, Any], checked: PreparedSource,
    measure: MeasureSpace, ruleset_digest: str, budget: _Budget,
    should_cancel: Callable[[], bool],
) -> RuleEvalOutput:
    out = RuleEvalOutput(rule_id=rule["rule_id"], rule_type=rule["rule_type"])
    params = rule.get("params") or {}
    threshold = float(params.get("overlap_area_threshold_m2", 0.0))
    boundary_touch = bool(params.get("boundary_touch", False))

    valid = [p for p in checked.features if p.geometry is not None]
    out.counts["examined"] = len(valid)
    out.counts["input_total"] = checked.total_input
    if checked.excluded_total():
        out.input_completeness = "partial"
        note = _excluded_note(checked)
        if note:
            out.notes.append(note)

    polys = [p.geometry for p in valid]
    if len(polys) < 2:
        out.outcome = "no_violations"
        out.counts.setdefault("candidate_pairs", 0)
        out.counts.setdefault("findings_emitted", 0)
        return out

    tree = STRtree(polys)
    for i, gi in enumerate(polys):
        if not budget.tick_time() or should_cancel():
            break
        for j in tree.query(gi):
            jj = int(j)
            if jj <= i:
                continue
            gj = polys[jj]
            if not budget.count_pair():
                break
            inter = gi.intersection(gj)
            if inter.is_empty:
                continue
            itype = _wkb_type(inter)
            if itype in ("LineString", "MultiLineString", "Point", "MultiPoint"):
                # 共边/共点：零面积重叠。仅当规则显式声明 boundary_touch
                # 违规时才进入分量判定（T05：默认不把贴边当面积重叠）。
                if not boundary_touch:
                    continue
            elif itype not in ("Polygon", "MultiPolygon", "GeometryCollection"):
                continue
            for part in _iter_pieces(inter):
                is_area = part[0].geom_type in ("Polygon", "MultiPolygon")
                if not is_area and not boundary_touch:
                    continue  # 零面积接触默认宽容（面积阈值只管面积分量）
                if is_area:
                    # 阈值先行：低于阈值的碎屑不消耗 finding 预算（与
                    # area_coverage/cross_layer 分支同序 —— 否则清洁数据
                    # 会被碎屑风暴虚假打partial）
                    area = measure.area_m2(part[0])
                    if area <= threshold:
                        continue
                if not budget.room_for_finding():
                    break
                if is_area:
                    msg = (
                        f"面重叠：{valid[i].identity['value']} × {valid[jj].identity['value']}"
                        f"（分量面积 {area:.2f} m² > 阈值 {threshold:.2f} m²）"
                    )
                    metric = {
                        "value": round(area, 3), "threshold": threshold,
                        "unit": SquareM, "comparison": ">",
                        "method": "intersection_component_area",
                    }
                else:
                    # boundary_touch=true：共边/共点按声明确认为违规 ——
                    # 度量 = 接触长度（点接触为 0.0），无阈值（政策型发现）
                    contact_len = measure.length_m(part[0])
                    msg = (
                        f"边界接触（政策违规）：{valid[i].identity['value']} × "
                        f"{valid[jj].identity['value']}"
                        f"（接触长度 {contact_len:.2f} m，规则声明 boundary_touch 违规）"
                    )
                    metric = {
                        "value": round(contact_len, 3), "threshold": None,
                        "unit": Meter, "comparison": "declared",
                        "method": "boundary_contact_length_declared",
                        "policy": "boundary_touch",
                    }
                out.findings.append(EvalFinding(
                    rule_id=rule["rule_id"], rule_type=rule["rule_type"],
                    severity=rule["severity"], message=msg[:500],
                    witness_geom=measure.to_display(part[0]),
                    witness_digest=part[1],
                    metric={**metric, **measure.crs_disclosure()},
                    locators=[_locator(checked, valid[i].index), _locator(checked, valid[jj].index)],
                    geometry_type=_wkb_type(part[0]),
                ))
    _finalize(out, budget, should_cancel)
    return out


# ─── 2. area_coverage ───────────────────────────────────────────────────


def eval_area_coverage(
    *, rule: Dict[str, Any], coverage: PreparedSource,
    aoi: PreparedSource, exclusions: Optional[PreparedSource] = None,
    measure: MeasureSpace = None, ruleset_digest: str = "", budget: _Budget = None,
    should_cancel: Callable[[], bool],
) -> RuleEvalOutput:
    out = RuleEvalOutput(rule_id=rule["rule_id"], rule_type=rule["rule_type"])
    params = rule.get("params") or {}
    threshold = float(params.get("gap_area_threshold_m2", 0.0))

    for src in (coverage, aoi, exclusions):
        if src is None:
            continue
        out.counts[f"{src.role}_examined"] = src.examined
        out.counts[f"{src.role}_input"] = src.total_input
        if src.excluded_total():
            out.input_completeness = "partial"
            note = _excluded_note(src)
            if note:
                out.notes.append(note)

    cov_geoms = [p.geometry for p in coverage.features if p.geometry is not None]
    aoi_geoms = [p.geometry for p in aoi.features if p.geometry is not None]
    if not aoi_geoms:
        return out.blocked("aoi_empty", "AOI 角色没有有效几何 —— 无法计算覆盖")

    if not budget.tick_time():
        _finalize(out, budget, should_cancel)
        return out
    cov_union = unary_union(cov_geoms) if cov_geoms else None
    aoi_union = unary_union(aoi_geoms)

    gap = aoi_union
    if cov_union is not None and not cov_union.is_empty:
        gap = gap.difference(cov_union)
    excl_area_m2 = 0.0
    if exclusions is not None:
        excl_geoms = [p.geometry for p in exclusions.features if p.geometry is not None]
        if excl_geoms:
            excl_union = unary_union(excl_geoms)
            excl_area_m2 = measure.area_m2(excl_union)
            gap = gap.difference(excl_union)
    out.counts["exclusion_area_m2"] = round(excl_area_m2, 3)

    if gap.is_empty:
        out.outcome = "no_violations"
        out.counts.setdefault("candidate_pairs", 0)
        out.counts.setdefault("findings_emitted", 0)
        return out

    if gap.geom_type not in ("Polygon", "MultiPolygon", "GeometryCollection"):
        return out.blocked("gap_geometry", f"差集几何类型异常：{gap.geom_type}")

    for part, digest in _iter_pieces(gap):
        if part.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        area = measure.area_m2(part)
        if area <= threshold:
            continue
        if not budget.room_for_finding():
            break
        # 缺口关联 locator：触及缺口的 AOI/coverage 要素（定位两份源）
        locators = [_locator(aoi, p.index) for p in aoi.features
                    if p.geometry is not None and p.geometry.intersects(part)][:4]
        locators += [_locator(coverage, p.index) for p in coverage.features
                     if p.geometry is not None and p.geometry.intersects(part)][:4]
        msg = (
            f"覆盖缺口：AOI − ∪coverage − ∪exclusions 的分量面积 {area:.2f} m²"
            f" > 阈值 {threshold:.2f} m²（扣除排除区 {excl_area_m2:.2f} m²）"
        )
        out.findings.append(EvalFinding(
            rule_id=rule["rule_id"], rule_type=rule["rule_type"],
            severity=rule["severity"], message=msg[:500],
            witness_geom=measure.to_display(part), witness_digest=digest,
            metric={
                "value": round(area, 3), "threshold": threshold,
                "unit": SquareM, "comparison": ">",
                "method": "aoi_minus_coverage_minus_exclusions",
                **measure.crs_disclosure(),
            },
            locators=locators, geometry_type=part.geom_type,
        ))
    _finalize(out, budget, should_cancel)
    return out


# ─── 3. objects_covered_by_target ───────────────────────────────────────


def eval_objects_covered_by_target(
    *, rule: Dict[str, Any], checked: PreparedSource, target: PreparedSource,
    measure: MeasureSpace, ruleset_digest: str, budget: _Budget,
    should_cancel: Callable[[], bool],
) -> RuleEvalOutput:
    out = RuleEvalOutput(rule_id=rule["rule_id"], rule_type=rule["rule_type"])
    params = rule.get("params") or {}
    policy = str(params.get("boundary_policy", "covered"))
    area_th = float(params.get("outside_area_threshold_m2", 0.0))
    length_th = float(params.get("outside_length_threshold_m", 0.0))
    dist_th = float(params.get("outside_distance_threshold_m", 0.0))

    out.counts["examined"] = checked.examined
    out.counts["input_total"] = checked.total_input
    out.counts["target_examined"] = target.examined
    for src in (checked, target):
        if src.excluded_total():
            out.input_completeness = "partial"
            note = _excluded_note(src)
            if note:
                out.notes.append(note)

    tgt_geoms = [p.geometry for p in target.features if p.geometry is not None]
    if not tgt_geoms:
        return out.blocked("target_empty", "target 角色没有有效面几何 —— 无法判定包含")
    if not budget.tick_time():
        _finalize(out, budget, should_cancel)
        return out
    tgt_union = unary_union(tgt_geoms)
    tgt_tree = STRtree(tgt_geoms)

    for p in checked.features:
        if p.geometry is None:
            continue
        if not budget.tick_time() or should_cancel():
            break
        g = p.geometry
        # covered 语义：贴边合法；within 语义：贴边（触及目标边界）即违规。
        outside = g.difference(tgt_union)
        if (policy == "within" and outside.is_empty
                and g.intersects(tgt_union.boundary) and not g.within(tgt_union)):
            # 严格内部政策下贴边违规：witness 取接触边界段
            outside = g.intersection(tgt_union.boundary)
        if outside is None or outside.is_empty:
            continue
        family = ("area" if g.geom_type in ("Polygon", "MultiPolygon")
                  else "length" if g.geom_type in ("LineString", "MultiLineString")
                  else "distance")
        for part, digest in _iter_pieces(outside):
            if not budget.room_for_finding():
                break
            if family == "area":
                value, unit, th = measure.area_m2(part), SquareM, area_th
                if value <= th:
                    continue
            elif family == "length":
                value, unit, th = measure.length_m(part), Meter, length_th
                if value <= th:
                    continue
            else:
                value, unit, th = measure.distance_m(part, tgt_union), Meter, dist_th
                if value <= th:
                    continue
            # 相关 target 要素（含被越界部分触及的）
            touched = [int(k) for k in tgt_tree.query(part)][:4]
            locators = [_locator(checked, p.index)]
            locators += [_locator(target, target.features[t].index) for t in touched
                         if target.features[t].geometry is not None][:4]
            msg = (
                f"对象越界：{p.identity['value']} 未被 target 覆盖"
                f"（政策 {policy}；越界分量 {value:.2f} {unit} > 阈值 {th:.2f}）"
            )
            out.findings.append(EvalFinding(
                rule_id=rule["rule_id"], rule_type=rule["rule_type"],
                severity=rule["severity"], message=msg[:500],
                witness_geom=measure.to_display(part), witness_digest=digest,
                metric={
                    "value": round(value, 3), "threshold": th,
                    "unit": unit, "comparison": ">",
                    "method": f"difference_component_{family}_{policy}",
                    "geometry_family": family,
                    **measure.crs_disclosure(),
                },
                locators=locators, geometry_type=_wkb_type(part),
            ))
    _finalize(out, budget, should_cancel)
    return out


# ─── 4. cross_layer_no_overlap ──────────────────────────────────────────


def eval_cross_layer_no_overlap(
    *, rule: Dict[str, Any], source_a: PreparedSource, source_b: PreparedSource,
    measure: MeasureSpace, ruleset_digest: str, budget: _Budget,
    should_cancel: Callable[[], bool],
) -> RuleEvalOutput:
    out = RuleEvalOutput(rule_id=rule["rule_id"], rule_type=rule["rule_type"])
    threshold = float((rule.get("params") or {}).get("overlap_area_threshold_m2", 0.0))

    for src in (source_a, source_b):
        out.counts[f"{src.role}_examined"] = src.examined
        out.counts[f"{src.role}_input"] = src.total_input
        if src.excluded_total():
            out.input_completeness = "partial"
            note = _excluded_note(src)
            if note:
                out.notes.append(note)

    a_items = [p for p in source_a.features if p.geometry is not None]
    b_items = [p for p in source_b.features if p.geometry is not None]
    if not a_items or not b_items:
        out.outcome = "no_violations"
        out.notes.append("一侧集合为空 —— 无跨层重叠可判定（空集通过披露）")
        out.counts.setdefault("candidate_pairs", 0)
        out.counts.setdefault("findings_emitted", 0)
        return out

    b_tree = STRtree([p.geometry for p in b_items])
    for pa in a_items:
        if not budget.tick_time() or should_cancel():
            break
        for kb in b_tree.query(pa.geometry):
            pb = b_items[int(kb)]
            if not budget.count_pair():
                break
            inter = pa.geometry.intersection(pb.geometry)
            if inter.is_empty:
                continue
            if _wkb_type(inter) in ("LineString", "MultiLineString", "Point", "MultiPoint"):
                continue  # 贴边/触点 = 零面积重叠（与同源规则同语义）
            for part, digest in _iter_pieces(inter):
                if part.geom_type not in ("Polygon", "MultiPolygon"):
                    continue
                area = measure.area_m2(part)
                if area <= threshold:
                    continue
                if not budget.room_for_finding():
                    break
                msg = (
                    f"跨层重叠：{pa.identity['value']}（{source_a.role}）× "
                    f"{pb.identity['value']}（{source_b.role}）"
                    f"（分量面积 {area:.2f} m² > 阈值 {threshold:.2f} m²）"
                )
                out.findings.append(EvalFinding(
                    rule_id=rule["rule_id"], rule_type=rule["rule_type"],
                    severity=rule["severity"], message=msg[:500],
                    witness_geom=measure.to_display(part), witness_digest=digest,
                    metric={
                        "value": round(area, 3), "threshold": threshold,
                        "unit": SquareM, "comparison": ">",
                        "method": "cross_intersection_component_area",
                        **measure.crs_disclosure(),
                    },
                    locators=[_locator(source_a, pa.index), _locator(source_b, pb.index)],
                    geometry_type=part.geom_type,
                ))
    _finalize(out, budget, should_cancel)
    return out


# ─── 5. line_endpoints_connected ────────────────────────────────────────


def _endpoints(geom: Any) -> List[Tuple[float, float]]:
    if geom.geom_type == "LineString":
        return [tuple(geom.coords[0][:2]), tuple(geom.coords[-1][:2])]  # type: ignore[misc]
    if geom.geom_type == "MultiLineString":
        first, last = geom.geoms[0], geom.geoms[-1]
        return [tuple(first.coords[0][:2]), tuple(last.coords[-1][:2])]  # type: ignore[misc]
    return []


def eval_line_endpoints_connected(
    *, rule: Dict[str, Any], checked: PreparedSource,
    connectors: PreparedSource = None,
    exit_zones: Optional[PreparedSource] = None,
    measure: MeasureSpace = None, ruleset_digest: str = "", budget: _Budget = None,
    should_cancel: Callable[[], bool],
) -> RuleEvalOutput:
    out = RuleEvalOutput(rule_id=rule["rule_id"], rule_type=rule["rule_type"])
    params = rule.get("params") or {}
    tolerance = float(params["tolerance_m"])
    connector_kind = str(params.get("connector_kind", "points"))
    allow_line_line = bool(params.get("allow_line_line", False))

    out.counts["examined"] = checked.examined
    out.counts["input_total"] = checked.total_input
    out.counts["connectors_examined"] = connectors.examined
    for src in (checked, connectors, exit_zones):
        if src is None:
            continue
        if src.excluded_total():
            out.input_completeness = "partial"
            note = _excluded_note(src)
            if note:
                out.notes.append(note)

    conn_items = [p for p in connectors.features if p.geometry is not None]
    point_conns = [p for p in conn_items if p.geometry.geom_type in ("Point", "MultiPoint")]
    line_conns = [p for p in conn_items if p.geometry.geom_type in ("LineString", "MultiLineString")]
    if connector_kind == "points":
        line_conns = []
    elif connector_kind == "lines":
        point_conns = []
    if not point_conns and not line_conns:
        return out.blocked(
            "connectors_empty", "connectors 角色没有可用连接对象 —— 无法判定端点连接"
        )

    pt_geoms = [p.geometry for p in point_conns]
    pt_tree = STRtree(pt_geoms) if pt_geoms else None
    ln_geoms = [p.geometry for p in line_conns]
    ln_tree = STRtree(ln_geoms) if ln_geoms else None
    exit_union = None
    if exit_zones is not None:
        exit_geoms = [p.geometry for p in exit_zones.features if p.geometry is not None]
        if exit_geoms:
            exit_union = unary_union(exit_geoms)
    out.counts["exit_zone_area_m2"] = round(measure.area_m2(exit_union), 3) if exit_union else 0.0

    # 候选索引缓冲：容差的保守扩张（候选搜索技术，非业务容差 —— 业务判定
    # 一律用 measure.distance_m 精确距离）。
    def _index_radius() -> float:
        base = tolerance
        if measure._rel_is_wgs84:
            # 度空间索引：把米制容差换成该纬度的保守度半径（取联合范围纬度）
            lat = _mean_latitude(checked)
            deg = max(abs(base) / 111_320.0 / max(abs(math.cos(lat)), 0.01), 1e-9)
            return deg * 1.5
        return base * 1.5

    idx_radius = _index_radius()
    # 无候选时的距离基准：连接对象并集**预先**变换到度量空间一次
    # （逐端点变换 10k 点并集是 O(n) 陷阱 —— 资源曲线测试钉住）。
    any_conn_union = unary_union(pt_geoms + ln_geoms)
    any_conn_union_m = measure.to_measure(any_conn_union)

    for p in checked.features:
        if p.geometry is None:
            continue
        if not budget.tick_time() or should_cancel():
            break
        ends = _endpoints(p.geometry)
        for end_label, end in zip(("起点", "终点"), ends):
            end_pt = Point(end[0], end[1])
            best: Optional[Tuple[float, Any, Any]] = None  # (distance_m, geom, item)
            if pt_tree is not None:
                for ki in pt_tree.query(end_pt.buffer(idx_radius)):
                    if not budget.count_pair():
                        break
                    item = point_conns[int(ki)]
                    d = measure.distance_m(end_pt, item.geometry)
                    if best is None or d < best[0]:
                        best = (d, item.geometry, item)
            if ln_tree is not None:
                for ki in ln_tree.query(end_pt.buffer(idx_radius)):
                    if not budget.count_pair():
                        break
                    item = line_conns[int(ki)]
                    d = measure.distance_m(end_pt, item.geometry)
                    if best is None or d < best[0]:
                        best = (d, item.geometry, item)
            if best is not None and best[0] <= tolerance:
                continue  # 已连接
            # 未连接 → 合法出口判定（出口区覆盖端点）
            if exit_union is not None and exit_union.covers(end_pt):
                out.counts["exempted_exits"] = out.counts.get("exempted_exits", 0) + 1
                continue
            # 线线豁免：显式允许且最近候选是线
            if allow_line_line and best is not None \
                    and _wkb_type(best[1]) in ("LineString", "MultiLineString"):
                continue
            if not budget.room_for_finding():
                break
            if best is not None:
                dist, nearest_geom, nearest_item = best
            else:
                dist = float(measure.to_measure(end_pt).distance(any_conn_union_m))
                nearest_geom, nearest_item = any_conn_union, None
            # witness：端点 → 最近点（关系 CRS 内构造，展示帧转换）
            from shapely.ops import nearest_points

            try:
                _p1, near_pt = nearest_points(end_pt, nearest_geom)
                witness = LineString([(end[0], end[1]), (near_pt.x, near_pt.y)])
                wtype = "LineString"
            except Exception:  # noqa: BLE001 — 最近点退化（几何奇异）→ 端点本身
                witness = end_pt
                wtype = "Point"
            msg = (
                f"端点未连接：{p.identity['value']} 的{end_label}距最近连接对象 "
                f"{dist:.2f} m > 容差 {tolerance:.2f} m"
            )
            locators = [_locator(checked, p.index)]
            if nearest_item is not None:
                locators.append(_locator(connectors, nearest_item.index))
            out.findings.append(EvalFinding(
                rule_id=rule["rule_id"], rule_type=rule["rule_type"],
                severity=rule["severity"], message=msg[:500],
                witness_geom=measure.to_display(witness),
                witness_digest=_digest_geom(witness),
                metric={
                    "value": round(dist, 3), "threshold": tolerance,
                    "unit": Meter, "comparison": ">",
                    "method": "endpoint_distance_in_measurement_crs",
                    "endpoint": [round(end[0], 6), round(end[1], 6)],
                    "endpoint_label": end_label,
                    **measure.crs_disclosure(),
                },
                locators=locators, geometry_type=wtype,
            ))
    _finalize(out, budget, should_cancel)
    return out


def _mean_latitude(source: PreparedSource) -> float:
    lats = [p.geometry.bounds[1] for p in source.features if p.geometry is not None]
    return sum(lats) / len(lats) if lats else 0.0


#: 求值器注册表（与 RULE_TYPES 漂移即缺陷 —— 测试钉住）
EVALUATORS = {
    "polygons_no_overlap": eval_polygons_no_overlap,
    "area_coverage": eval_area_coverage,
    "objects_covered_by_target": eval_objects_covered_by_target,
    "cross_layer_no_overlap": eval_cross_layer_no_overlap,
    "line_endpoints_connected": eval_line_endpoints_connected,
}

#: 每类规则的角色 → 期望几何族（prepare_source 输入）
RULE_EXPECTED_FAMILY = {
    "polygons_no_overlap": {"checked": "polygon"},
    "area_coverage": {"coverage": "polygon", "aoi": "polygon", "exclusions": "polygon"},
    "objects_covered_by_target": {"checked": "any", "target": "polygon"},
    "cross_layer_no_overlap": {"source_a": "polygon", "source_b": "polygon"},
    "line_endpoints_connected": {"checked": "line", "connectors": "any", "exit_zones": "polygon"},
}


__all__ = [
    "EvalFinding", "RuleEvalOutput", "EVALUATORS", "RULE_EXPECTED_FAMILY",
    "MeasureSpace",
]
