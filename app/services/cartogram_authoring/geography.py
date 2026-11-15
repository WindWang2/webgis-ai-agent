"""G47 地理准备：资格、投影、anchor、原邻接（WP02 / WP03 / WP04）。

**布局坐标不是地理坐标。** 本模块只做三件事，产物也只用于布局准备：

1. 资格裁决：身份（缺失/重复）、几何（类型/合法性/空）、坐标范围与轴序，
   以及本任务支持矩阵（单个区域投影；日期线/极区/全球明确拒绝）。
2. 在作者声明的投影里算每个统计单元的 anchor（``representative_point``，
   落在面内；MultiPolygon 的岛屿仍属同一统计单元）。
3. 由**真实共享边界长度**算原邻接；角点接触（长度≈0）不算邻接，孤岛没有
   邻接就如实没有。权重只用于吸引项，不改变面积。

任何"看不懂就猜"的行为都在这里被拒：不把度当米、不自动修非法面、不给
最近的邻居臆造桥隧。
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.strtree import STRtree
from shapely.validation import explain_validity

from app.services.cartogram_authoring.errors import AuthoringError

#: 支持矩阵：只处理不含日期线/极区的**单一区域**范围。
MAX_GEOGRAPHIC_SPAN_DEG = 180.0
MAX_ABS_LATITUDE = 66.5
MAX_TOTAL_VERTICES = 200_000
MAX_OFFENDERS = 12


@dataclass(frozen=True)
class Unit:
    feature_id: str
    label: str
    geometry: Any                 # 投影后的 shapely Polygon/MultiPolygon
    anchor: Tuple[float, float]   # 投影坐标（米）
    projected_area: float
    part_count: int


@dataclass(frozen=True)
class Edge:
    a: str
    b: str
    shared_length: float


@dataclass
class PreparedGeography:
    units: List[Unit]
    edges: List[Edge]
    source_crs: str
    target_crs: str
    bbox: Tuple[float, float, float, float]
    vertex_count: int
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def index(self) -> Dict[str, int]:
        return {u.feature_id: i for i, u in enumerate(self.units)}


def _crs_of(text: str, *, field_name: str):
    try:
        from pyproj import CRS
    except Exception as exc:  # pragma: no cover - pyproj 是硬依赖
        raise AuthoringError(
            "UNSUPPORTED_PROJECTION", "pyproj 不可用，无法做平面准备",
            status_code=503, field=field_name) from exc
    try:
        return CRS.from_user_input(text)
    except Exception as exc:
        raise AuthoringError(
            "UNKNOWN_CRS", f"无法解析 CRS {text!r}: {type(exc).__name__}",
            field=field_name,
            correction_hint="用 EPSG 代码（如 EPSG:4326 / EPSG:32650）。") from exc


def _feature_id(feat: Dict[str, Any], idx: int) -> str:
    raw = feat.get("id")
    if isinstance(raw, bool) or raw is None:
        return ""
    if isinstance(raw, (int, float)):
        return str(int(raw)) if float(raw).is_integer() else str(raw)
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _label_of(props: Dict[str, Any], fallback: str) -> str:
    for key in ("name", "NAME", "title", "label"):
        v = props.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:80]
    return fallback


def _check_geographic_extent(points: Sequence[Tuple[float, float]]) -> None:
    if not points:
        raise AuthoringError("EMPTY_GEOMETRY", "源几何没有可用坐标")
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    if max(abs(minx), abs(maxx)) > 180.0001 or max(abs(miny), abs(maxy)) > 90.0001:
        # 常见的纬度/经度互换：x 全在 ±90 内而 y 超出 ±90。
        if max(abs(miny), abs(maxy)) > 90.0001 and max(abs(minx), abs(maxx)) <= 90.0001:
            raise AuthoringError(
                "AXIS_ORDER_SUSPECT",
                f"坐标疑似 (lat, lon) 而非 (x, y)：x∈[{minx:.4f},{maxx:.4f}]、"
                f"y∈[{miny:.4f},{maxy:.4f}]",
                field="source_crs",
                correction_hint="以 always_xy 顺序提供 (lon, lat)，或声明正确的 "
                                "source_crs（如 EPSG:4326）。")
        raise AuthoringError(
            "INVALID_GEOMETRY",
            f"经纬度坐标越界：x∈[{minx:.4f},{maxx:.4f}]、y∈[{miny:.4f},{maxy:.4f}]",
            field="source_crs",
            correction_hint="核 source_crs 与坐标轴序；不把投影坐标当经纬度。")
    if maxx - minx > MAX_GEOGRAPHIC_SPAN_DEG:
        raise AuthoringError(
            "UNSUPPORTED_GEOGRAPHY",
            f"经度跨度 {maxx - minx:.2f}° 超过支持矩阵（单区域 ≤ "
            f"{MAX_GEOGRAPHIC_SPAN_DEG:.0f}°）；日期线/全球范围未实现",
            field="scope",
            correction_hint="收窄到一个连续区域，或改用普通比例圆；不生成似是而非的布局。")
    if maxy > MAX_ABS_LATITUDE or miny < -MAX_ABS_LATITUDE:
        raise AuthoringError(
            "UNSUPPORTED_GEOGRAPHY",
            f"纬度范围 [{miny:.2f},{maxy:.2f}] 进入极区（|lat| > "
            f"{MAX_ABS_LATITUDE}°）—— 本任务未实现极区投影",
            field="scope",
            correction_hint="换用不含极区的范围；不靠截图看起来合理放行。")


def _project_geometry(geom, transformer):
    """坐标变换（shapely 2.x 向量化 API；``shapely.ops.transform`` 已弃用）。"""
    from shapely import transform as shapely_apply

    def _apply(coords):  # coords: (N, 2) float64
        xs, ys = transformer.transform(coords[:, 0], coords[:, 1])
        return [[float(x), float(y)] for x, y in zip(xs, ys)]

    return shapely_apply(geom, _apply)


def prepare_geography(
    features: List[Dict[str, Any]],
    *,
    source_crs: str,
    target_crs: str,
    snap_tolerance_m: float,
    min_shared_length_m: float,
    max_vertices: int = MAX_TOTAL_VERTICES,
    included_ids: Optional[Sequence[str]] = None,
    excluded_ids: Sequence[str] = (),
) -> PreparedGeography:
    """把已冻结要素准备成投影单元 + 原邻接。所有拒绝都带要素身份。"""
    src = _crs_of(source_crs, field_name="source_crs")
    tgt = _crs_of(target_crs, field_name="projection.crs")
    if src.is_geographic and not target_crs.strip():
        raise AuthoringError(
            "UNSUPPORTED_PROJECTION", "地理坐标必须有明确目标投影", field="projection.crs")

    include = set(str(x) for x in included_ids) if included_ids is not None else None
    exclude = set(str(x) for x in excluded_ids)

    ids: List[str] = []
    missing_ids = 0
    for idx, feat in enumerate(features):
        fid = _feature_id(feat, idx)
        if not fid:
            missing_ids += 1
            continue
        ids.append(fid)
    if missing_ids:
        raise AuthoringError(
            "MISSING_FEATURE_ID",
            f"源中有 {missing_ids} 个要素缺少稳定 id —— 无法建立身份与联动",
            field="scope",
            correction_hint="为每个要素提供稳定 id（不要用行号或屏幕坐标）。")
    dupes = sorted(fid for fid, count in Counter(ids).items() if count > 1)
    if dupes:
        raise AuthoringError(
            "DUPLICATE_FEATURE_ID",
            f"源中有 {len(dupes)} 个重复 id（如 {dupes[0]!r}）—— 不能静默取第一行或重复计总量",
            field="scope",
            details={"duplicate_ids": dupes[:MAX_OFFENDERS],
                     "duplicate_count": len(dupes)},
            correction_hint="先去重/合并统计单元，或在范围里显式排除并说明。")

    selected: List[Tuple[str, Dict[str, Any], Any]] = []
    vertex_count = 0
    invalid: List[Dict[str, str]] = []
    empty: List[str] = []
    total_vertices = 0
    for feat in features:
        fid = _feature_id(feat, 0) or ""
        if include is not None and fid not in include:
            continue
        if fid in exclude:
            continue
        geom_raw = feat.get("geometry")
        props = feat.get("properties") if isinstance(feat.get("properties"), dict) else {}
        if not isinstance(geom_raw, dict) or not geom_raw.get("type"):
            invalid.append({"feature_id": fid, "reason": "missing_geometry"})
            continue
        gtype = str(geom_raw.get("type"))
        if gtype not in ("Polygon", "MultiPolygon"):
            invalid.append({"feature_id": fid, "reason": f"geometry_type:{gtype}"})
            continue
        try:
            geom = shape(geom_raw)
        except Exception:
            invalid.append({"feature_id": fid, "reason": "unreadable_geometry"})
            continue
        if geom.is_empty:
            empty.append(fid)
            continue
        if not geom.is_valid:
            # shapely 2.x 没有 ``is_valid_reason()`` 方法；诊断文字来自
            # ``explain_validity``（"Self-intersection[...]" 等），
            # 精确到要素，便于用户直接修数据。
            invalid.append({"feature_id": fid,
                            "reason": f"invalid_geometry:{explain_validity(geom)[:80]}"})
            continue
        total_vertices += _vertex_count(geom)
        selected.append((fid, props, geom))
        vertex_count = total_vertices

    if invalid:
        raise AuthoringError(
            "INVALID_GEOMETRY",
            f"{len(invalid)} 个要素几何非法/缺失（如 "
            f"{invalid[0]['feature_id']!r}: {invalid[0]['reason']}）",
            field="scope",
            details={"offenders": invalid[:MAX_OFFENDERS],
                     "invalid_count": len(invalid)},
            correction_hint="先修几何（或显式排除并写理由）；不静默修复后当原样。")
    if empty:
        raise AuthoringError(
            "EMPTY_GEOMETRY", f"{len(empty)} 个要素几何为空（如 {empty[0]!r}）",
            field="scope", details={"empty_ids": empty[:MAX_OFFENDERS]})
    if not selected:
        raise AuthoringError(
            "NO_VALID_QUANTITY", "范围内没有可用的面要素", field="scope")
    if total_vertices > max_vertices:
        raise AuthoringError(
            "BUDGET_EXCEEDED",
            f"总顶点 {total_vertices} 超过上限 {max_vertices}；不做静默简化冒充原几何",
            field="scope",
            correction_hint="先做显式简化/聚合（G27 能力），再进入 cartogram。")

    if src.is_geographic:
        pts: List[Tuple[float, float]] = []
        for _fid, _props, geom in selected:
            minx, miny, maxx, maxy = geom.bounds
            pts.extend([(minx, miny), (maxx, maxy), (minx, maxy), (maxx, miny)])
        _check_geographic_extent(pts)

    try:
        transformer = _transformer(src, tgt)
    except AuthoringError:
        raise
    except Exception as exc:
        raise AuthoringError(
            "UNSUPPORTED_PROJECTION",
            f"无法建立 {source_crs} → {target_crs} 的变换：{type(exc).__name__}",
            field="projection.crs",
            correction_hint="换一个覆盖该区域的投影（如 UTM/Web Mercator）。") from exc

    units: List[Unit] = []
    for fid, props, geom in selected:
        projected = _project_geometry(geom, transformer)
        if projected.is_empty:
            raise AuthoringError(
                "EMPTY_GEOMETRY", f"要素 {fid!r} 投影后为空", field="projection.crs")
        anchor_pt = projected.representative_point()
        part_count = len(projected.geoms) if isinstance(projected, MultiPolygon) else 1
        units.append(Unit(
            feature_id=fid,
            label=_label_of(props, fid),
            geometry=projected,
            anchor=(float(anchor_pt.x), float(anchor_pt.y)),
            projected_area=float(projected.area),
            part_count=part_count,
        ))

    edges = _compute_adjacency(units, snap_tolerance_m, min_shared_length_m)
    xs = [u.anchor[0] for u in units]
    ys = [u.anchor[1] for u in units]
    bbox = (min(xs), min(ys), max(xs), max(ys))
    return PreparedGeography(
        units=units, edges=edges, source_crs=str(src.to_string()),
        target_crs=str(tgt.to_string()), bbox=bbox, vertex_count=total_vertices,
        diagnostics={
            "multi_part_units": sum(1 for u in units if u.part_count > 1),
            "adjacency_contract": {
                "shared_boundary_length_m": True,
                "snap_tolerance_m": float(snap_tolerance_m),
                "min_shared_length_m": float(min_shared_length_m),
                "point_contact_counts_as_adjacent": False,
                "islands_get_synthetic_links": False,
            },
            "axis_order": "always_xy (lon, lat) → easting, northing",
            "layout_coordinates": "diagram units only; never EPSG geographic",
        },
    )


def _vertex_count(geom) -> int:
    if isinstance(geom, Polygon):
        return len(geom.exterior.coords) + sum(len(r.coords) for r in geom.interiors)
    if isinstance(geom, MultiPolygon):
        return sum(_vertex_count(g) for g in geom.geoms)
    return 0


def _transformer(src, tgt):
    from pyproj import Transformer
    return Transformer.from_crs(src, tgt, always_xy=True)


def _compute_adjacency(
    units: List[Unit], snap_tolerance_m: float, min_shared_length_m: float,
) -> List[Edge]:
    """真实共享边界长度（吸附容差内），按稳定 id 排序。"""
    if len(units) < 2:
        return []
    geoms = [u.geometry for u in units]
    tree = STRtree(geoms)
    edges: List[Edge] = []
    tol = float(snap_tolerance_m)
    for i, geom in enumerate(geoms):
        # bbox 先按容差扩张，保证"有缝隙但共边"的候选不漏。
        probe = geom.buffer(tol, join_style="mitre") if tol > 0 else geom
        for j in tree.query(probe):
            j = int(j)
            if j <= i:
                continue
            try:
                shared = geom.boundary.intersection(
                    geoms[j].boundary, grid_size=tol).length
            except Exception:
                shared = 0.0
            if shared >= min_shared_length_m:
                edges.append(Edge(a=units[i].feature_id, b=units[j].feature_id,
                                  shared_length=float(shared)))
    edges.sort(key=lambda e: (e.a, e.b))
    return edges


def normalize_edge_weights(edges: List[Edge]) -> List[Dict[str, Any]]:
    """把共享边界长度归一成 [0,1] 权重（只影响吸引强度，不影响面积）。"""
    if not edges:
        return []
    longest = max(e.shared_length for e in edges) or 1.0
    return [{"a": e.a, "b": e.b, "weight": round(e.shared_length / longest, 6),
             "shared_length_m": round(e.shared_length, 3)} for e in edges]


def bbox_span(bbox: Tuple[float, float, float, float]) -> Tuple[float, float]:
    return (max(1e-9, bbox[2] - bbox[0]), max(1e-9, bbox[3] - bbox[1]))


def is_finite_point(x: float, y: float) -> bool:
    return math.isfinite(x) and math.isfinite(y)
