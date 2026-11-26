"""WP2 · 任务级空间-时间覆盖矩阵（Coverage Matrix）。

GOAL-20261126-02：对「任务需求 × 候选数据」做分层覆盖评估，四层分离
（任务书红线）：

1. 声明范围（来源自报 bbox/temporal）；
2. 元数据相交（bbox/时间窗相交粗筛——**只可作粗筛，不得宣称全覆盖**）；
3. 实际可核验覆盖（footprint 多边形交叠比，metric CRS 手算可复现；
   栅格 nodata 折减有效覆盖）；
4. 要素完备性（**面积比不得推断 POI/人口记录完备度**——除非来源方
   显式提供完备性证据，否则恒 unknown）。

科学适用性（消费已有资格语义，不新建资格状态）：
- ``role_class`` 沿用 ``data_fabric.service_qualification`` 的封闭词表
  （display_only / numeric_raster / vector_features / unknown）；展示级
  来源（WMS/tiles）不得进入 numeric_stats/rate/density 资格；
- 率（rate）需求无分母角色 → 科学前置缺口（衔接 preflight 分母判据）。

几何与投影：
- 交叠比在本地 UTM（WGS84）metric CRS 计算——度空间面积比无科学意义；
- 跨日期线：bbox/几何按任务框平移到 ±180 连续帧后统一求交（shift 规则
  见 ``_shift_frame``），披露计算方法；
- 多部件/边界空洞由 shapely 多边形布尔运算原生处理。

诚实边界：未知 ≠ 不存在；许可/署名缺失只披露不伪造授权。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.dataset_semantics.task_scope import _normalize_bbox
from app.services.task_data_supply.requirement import TaskDataRequirement

# ── 封闭词表 ─────────────────────────────────────────────────────────────

COVER_FULL = "full"                    # 实际可核验全覆盖
COVER_PARTIAL = "partial"              # 实际核验：部分覆盖（含 nodata 折减）
COVER_DECLARED_ONLY = "declared_only"  # 仅声明/元数据相交，未核验
COVER_NONE = "none"                    # 实际核验：不相交
COVER_UNKNOWN = "unknown"              # 无范围证据，无法评估

_VERDICT_RANK = {
    COVER_FULL: 0, COVER_PARTIAL: 1, COVER_DECLARED_ONLY: 2,
    COVER_NONE: 3, COVER_UNKNOWN: 4,
}

#: role_class 词表（沿用 data_fabric.service_qualification，不复制不新增）。
_ROLE_CLASSES = ("display_only", "numeric_raster", "vector_features",
                 "unknown")

_NUMERIC_METHODS = ("numeric_stats", "rate", "density")

_MAX_CANDIDATES_PER_ROLE = 24
_MAX_EVALUATIONS_OUT = 7

_FULL_RATIO = 0.999


# ── 候选模型 ─────────────────────────────────────────────────────────────

@dataclass
class CoverageCandidate:
    """一个候选数据源的证据包（目录条目/已治理服务/会话资产）。"""
    candidate_id: str                    # 可验证 dataset_key / ref
    channel: str                         # ⊆ 获取通道词表（声明层复用）
    crs: str = "EPSG:4326"
    serves_roles: Tuple[str, ...] = ()   # 该候选可服务的角色（目录 logical_role/调用方证据）
    declared_bbox: Optional[List[float]] = None
    footprint_geometry: Optional[Dict[str, Any]] = None  # 实际覆盖足迹（GeoJSON）
    temporal_coverage: Optional[List[str]] = None
    version: str = ""
    feature_count: Optional[int] = None
    fields: Tuple[str, ...] = ()
    geometry_kind: str = "unknown"
    resolution_meters: Optional[float] = None
    role_class: str = "unknown"          # ⊆ _ROLE_CLASSES（service_qualification）
    license: str = ""
    attribution: str = ""
    nodata_fraction: Optional[float] = None  # 栅格质量证据（0..1）
    completeness_evidence: str = ""      # 显式完备性证据；空 = unknown
    cost_hint: Optional[Dict[str, Any]] = None  # ADS 成本提示（rows/bytes/latency_ms）
    source: str = ""                     # 证据出处（catalog entry / registry id）

    def __post_init__(self) -> None:
        self.candidate_id = str(self.candidate_id or "").strip()[:128]
        if not self.candidate_id:
            raise ValueError("COVERAGE_CANDIDATE_ID_REQUIRED")
        self.channel = str(self.channel or "")[:32]
        self.crs = str(self.crs or "EPSG:4326")[:24]
        self.serves_roles = tuple(str(r)[:32] for r in self.serves_roles
                                  if str(r).strip())[:8]
        if self.declared_bbox is not None:
            norm = _normalize_bbox(self.declared_bbox)
            if norm is not None:
                # 既有归一接受普通 bbox；跨线形状（minx>maxx）归一为 None
                # ——这里以跨线约定保留（与 requirement.TaskScope 同口径）。
                self.declared_bbox = norm
            else:
                raw = [float(b) for b in self.declared_bbox]
                if (len(raw) == 4 and all(math.isfinite(b) for b in raw)
                        and raw[0] > raw[2] and raw[1] <= raw[3]):
                    self.declared_bbox = raw  # 跨日期线 bbox
                else:
                    raise ValueError(
                        "COVERAGE_CANDIDATE_BBOX_INVALID: "
                        f"{self.declared_bbox!r}")
        if self.footprint_geometry is not None:
            _check_geometry_bounded(self.footprint_geometry)
        if self.temporal_coverage is not None:
            tc = [str(t)[:10] for t in self.temporal_coverage]
            if len(tc) != 2:
                raise ValueError(
                    "COVERAGE_CANDIDATE_TEMPORAL_INVALID: 需 [start, end]")
            self.temporal_coverage = tc
        self.version = str(self.version or "")[:64]
        if self.feature_count is not None:
            self.feature_count = int(self.feature_count)
        self.fields = tuple(str(f)[:64] for f in self.fields if str(f))[:32]
        self.geometry_kind = str(self.geometry_kind or "unknown")[:16]
        if self.resolution_meters is not None:
            self.resolution_meters = float(self.resolution_meters)
            if not math.isfinite(self.resolution_meters) or (
                    self.resolution_meters < 0):
                raise ValueError("COVERAGE_CANDIDATE_RESOLUTION_INVALID")
        self.role_class = str(self.role_class or "unknown")[:24]
        if self.role_class not in _ROLE_CLASSES:
            raise ValueError(
                f"COVERAGE_ROLE_CLASS_UNKNOWN: {self.role_class!r} ∉ "
                f"{','.join(_ROLE_CLASSES)}（沿用 service_qualification 词表）")
        self.license = str(self.license or "")[:128]
        self.attribution = str(self.attribution or "")[:128]
        if self.nodata_fraction is not None:
            self.nodata_fraction = float(self.nodata_fraction)
            if not (0.0 <= self.nodata_fraction <= 1.0
                    and math.isfinite(self.nodata_fraction)):
                raise ValueError(
                    "COVERAGE_NODATA_FRACTION_INVALID: 需为 0..1")
        self.completeness_evidence = str(self.completeness_evidence or "")[:300]
        if self.cost_hint is not None and not isinstance(self.cost_hint, dict):
            raise ValueError("COVERAGE_COST_HINT_INVALID: 需为 dict")
        self.source = str(self.source or "")[:128]


def _check_geometry_bounded(geometry: Dict[str, Any]) -> None:
    """footprint 只接受有界概化几何（顶点 ≤ 2000），防全量数据走私。"""
    from app.services.task_data_supply.requirement import (
        _MAX_GEOMETRY_VERTICES,
    )

    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    count = 0

    def walk(node: Any) -> None:
        nonlocal count
        if isinstance(node, (list, tuple)):
            if (len(node) >= 2 and isinstance(node[0], (int, float))
                    and isinstance(node[1], (int, float))):
                count += 1
                return
            for child in node:
                walk(child)

    walk(coords)
    if count > _MAX_GEOMETRY_VERTICES:
        raise ValueError(
            f"COVERAGE_FOOTPRINT_TOO_LARGE: {count} > "
            f"{_MAX_GEOMETRY_VERTICES}（footprint 需概化，不接受全量数据）")


# ── 评估结果 ─────────────────────────────────────────────────────────────

@dataclass
class CoverageEvaluation:
    """一条「需求 × 候选」覆盖评估行（有界、可解释）。"""
    role: str
    candidate_id: str
    verdict: str = COVER_UNKNOWN
    spatial_ratio: Optional[float] = None    # 实际核验时 0..1；否则 None
    temporal_verdict: str = "unknown"        # ok / partial / unknown / none
    gaps: List[Dict[str, str]] = field(default_factory=list)
    disclosures: List[str] = field(default_factory=list)
    completeness: str = "unknown"            # unknown / declared / verified
    fit_for_methods: Dict[str, bool] = field(default_factory=dict)
    numeric_stats_qualified: bool = False
    license_disclosed: bool = False

    @property
    def rank(self) -> int:
        return _VERDICT_RANK.get(self.verdict, 9)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role[:32],
            "candidate_id": self.candidate_id[:96],
            "verdict": self.verdict,
            "spatial_ratio": (round(self.spatial_ratio, 4)
                              if self.spatial_ratio is not None else None),
            "temporal_verdict": self.temporal_verdict,
            "gaps": [
                {k: str(v)[:160] for k, v in g.items()}
                for g in self.gaps[:8]
            ],
            "disclosures": [d[:200] for d in self.disclosures[:4]],
            "completeness": self.completeness,
            "fit_for_methods": dict(list(self.fit_for_methods.items())[:8]),
            "numeric_stats_qualified": self.numeric_stats_qualified,
            "license_disclosed": self.license_disclosed,
        }


@dataclass
class RoleCoverage:
    """一个角色的覆盖行集（按适用性排序；非价格序）。"""
    role: str
    evaluations: List[CoverageEvaluation] = field(default_factory=list)
    unknown_reasons: List[str] = field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role[:32],
            "evaluations": [
                e.to_bounded_dict()
                for e in self.evaluations[:_MAX_EVALUATIONS_OUT]
            ],
            "unknown_reasons": [u[:200] for u in self.unknown_reasons[:4]],
        }


@dataclass
class CoverageMatrix:
    """整任务覆盖矩阵（角色 × 候选）。"""
    roles: List[RoleCoverage] = field(default_factory=list)
    method_notes: List[str] = field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "roles": [r.to_bounded_dict() for r in self.roles],
            "method_notes": [m[:240] for m in self.method_notes[:6]],
        }


# ── 几何工具（metric CRS + 跨日期线） ────────────────────────────────────

def _shift_frame_lon(x: float, lo: float, hi: float) -> float:
    """把经度 x 平移 k*360 使其落入 [lo, hi] 连续帧（跨线任务用）。"""
    for k in (0.0, 360.0, -360.0):
        v = x + k
        if lo - 1e-9 <= v <= hi + 1e-9:
            return v
    return x


def _task_frame(task_poly) -> Tuple[float, float]:
    """任务多边形所在连续帧的经度范围（普通任务 = 自身 bbox）。"""
    minx, miny, maxx, maxy = task_poly.bounds
    if minx > maxx:  # 理论不会（shapely 已连续）；防御
        minx, maxx = maxx, minx
    return minx, maxx


def _metric_transform(lon: float, lat: float):
    """centroid → 本地 UTM Transformer（WGS84）。"""
    from pyproj import CRS, Transformer

    zone = int(((lon + 180.0) % 360.0) // 6.0) + 1
    south = lat < 0
    proj = f"+proj=utm +zone={zone} +{'south' if south else 'north'}" \
           " +datum=WGS84 +units=m +no_defs"
    return Transformer.from_crs(CRS("EPSG:4326"), CRS(proj),
                                always_xy=True)


def _polygon_from_geojson(geometry: Dict[str, Any]):
    from shapely.geometry import shape
    from shapely.geometry.polygon import orient

    # orient(..., 1.0)：外环 CCW / 内环 CW——调用方给的环方向不可信，
    # 不归一会把带洞多边形判成无效（交集为空 → 伪 none）。
    return orient(shape(geometry), 1.0)


def _normalize_antimeridian_coords(coords: Any) -> Any:
    """递归平移跨线几何顶点：max-min>180 时，负经度 +360（或 >180 的
    全体 -360），把 ±180 两侧拆开的顶点并成连续帧。"""
    xs: List[float] = []

    def collect(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if (len(node) >= 2 and isinstance(node[0], (int, float))
                    and isinstance(node[1], (int, float))):
                xs.append(float(node[0]))
                return
            for child in node:
                collect(child)

    collect(coords)
    if not xs or (max(xs) - min(xs)) <= 180.0:
        return coords
    shift = 360.0 if min(xs) < 0 else -360.0

    def shift_node(node: Any) -> Any:
        if isinstance(node, (list, tuple)):
            if (len(node) >= 2 and isinstance(node[0], (int, float))
                    and isinstance(node[1], (int, float))):
                return [float(node[0]) + shift, node[1], *list(node[2:])]
            return [shift_node(c) for c in node]
        return node

    return shift_node(coords)


def _shift_geometry_to_frame(geometry: Dict[str, Any],
                             lo: float, hi: float) -> Optional[Any]:
    """候选几何 → 任务连续帧（逐顶点 ±360 选择落入 [lo,hi] 的表示）。"""
    from shapely.geometry import shape
    from shapely.geometry.polygon import orient

    def shift_vertex(node: Any) -> Any:
        if isinstance(node, (list, tuple)):
            if (len(node) >= 2 and isinstance(node[0], (int, float))
                    and isinstance(node[1], (int, float))):
                x = float(node[0])
                for k in (0.0, 360.0, -360.0):
                    if lo - 1e-9 <= x + k <= hi + 1e-9:
                        return [x + k, node[1], *list(node[2:])]
                return [x, node[1], *list(node[2:])]
            return [shift_vertex(c) for c in node]
        return node

    shifted = {"type": geometry.get("type"),
               "coordinates": shift_vertex(geometry.get("coordinates"))}
    try:
        return orient(shape(shifted), 1.0)
    except Exception:
        return None


def _intersection_ratio(task_poly, cand_poly, cand_geojson,
                        frame: Tuple[float, float]) -> Tuple[float, str]:
    """metric CRS 交叠比 → (ratio, method_note)。

    跨日期线：任务与候选几何先各自归一跨线顶点，候选再按**任务声明帧**
    （跨线 bbox → [minx, minx+360]；普通任务 → 自身 bbox）逐顶点 ±360
    平移，最后投影到本地 UTM 计算面积比——度空间面积比无科学意义。
    帧必须取声明 bbox 而非拆半 union 的 bounds（后者跨线时横跨 ±180，
    会把已归一的候选错误回移）。
    """
    lo, hi = frame
    shifted = _shift_geometry_to_frame(cand_geojson, lo, hi)
    if shifted is not None:
        cand_poly = shifted

    cx = (max(task_poly.bounds[0], cand_poly.bounds[0])
          + min(task_poly.bounds[2], cand_poly.bounds[2])) / 2.0
    cy = (max(task_poly.bounds[1], cand_poly.bounds[1])
          + min(task_poly.bounds[3], cand_poly.bounds[3])) / 2.0
    tf = _metric_transform(cx, cy)

    from shapely.ops import transform as shp_transform

    task_m = shp_transform(lambda x, y, z=None: tf.transform(x, y), task_poly)
    cand_m = shp_transform(lambda x, y, z=None: tf.transform(x, y), cand_poly)
    task_area = task_m.area
    if task_area <= 0:
        return 0.0, "metric-area task polygon degenerate"
    ratio = task_m.intersection(cand_m).area / task_area
    note = ("intersection ratio in local UTM (WGS84) metric CRS; "
            "antimeridian frames unified by per-vertex ±360° shift "
            "when applicable")
    return ratio, note


# ── 时间判定 ─────────────────────────────────────────────────────────────

def _parse_day(s: str):
    from datetime import date, datetime

    s = s.strip()
    try:
        if len(s) == 10:
            return date.fromisoformat(s)
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _temporal_check(task_window: Optional[List[str]],
                    cand_window: Optional[List[str]]) -> Tuple[str, List[Dict[str, str]]]:
    """任务时间窗 vs 候选声明窗 → (verdict, gaps)。"""
    gaps: List[Dict[str, str]] = []
    if task_window is None:
        return "unknown", gaps  # 任务未声明时间 → 不判定（兼容语义）
    if cand_window is None:
        gaps.append({"dimension": "temporal",
                     "detail": "候选未声明时间覆盖——时间口径不可核验"})
        return "unknown", gaps
    ts, te = _parse_day(task_window[0]), _parse_day(task_window[1])
    cs, ce = _parse_day(cand_window[0]), _parse_day(cand_window[1])
    if not (ts and te and cs and ce):
        gaps.append({"dimension": "temporal",
                     "detail": "时间端点解析失败——不虚构可比性"})
        return "unknown", gaps
    if ce < ts or cs > te:
        gaps.append({"dimension": "temporal",
                     "detail": (f"候选时间窗 {cand_window} 与任务窗 "
                                f"{task_window} 不相交（版本/期次不匹配）"),
                     "code": "COVERAGE_TEMPORAL_DISJOINT"})
        return "none", gaps
    if cs <= ts and ce >= te:
        return "ok", gaps
    gaps.append({"dimension": "temporal",
                 "detail": (f"候选时间窗 {cand_window} 未完整覆盖任务窗 "
                            f"{task_window}"),
                 "code": "COVERAGE_TEMPORAL_PARTIAL"})
    return "partial", gaps


# ── 主评估 ───────────────────────────────────────────────────────────────

def evaluate_task_coverage(
    requirement: TaskDataRequirement,
    candidates: List[CoverageCandidate],
) -> CoverageMatrix:
    """需求合同 × 候选证据包 → 覆盖矩阵（确定性、有界、零网络 I/O）。"""
    matrix = CoverageMatrix()
    matrix.method_notes.append(
        "bbox 相交只作粗筛：无实际 footprint 时最高只能给 declared_only，"
        "不得宣称全覆盖")
    matrix.method_notes.append(
        "面积比不推断 POI/人口等记录完备度；完备性仅在来源方显式提供证据时"
        "记为 declared")
    matrix.method_notes.append(
        "未知（no evidence）≠ 不存在（none）；展示级来源不进数值统计资格")

    candidates = list(candidates)[:_MAX_CANDIDATES_PER_ROLE * 8]

    for item in requirement.items:
        role = RoleCoverage(role=item.role)
        serving = [c for c in candidates if item.role in c.serves_roles]
        if not serving:
            role.unknown_reasons.append(
                f"无候选声明服务角色 {item.role}（候选数 "
                f"{len(candidates)}）——未搜索/未登记 ≠ 不存在")
        for cand in serving[:_MAX_CANDIDATES_PER_ROLE]:
            role.evaluations.append(
                _evaluate_one(item, cand, requirement))
        role.evaluations.sort(key=lambda e: e.rank)
        matrix.roles.append(role)
    return matrix


def _evaluate_one(item, cand: CoverageCandidate,
                  requirement: TaskDataRequirement) -> CoverageEvaluation:
    ev = CoverageEvaluation(role=item.role, candidate_id=cand.candidate_id)

    # ── 空间：四层 ──
    task_poly = None
    scope = requirement.task_scope
    if scope.geometry is not None:
        try:
            norm_geom = dict(scope.geometry)
            norm_geom["coordinates"] = _normalize_antimeridian_coords(
                scope.geometry.get("coordinates"))
            task_poly = _polygon_from_geojson(norm_geom)
        except Exception:
            task_poly = None
    elif scope.bbox is not None:
        from shapely.geometry import box

        b = scope.bbox
        if b[0] > b[2]:  # 跨日期线任务框：拆两半合成（经度帧 170..190）
            from shapely.geometry import box as _box
            from shapely.ops import unary_union

            east = _box(b[0], b[1], 180.0, b[3])
            west = _box(-180.0, b[1], b[2], b[3])
            task_poly = unary_union([east, west])
        else:
            task_poly = box(b[0], b[1], b[2], b[3])

    cand_poly = None
    cand_geojson = cand.footprint_geometry
    if cand_geojson is not None:
        try:
            norm_cand = dict(cand_geojson)
            norm_cand["coordinates"] = _normalize_antimeridian_coords(
                cand_geojson.get("coordinates"))
            cand_poly = _polygon_from_geojson(norm_cand)
            cand_geojson = norm_cand
        except Exception:
            cand_poly = None
            ev.disclosures.append("候选 footprint 几何不可解析——按声明面评估")

    if task_poly is None:
        ev.verdict = COVER_UNKNOWN
        ev.gaps.append({"dimension": "spatial",
                        "detail": "任务范围未解析为可计算几何（place 未"
                                  "落图/无 bbox）——空间覆盖不可核验",
                        "code": "COVERAGE_TASK_GEOMETRY_UNRESOLVED"})
    elif cand_poly is None:
        if cand.declared_bbox is None:
            ev.verdict = COVER_UNKNOWN
            ev.gaps.append({"dimension": "spatial",
                            "detail": "候选无 bbox 也无 footprint——范围"
                                      "证据缺失",
                            "code": "COVERAGE_NO_EXTENT_EVIDENCE"})
        else:
            cb = cand.declared_bbox
            if cb[0] > cb[2]:  # 候选声明跨线 bbox → 拆两半
                from shapely.geometry import box as _box
                from shapely.ops import unary_union

                meta_poly = unary_union([
                    _box(cb[0], cb[1], 180.0, cb[3]),
                    _box(-180.0, cb[1], cb[2], cb[3])])
            else:
                from shapely.geometry import box as _box

                meta_poly = _box(cb[0], cb[1], cb[2], cb[3])
            if task_poly.intersection(meta_poly).is_empty:
                ev.verdict = COVER_NONE
                ev.gaps.append({"dimension": "spatial",
                                "detail": "声明 bbox 与任务范围不相交",
                                "code": "COVERAGE_BBOX_DISJOINT"})
            else:
                ev.verdict = COVER_DECLARED_ONLY
                ev.disclosures.append(
                    "仅元数据 bbox 相交（粗筛）——实际覆盖未核验，不得"
                    "当作全覆盖")
    else:
        # 任务声明帧：跨线 bbox → [minx, minx+360]；否则任务自身 bbox
        if (scope.bbox is not None and scope.bbox[0] > scope.bbox[2]):
            frame = (float(scope.bbox[0]), float(scope.bbox[0]) + 360.0)
        else:
            b = task_poly.bounds
            frame = (float(b[0]), float(b[2]))
        ratio, note = _intersection_ratio(task_poly, cand_poly,
                                          cand_geojson, frame)
        method_note = note
        ev.spatial_ratio = ratio
        if ratio <= 0.0:
            ev.verdict = COVER_NONE
            ev.gaps.append({"dimension": "spatial",
                            "detail": "实际 footprint 与任务范围不相交",
                            "code": "COVERAGE_FOOTPRINT_DISJOINT"})
        elif ratio >= _FULL_RATIO:
            ev.verdict = COVER_FULL
        else:
            ev.verdict = COVER_PARTIAL
            ev.gaps.append({
                "dimension": "spatial",
                "detail": (f"实际核验：footprint 覆盖任务范围的 "
                           f"{ratio:.1%}（空洞/裁剪）"),
                "code": "COVERAGE_FOOTPRINT_PARTIAL"})
        # 栅格 nodata 折减有效覆盖
        if cand.nodata_fraction is not None and cand.nodata_fraction > 0:
            effective = ratio * (1.0 - cand.nodata_fraction)
            ev.spatial_ratio = effective
            if ev.verdict == COVER_FULL and effective < _FULL_RATIO:
                ev.verdict = COVER_PARTIAL
            ev.gaps.append({
                "dimension": "quality",
                "detail": (f"nodata 占比 {cand.nodata_fraction:.0%}——"
                           f"有效覆盖折减为 {effective:.1%}"),
                "code": "COVERAGE_NODATA_REDUCTION"})
        if method_note:
            ev.disclosures.append(method_note)

    # ── 时间 ──
    t_verdict, t_gaps = _temporal_check(
        scope.time_range, cand.temporal_coverage)
    ev.temporal_verdict = t_verdict
    ev.gaps.extend(t_gaps)
    if (t_verdict == "none"
            and any(g.get("code") == "COVERAGE_TEMPORAL_DISJOINT"
                    for g in t_gaps)):
        if item.state == "candidate_found" or True:
            # 版本/期次不匹配是覆盖缺口的一种（不自动判数据过期——
            # 需求侧显式声明时间窗才有此判定）
            pass

    # ── 属性 ──
    cand_fields = set(cand.fields)
    for f_ in item.required_fields:
        if f_ not in cand_fields:
            ev.gaps.append({"dimension": "attribute", "field": f_,
                            "detail": f"候选字段缺失: {f_}",
                            "code": "COVERAGE_FIELD_MISSING"})

    # ── 粒度 ──
    if (item.resolution_meters is not None
            and cand.resolution_meters is not None
            and cand.resolution_meters > item.resolution_meters):
        ev.gaps.append({
            "dimension": "granularity",
            "detail": (f"候选分辨率 {cand.resolution_meters:g}m 粗于需求 "
                       f"{item.resolution_meters:g}m"),
            "code": "COVERAGE_RESOLUTION_COARSER"})

    # ── 完备性（诚实层） ──
    if cand.completeness_evidence:
        ev.completeness = "declared"
        ev.disclosures.append(
            f"完备性为来源方声明（未独立核验）：{cand.completeness_evidence}")
    else:
        ev.completeness = "unknown"
        ev.disclosures.append(
            "记录完备性未知：覆盖面积比不构成要素/记录完整度的证据")

    # ── 科学适用性：消费方法 × role_class ──
    for m in item.consumption_methods:
        if m in _NUMERIC_METHODS:
            fit = cand.role_class in ("numeric_raster", "vector_features")
            if cand.role_class == "display_only":
                ev.disclosures.append(
                    "展示级来源（WMS/tiles）不得进入数值统计资格——仅可用于"
                    "地图展示")
            ev.fit_for_methods[m] = fit
        else:
            ev.fit_for_methods[m] = True
    if (cand.role_class == "display_only"
            and "numeric_stats" not in ev.fit_for_methods):
        # 显式记录（而非缺省缺席）：展示级来源对数值统计不可用，
        # 消费方面无需猜测缺键语义
        ev.fit_for_methods["numeric_stats"] = False
    ev.numeric_stats_qualified = all(
        ev.fit_for_methods.get(m, False) for m in item.consumption_methods
        if m in _NUMERIC_METHODS)

    # ── 率/分母科学前置 ──
    if "rate" in item.consumption_methods:
        req_roles = {it.role for it in requirement.items}
        has_denom = ("denominator" in req_roles and any(
            it.role == "denominator"
            for it in requirement.items))
        if not (item.needs_denominator or has_denom):
            ev.gaps.append({
                "dimension": "denominator",
                "detail": "率结论需要分母角色数据（人口/面积/单元总数）——"
                          "需求合同未声明分母",
                "code": "COVERAGE_RATE_NEEDS_DENOMINATOR"})
        elif not has_denom and item.needs_denominator:
            ev.gaps.append({
                "dimension": "denominator",
                "detail": "需求声明 needs_denominator 但合同中无 denominator"
                          " 角色",
                "code": "COVERAGE_RATE_NEEDS_DENOMINATOR"})

    # ── 许可（披露，不伪造授权） ──
    if (item.license_redistribution_required
            and not str(cand.license or "").strip()):
        ev.license_disclosed = True
        ev.disclosures.append(
            "许可未声明：再分发条件未核验——只披露，不伪造授权")
    if item.license_attribution_required and not str(
            cand.attribution or "").strip():
        ev.license_disclosed = True
        ev.disclosures.append("署名要求已声明但候选未提供署名信息")

    return ev


__all__ = [
    "COVER_FULL", "COVER_PARTIAL", "COVER_DECLARED_ONLY",
    "COVER_NONE", "COVER_UNKNOWN",
    "CoverageCandidate", "CoverageEvaluation", "RoleCoverage",
    "CoverageMatrix", "evaluate_task_coverage",
]
