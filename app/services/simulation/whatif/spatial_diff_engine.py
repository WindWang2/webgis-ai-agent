"""空间差分引擎（ADR-0193 §D3）——几何差分 + 属性指标差分。

单一职责：给定同一基线的两组图层载荷（``{layer_id: FeatureCollection}``），
输出**确定性**差分结果（同输入同输出）：

- 几何差分（:class:`GeometryDiff`）：要素级配对（properties.id 优先，缺失时
  规范化 WKT 兜底）→ added / removed / modified / unchanged；面积/长度增量
  经等距圆柱局部投影（半径 6378137，lat0 取两侧要素联合均值）折算为米制；
- 服务覆盖差分（``_coverage`` 保留键）：设施点缓冲并集的 gained/lost 面积 +
  人口格网（含 ``population`` 属性）质心落入缓冲的覆盖人口；
- 指标差分（:func:`compute_metric_deltas`）：复用
  ``spatial_decision.models.MetricDeltaV2`` 契约（含 GIS-03 语义：无真实
  基线证据的指标全 None + evidence_gap_note，禁止编造默认值）。内置模型
  ``proxy:v1``，常数与公式全部内联披露；
- 对比图层（:func:`build_diff_overlay`）：变更要素携带 ``diff_kind`` /
  ``impact_sign``（positive/negative/neutral）/ ``render_hint``
  （绿 #22c55e = 正面改善，红 #ef4444 = 负面恶化，灰 #9ca3af = 中性）。

防重复施工：情境事实差分在 ``gis_situation/diff.py``（SituationDelta 词汇），
与本模块的空间几何差分互不调用；单次型评估在 ``spatial_decision/``。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import shape
from shapely.ops import transform as shp_transform, unary_union

from app.services.spatial_decision.models import MetricDeltaV2

logger = logging.getLogger(__name__)

#: proxy:v1 —— 等距圆柱局部投影的地球半径（WGS84 长半轴，米）。
_PROJECTION_RADIUS_M = 6378137.0
#: proxy:v1 —— 道路通行能力代理：每 km 道路长度的当量通行能力（veh/h）。
ROAD_CAPACITY_PER_KM = 1200.0
#: proxy:v1 —— 服务覆盖默认半径（米，五分钟步行圈量级）。
DEFAULT_SERVICE_RADIUS_M = 800.0

#: 保留键：diff_layers 返回 dict 中服务覆盖差分的键（非 layer_id）。
COVERAGE_KEY = "_coverage"

_IMPACT_POSITIVE = "positive"
_IMPACT_NEGATIVE = "negative"
_IMPACT_NEUTRAL = "neutral"
_RENDER_HINTS = {
    _IMPACT_POSITIVE: "#22c55e",
    _IMPACT_NEGATIVE: "#ef4444",
    _IMPACT_NEUTRAL: "#9ca3af",
}

_LAYER_ROLE_KEYWORDS: List[Tuple[Tuple[str, ...], str]] = [
    (("park", "green", "绿地"), "green"),
    (("road", "street", "bridge", "flyover", "highway", "道路"), "road"),
    (
        ("facility", "school", "hospital", "clinic", "subway", "station", "poi", "设施"),
        "facility",
    ),
    (("population", "demand", "人口"), "population"),
]


def resolve_layer_role(layer_id: str, layer: Optional[Dict[str, Any]] = None) -> str:
    """图层角色解析：显式 ``whatif_role`` 优先，否则 layer_id 关键词启发。

    角色 ∈ {green, road, facility, population, other}；仅用于 proxy:v1 的
    指标归属与 impact 着色，任何启发式失败都安全落 ``other``（不参与指标）。
    """
    explicit = ""
    if isinstance(layer, dict):
        explicit = str(layer.get("whatif_role") or "")
    if explicit:
        return explicit
    lid = (layer_id or "").lower()
    for keywords, role in _LAYER_ROLE_KEYWORDS:
        for kw in keywords:
            if kw in lid:
                return role
    return "other"


# ────────────────────────────── 投影与几何度量 ──────────────────────────────


def _collect_latitudes(payloads: Dict[str, Dict[str, Any]]) -> List[float]:
    lats: List[float] = []
    for fc in payloads.values():
        for feat in _features_of(fc):
            geom = feat.get("geometry") if isinstance(feat.get("geometry"), dict) else None
            if not geom:
                continue
            for coord in _iter_coords(geom.get("coordinates")):
                lats.append(float(coord[1]))
    return lats


def _iter_coords(node: Any):
    if isinstance(node, (list, tuple)) and node and isinstance(node[0], (int, float)):
        yield node
    elif isinstance(node, (list, tuple)):
        for child in node:
            yield from _iter_coords(child)


def _make_projection(lat0_deg: float):
    """等距圆柱局部投影：x = R·lon·π/180·cos(lat0)，y = R·lat·π/180。

    签名兼容 ``shapely.ops.transform`` 的两种调用形态：序列快路径
    ``func(xs, ys)`` 与逐坐标回退 ``func(x, y)``。
    """
    k = _PROJECTION_RADIUS_M * math.pi / 180.0
    kx = k * math.cos(math.radians(lat0_deg))
    ky = k

    def _project(x, y):
        return [x * kx, y * ky]

    return _project


def _project_shapes(features: List[Dict[str, Any]], lat0_deg: float) -> List[Any]:
    project = _make_projection(lat0_deg)
    shapes_out = []
    for feat in features:
        geom = feat.get("geometry")
        if not isinstance(geom, dict) or not geom.get("coordinates"):
            continue
        try:
            shp = shape(geom)
        except Exception:  # noqa: BLE001 — 坏几何跳过（诚实缺计，不中断差分）
            continue
        shapes_out.append(shp_transform(project, shp))
    return shapes_out


def _total_area(shapes: List[Any]) -> float:
    return float(sum(s.area for s in shapes if s.geom_type in ("Polygon", "MultiPolygon")))


def _total_length(shapes: List[Any]) -> float:
    return float(sum(s.length for s in shapes if s.geom_type in ("LineString", "MultiLineString")))


def _count_points(shapes: List[Any]) -> float:
    return float(sum(1 for s in shapes if s.geom_type == "Point"))


# ────────────────────────────── 要素配对 ──────────────────────────────


def _features_of(fc: Any) -> List[Dict[str, Any]]:
    if not isinstance(fc, dict):
        return []
    feats = fc.get("features")
    if not isinstance(feats, list):
        return []
    return [f for f in feats if isinstance(f, dict) and isinstance(f.get("geometry"), dict)]


def _feature_key(feat: Dict[str, Any]) -> str:
    props = feat.get("properties") if isinstance(feat.get("properties"), dict) else {}
    fid = props.get("id") or feat.get("id")
    if isinstance(fid, (str, int)) and str(fid):
        return f"id:{fid}"
    try:
        wkt = shape(feat["geometry"]).wkt
    except Exception:  # noqa: BLE001
        wkt = json.dumps(feat.get("geometry"), ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha1(wkt.encode("utf-8")).hexdigest()  # noqa: S324 — 非安全用途
    return f"geom:{feat.get('geometry', {}).get('type', '')}:{digest}"


def _same_geometry(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    try:
        return (
            shape(a["geometry"]).equals(shape(b["geometry"]))
            if a.get("geometry") and b.get("geometry")
            else False
        )
    except Exception:  # noqa: BLE001
        return False


@dataclass
class GeometryDiff:
    """单图层几何差分（米制增量；投影口径见模块 docstring）。"""

    layer_id: str
    added_features: List[Dict[str, Any]] = field(default_factory=list)
    removed_features: List[Dict[str, Any]] = field(default_factory=list)
    modified_features: List[Dict[str, Any]] = field(default_factory=list)
    unchanged_count: int = 0
    unchanged_area_m2: float = 0.0
    added_area_m2: float = 0.0
    removed_area_m2: float = 0.0
    added_length_m: float = 0.0
    removed_length_m: float = 0.0

    def summary(self) -> Dict[str, Any]:
        return {
            "added": len(self.added_features),
            "removed": len(self.removed_features),
            "modified": len(self.modified_features),
            "unchanged": self.unchanged_count,
            "added_area_m2": round(self.added_area_m2, 3),
            "removed_area_m2": round(self.removed_area_m2, 3),
            "added_length_m": round(self.added_length_m, 3),
            "removed_length_m": round(self.removed_length_m, 3),
        }


def _shared_lat0(
    baseline_payloads: Dict[str, Dict[str, Any]],
    branch_payloads: Dict[str, Dict[str, Any]],
) -> float:
    """两侧**联合**均值纬度（比率精确性的关键：两侧必须同一投影口径，
    否则同纬度长度/面积比率会随 lat0 漂移，delta_pct 失真）。"""
    latitudes = _collect_latitudes(baseline_payloads) + _collect_latitudes(branch_payloads)
    return sum(latitudes) / len(latitudes) if latitudes else 0.0


def diff_layers(
    baseline_payloads: Dict[str, Dict[str, Any]],
    branch_payloads: Dict[str, Dict[str, Any]],
    service_radius_m: float = DEFAULT_SERVICE_RADIUS_M,
) -> Dict[str, Any]:
    """逐图层几何差分 + 服务覆盖差分（确定性纯函数）。

    返回 ``{layer_id: GeometryDiff}``；当任一侧存在 facility 角色图层时追加
    保留键 ``COVERAGE_KEY``（dict：gained/lost 面积与人口），供对比图层与
    指标差分共用同一次配对结果。
    """
    diffs: Dict[str, Any] = {}
    layer_ids = sorted(set(baseline_payloads) | set(branch_payloads))
    has_facility = any(
        resolve_layer_role(lid) == "facility" for lid in layer_ids
    )
    for layer_id in layer_ids:
        base_fc = baseline_payloads.get(layer_id)
        branch_fc = branch_payloads.get(layer_id)
        base_feats = _features_of(base_fc)
        branch_feats = _features_of(branch_fc)
        latitudes = _collect_latitudes(
            {k: v for k, v in ((layer_id, base_fc), (f"{layer_id}@", branch_fc)) if isinstance(v, dict)}
        )
        lat0 = sum(latitudes) / len(latitudes) if latitudes else 0.0

        base_shapes = _project_shapes(base_feats, lat0)
        branch_shapes = _project_shapes(branch_feats, lat0)
        base_by_key = {_feature_key(f): (f, s) for f, s in zip(base_feats, base_shapes)}
        branch_by_key = {
            _feature_key(f): (f, s) for f, s in zip(branch_feats, branch_shapes)
        }

        diff = GeometryDiff(layer_id=layer_id)
        for key, (feat, shp) in branch_by_key.items():
            if key not in base_by_key:
                diff.added_features.append(feat)
                diff.added_area_m2 += _safe_area(shp)
                diff.added_length_m += _safe_length(shp)
            elif not _same_geometry(base_by_key[key][0], feat):
                diff.modified_features.append(
                    {"before": base_by_key[key][0], "after": feat}
                )
            else:
                diff.unchanged_count += 1
                diff.unchanged_area_m2 += _safe_area(shp)
        for key, (feat, shp) in base_by_key.items():
            if key not in branch_by_key:
                diff.removed_features.append(feat)
                diff.removed_area_m2 += _safe_area(shp)
                diff.removed_length_m += _safe_length(shp)
        diffs[layer_id] = diff

    if has_facility:
        coverage_lat0 = _shared_lat0(baseline_payloads, branch_payloads)
        diffs[COVERAGE_KEY] = _coverage_diff(
            baseline_payloads, branch_payloads, service_radius_m, coverage_lat0
        )
    return diffs


def _safe_area(shp: Any) -> float:
    try:
        return float(shp.area) if shp.geom_type in ("Polygon", "MultiPolygon") else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def _safe_length(shp: Any) -> float:
    try:
        return (
            float(shp.length)
            if shp.geom_type in ("LineString", "MultiLineString")
            else 0.0
        )
    except Exception:  # noqa: BLE001
        return 0.0


def _coverage_diff(
    baseline_payloads: Dict[str, Dict[str, Any]],
    branch_payloads: Dict[str, Dict[str, Any]],
    service_radius_m: float,
    lat0: float,
) -> Dict[str, Any]:
    """服务覆盖差分（proxy:v1）：设施缓冲并集的 gained/lost 面积 + 覆盖人口。

    覆盖人口口径：人口图层（population 角色）中含数值 ``population`` 属性的
    格网要素，其**质心**落入设施缓冲并集即计入（不做部分面积积分）。
    两侧共用同一 ``lat0`` 投影（否则缓冲几何不可比）。
    """
    out: Dict[str, Any] = {
        "gained_area_m2": 0.0,
        "lost_area_m2": 0.0,
        "baseline_coverage_population": None,
        "branch_coverage_population": None,
        "gained_population": None,
        "lost_population": None,
    }
    base_union = _facility_buffer_union(baseline_payloads, service_radius_m, lat0)
    branch_union = _facility_buffer_union(branch_payloads, service_radius_m, lat0)
    if base_union is None and branch_union is None:
        return out
    base_u = base_union if base_union is not None else _empty_geom()
    branch_u = branch_union if branch_union is not None else _empty_geom()
    try:
        out["gained_area_m2"] = float(
            (branch_u.difference(base_u)).area
        ) if not branch_u.is_empty else 0.0
        out["lost_area_m2"] = float(
            (base_u.difference(branch_u)).area
        ) if not base_u.is_empty else 0.0
    except Exception:  # noqa: BLE001 — 覆盖差分失败按零计（诚实缺计）
        logger.debug("[whatif] coverage area diff failed", exc_info=True)

    base_cells = _population_cells(baseline_payloads, lat0)
    branch_cells = _population_cells(branch_payloads, lat0)
    if base_cells is not None:
        out["baseline_coverage_population"] = _covered_population(base_cells, base_u)
    if branch_cells is not None:
        out["branch_coverage_population"] = _covered_population(branch_cells, branch_u)
    if base_cells is not None and branch_cells is not None:
        base_cov = out["baseline_coverage_population"]
        branch_cov = out["branch_coverage_population"]
        out["gained_population"] = max(0.0, branch_cov - base_cov)
        out["lost_population"] = max(0.0, base_cov - branch_cov)
    return out


def _empty_geom():
    from shapely.geometry import GeometryCollection

    return GeometryCollection()


def _facility_buffer_union(
    payloads: Dict[str, Dict[str, Any]], radius_m: float, lat0: float
) -> Optional[Any]:
    buffers = []
    for layer_id, fc in payloads.items():
        if resolve_layer_role(layer_id) != "facility":
            continue
        for feat in _features_of(fc):
            geom = feat.get("geometry")
            if not isinstance(geom, dict) or geom.get("type") != "Point":
                continue
            props = feat.get("properties") if isinstance(feat.get("properties"), dict) else {}
            radius = props.get("service_radius_m", radius_m)
            try:
                radius = float(radius)
            except (TypeError, ValueError):
                radius = radius_m
            try:
                pt = _project_shapes([feat], lat0)[0]
            except Exception:  # noqa: BLE001 — 坏几何设施点跳过
                continue
            buffers.append(pt.buffer(radius))
    if not buffers:
        return None
    return unary_union(buffers)


def _population_cells(
    payloads: Dict[str, Dict[str, Any]], lat0: float
) -> Optional[List[Dict[str, Any]]]:
    """返回 (projected_centroid, population) 列表；无人口图层 → None。"""
    cells: List[Dict[str, Any]] = []
    found_population_layer = False
    for layer_id, fc in payloads.items():
        if resolve_layer_role(layer_id) != "population":
            continue
        found_population_layer = True
        for feat in _features_of(fc):
            props = feat.get("properties") if isinstance(feat.get("properties"), dict) else {}
            pop = props.get("population")
            if not isinstance(pop, (int, float)) or isinstance(pop, bool):
                continue
            geom = feat.get("geometry")
            if not isinstance(geom, dict):
                continue
            try:
                shp = _project_shapes([feat], lat0)[0]
            except Exception:  # noqa: BLE001
                continue
            cells.append({"centroid": shp.centroid, "population": float(pop)})
    return cells if found_population_layer else None


def _covered_population(cells: List[Dict[str, Any]], coverage_geom: Any) -> float:
    total = 0.0
    for cell in cells:
        try:
            if coverage_geom.is_empty:
                continue
            if coverage_geom.contains(cell["centroid"]) or coverage_geom.touches(
                cell["centroid"]
            ):
                total += cell["population"]
        except Exception:  # noqa: BLE001
            continue
    return total


# ────────────────────────────── 指标差分 ──────────────────────────────

_METRIC_SPECS: Dict[str, Dict[str, str]] = {
    "service_coverage_population": {
        "name": "服务覆盖人口",
        "unit": "人",
        "direction": "maximize",
    },
    "facility_count": {"name": "设施点位数", "unit": "个", "direction": "maximize"},
    "green_area_m2": {"name": "绿地面积", "unit": "m²", "direction": "maximize"},
    "road_length_m": {"name": "道路总长度", "unit": "m", "direction": ""},
    "road_capacity_index": {
        "name": "道路通行能力指数（代理）",
        "unit": "veh/h",
        "direction": "maximize",
    },
}


def evaluate_metrics(
    payloads: Dict[str, Dict[str, Any]],
    service_radius_m: float = DEFAULT_SERVICE_RADIUS_M,
    lat0: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """单侧指标评估（proxy:v1）。输入缺失的指标返回 None（GIS-03）。

    ``lat0`` 缺省时从本侧载荷自算；做两侧对比时**必须**传共享 lat0
    （见 :func:`_shared_lat0`），否则 delta 比率随投影漂移。
    """
    if lat0 is None:
        latitudes = _collect_latitudes(payloads)
        lat0 = sum(latitudes) / len(latitudes) if latitudes else 0.0

    green_area = 0.0
    road_length = 0.0
    has_green = has_road = False
    facility_count = 0.0
    has_facility = False
    for layer_id, fc in payloads.items():
        role = resolve_layer_role(layer_id)
        feats = _features_of(fc)
        shapes = _project_shapes(feats, lat0)
        if role == "green":
            has_green = True
            green_area += _total_area(shapes)
        elif role == "road":
            has_road = True
            road_length += _total_length(shapes)
        elif role == "facility":
            has_facility = True
            facility_count += _count_points(shapes)

    coverage: Optional[float] = None
    if has_facility:
        cells = _population_cells(payloads, lat0)
        if cells is not None:
            union = _facility_buffer_union(payloads, service_radius_m, lat0)
            coverage = (
                _covered_population(cells, union) if union is not None else 0.0
            )

    return {
        "service_coverage_population": coverage,
        "facility_count": facility_count if has_facility else None,
        "green_area_m2": green_area if has_green else None,
        "road_length_m": road_length if has_road else None,
        "road_capacity_index": (
            road_length / 1000.0 * ROAD_CAPACITY_PER_KM if has_road else None
        ),
    }


def _gap_note(metric_key: str) -> str:
    notes = {
        "service_coverage_population": (
            "缺少设施图层（facility 角色）或含 population 属性的人口图层，"
            "无法计算服务覆盖人口（GIS-03：不编造基线）"
        ),
        "facility_count": "任一侧缺少设施图层（facility 角色）",
        "green_area_m2": "任一侧缺少绿地图层（green 角色）",
        "road_length_m": "任一侧缺少道路图层（road 角色）",
        "road_capacity_index": "任一侧缺少道路图层（road 角色）",
    }
    return notes.get(metric_key, "基线证据缺失（GIS-03：不编造基线）")


def compute_metric_deltas(
    baseline_payloads: Dict[str, Dict[str, Any]],
    branch_payloads: Dict[str, Dict[str, Any]],
    service_radius_m: float = DEFAULT_SERVICE_RADIUS_M,
) -> List[MetricDeltaV2]:
    """两侧指标评估 → MetricDeltaV2 列表（GIS-03：缺基线全 None + gap note）。

    两侧共享同一投影 lat0（联合均值）—— delta 比率与投影无关的前提。
    """
    shared_lat0 = _shared_lat0(baseline_payloads, branch_payloads)
    base_values = evaluate_metrics(baseline_payloads, service_radius_m, lat0=shared_lat0)
    branch_values = evaluate_metrics(branch_payloads, service_radius_m, lat0=shared_lat0)
    deltas: List[MetricDeltaV2] = []
    for key, spec in _METRIC_SPECS.items():
        base = base_values.get(key)
        sim = branch_values.get(key)
        if base is None or sim is None:
            deltas.append(
                MetricDeltaV2(
                    metric_key=key,
                    metric_name=spec["name"],
                    baseline=None,
                    simulated=None,
                    delta_abs=None,
                    delta_pct=None,
                    unit=spec["unit"],
                    missing_baseline=True,
                    evidence_gap_note=_gap_note(key),
                )
            )
            continue
        delta_abs = sim - base
        delta_pct = (delta_abs / base * 100.0) if base != 0 else None
        deltas.append(
            MetricDeltaV2(
                metric_key=key,
                metric_name=spec["name"],
                baseline=base,
                simulated=sim,
                delta_abs=delta_abs,
                delta_pct=delta_pct,
                unit=spec["unit"],
                missing_baseline=False,
            )
        )
    return deltas


# ────────────────────────────── 对比图层 ──────────────────────────────


def _impact_sign_for_feature(diff_kind: str, role: str) -> str:
    """impact 着色（proxy:v1 启发式）：绿地/设施/道路的增删即正负信号。"""
    if diff_kind == "added":
        return _IMPACT_POSITIVE if role in ("green", "facility", "road") else _IMPACT_NEUTRAL
    if diff_kind == "removed":
        return _IMPACT_NEGATIVE if role in ("green", "facility", "road") else _IMPACT_NEUTRAL
    return _IMPACT_NEUTRAL


def _annotate(feat: Dict[str, Any], layer_id: str, diff_kind: str) -> Dict[str, Any]:
    role = resolve_layer_role(layer_id)
    sign = _impact_sign_for_feature(diff_kind, role)
    out = json.loads(json.dumps(feat, ensure_ascii=False))
    props = out.get("properties") if isinstance(out.get("properties"), dict) else {}
    props["layer_id"] = layer_id
    props["diff_kind"] = diff_kind
    props["impact_sign"] = sign
    props["render_hint"] = _RENDER_HINTS[sign]
    out["properties"] = props
    return out


def build_diff_overlay(source: Any) -> Dict[str, Any]:
    """显式对比图层 FeatureCollection（红=负面恶化，绿=正面改善）。

    双形态入参：
    - ``dict[layer_id, GeometryDiff]``（diff_layers 原始输出，含 ``_coverage``
      保留键时自动跳过）→ 现场标注 diff_kind/impact_sign/render_hint；
    - ``list[BranchDiffResult]``（管理面分支差分）→ 合并各分支已标注的
      ``overlay_features``（多方案对比图层）。
    """
    features: List[Dict[str, Any]] = []
    if isinstance(source, dict):
        for layer_id, value in source.items():
            if not isinstance(value, GeometryDiff):
                continue
            for feat in value.added_features:
                features.append(_annotate(feat, layer_id, "added"))
            for feat in value.removed_features:
                features.append(_annotate(feat, layer_id, "removed"))
            for pair in value.modified_features:
                features.append(_annotate(pair["after"], layer_id, "modified"))
    elif isinstance(source, list):
        for item in source:
            overlay = getattr(item, "overlay_features", None)
            if isinstance(overlay, list):
                features.extend(
                    f for f in overlay if isinstance(f, dict)
                )
    return {"type": "FeatureCollection", "features": features}


def extract_layer_payloads(mapspec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """MapSpec → ``{layer_id: FeatureCollection}``（仅 inlineData 通道）。

    兼容两种入库形态：``entry.inlineData`` 直为 FeatureCollection，或引擎
    ingestion 的双层包装（``entry.inlineData.inlineData``）。ref/url/dataPath
    等外置载荷不在 spec 内（Zero Big Data in Context），按缺载荷图层跳过 ——
    调用方据此产出 evidence_gap_note。
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(mapspec, dict):
        return out
    sources = mapspec.get("sources") if isinstance(mapspec.get("sources"), dict) else {}
    for layer in mapspec.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        layer_id = str(layer.get("id") or "")
        source_id = layer.get("source")
        if not layer_id or not isinstance(source_id, str):
            continue
        entry = sources.get(source_id)
        if not isinstance(entry, dict):
            continue
        inline = entry.get("inlineData")
        for candidate in (inline, inline.get("inlineData") if isinstance(inline, dict) else None):
            if isinstance(candidate, dict) and candidate.get("type") == "FeatureCollection":
                out[layer_id] = candidate
                break
    return out


def cost_proxy_of(diffs: Dict[str, Any]) -> float:
    """投入代理（proxy:v1）：新增要素几何量之和（长度 m + √面积 m）。

    仅用于处方建议的 ROI 敏感度排序口径 —— 显式披露为代理量，不是造价估算。
    """
    total = 0.0
    for value in diffs.values():
        if not isinstance(value, GeometryDiff):
            continue
        total += value.added_length_m + math.sqrt(max(value.added_area_m2, 0.0))
    return round(total, 3)
