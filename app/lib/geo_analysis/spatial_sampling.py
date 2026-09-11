"""空间抽样科学（Goal 07 Phase A taxonomy「Sampling」族实现层）。

随机 / 系统 / 分层三种空间点抽样设计。抽样是**生成型**算法：输入面要素
（抽样框），输出 Point FeatureCollection（样本），属性携带来源多边形
索引、层别与样本 id —— 下游（野外核查点、精度评估、地统计布点）可直接
消费。

职责边界（CONTRACT_BACKBONE §1，与 terrain/cost_surface 同门）：本模块
只做纯几何/统计数学 —— 不读文件、不写 artifact、不挂证据块（工具层
职责）。

科学约定：

- **均匀性在投影后度量空间定义**：地理输入先自动投影到局部 UTM
  （``to_utm_gdf``），随机点在度量 bbox 内均匀撒点 + prepared 覆盖判定
  （拒绝采样）——直接经纬度撒点会让高纬样本密度偏高；
- **确定性**：全部随机性经 ``numpy.random.default_rng(seed)``（调用方
  种子，缺省 42）；同一 (输入, seed) 逐位可复现；
- **披露**：零面积/无有效多边形跳过计数、拒绝采样尝试上限、分层分配
  表全部进 meta（确定性纯文本/数值事实，无时间戳）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.lib.cancellation import checkpoint
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    MissingRequiredField,
    NoValidObservations,
    ResourceScaleMismatch,
)

__all__ = [
    "SAMPLING_SEED_DEFAULT",
    "random_points_in_polygons",
    "systematic_grid_points",
    "stratified_points_in_polygons",
]

#: 调用方种子缺省（与置换推断族同一缺省，复现语义一致）。
SAMPLING_SEED_DEFAULT = 42

#: 拒绝采样尝试上限（× 需求点数）：bbox 覆盖率极低的凹型多边形不再
#: 无限尝试 —— 超限即类型化报错（披露提示降分辨率/简化几何）。
_MAX_REJECTION_ATTEMPTS_FACTOR = 200

_PREPARED_MIN_VERTICES = 24


def _parse_polygon_gdf(geojson: Any):
    """GeoJSON → UTM GeoDataFrame（仅 Polygon/MultiPolygon）。"""
    from app.lib.geo_processor.core import to_utm_gdf

    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection of polygon features",
        )
    gdf, utm_crs = res
    types = set(gdf.geometry.geom_type)
    if not types <= {"Polygon", "MultiPolygon"}:
        raise NoValidObservations(
            f"sampling frame requires polygonal geometries (found {sorted(types)})",
            correction_hint="dissolve/tessellate points to polygons first",
        )
    gdf = gdf.reset_index(drop=True)
    return gdf, gdf.geometry.area.to_numpy(float), utm_crs


def _sample_uniform_in_geom(
    geom, n: int, rng: np.random.Generator,
    *,
    label: str = "polygon",
) -> Tuple[np.ndarray, np.ndarray, int]:
    """在单个（多）多边形内均匀撒 n 点（度量 bbox 拒绝采样）。

    返回 (xs, ys, attempts)。超限（覆盖率过低）→ DegenerateData。
    """
    min_x, min_y, max_x, max_y = geom.bounds
    width = max_x - min_x
    height = max_y - min_y
    if width <= 0 or height <= 0:
        raise DegenerateData(
            f"{label} has zero extent in projected space",
            correction_hint="remove degenerate polygons from the sampling frame",
        )
    from shapely import points as shp_points

    collected_x: List[float] = []
    collected_y: List[float] = []
    needed = n
    attempts = 0
    max_attempts = n * _MAX_REJECTION_ATTEMPTS_FACTOR
    while needed > 0:
        batch = max(needed * 2, 32)
        attempts += batch
        if attempts > max_attempts:
            raise DegenerateData(
                f"rejection sampling exceeded {max_attempts} attempts for "
                f"{label} (bbox coverage too low); simplify the geometry or "
                "increase the sample count granularity",
                correction_hint="check the polygon geometry validity/extent",
            )
        xs = rng.uniform(min_x, max_x, size=batch)
        ys = rng.uniform(min_y, max_y, size=batch)
        inside = geom.covers(shp_points(np.column_stack([xs, ys])))
        kept_x = xs[inside][:needed]
        kept_y = ys[inside][:needed]
        collected_x.extend(kept_x.tolist())
        collected_y.extend(kept_y.tolist())
        needed -= len(kept_x)
    return np.asarray(collected_x), np.asarray(collected_y), attempts


def _to_feature_collection(
    xs: np.ndarray, ys: np.ndarray, utm_crs: str,
    props: Dict[str, List[Any]],
) -> Dict[str, Any]:
    """UTM 样本点 → WGS84 Point FeatureCollection（带来源属性）。"""
    import geopandas as gpd

    from shapely.geometry import mapping

    sample_gdf = gpd.GeoDataFrame(
        props,
        geometry=gpd.points_from_xy(xs, ys),
        crs=utm_crs,
    ).to_crs("EPSG:4326")
    features = []
    records = sample_gdf.drop(columns="geometry").to_dict("records")
    for i, geom in enumerate(sample_gdf.geometry):
        p = {k: (v.item() if isinstance(v, np.generic) else v)
             for k, v in records[i].items()}
        features.append({
            "type": "Feature",
            "properties": p,
            "geometry": mapping(geom),
        })
    return {"type": "FeatureCollection", "features": features}


def _validate_n(n: Any) -> int:
    try:
        n_i = int(n)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"n must be an integer (got {n!r})") from exc
    if n_i < 1:
        raise InsufficientSamples(
            f"sample count must be >= 1 (got {n_i})",
            correction_hint="request at least one sample per unit",
        )
    if n_i > 1_000_000:
        raise ResourceScaleMismatch(
            f"sample count {n_i} exceeds the 1,000,000 guard",
            correction_hint="split the request or coarsen the design",
        )
    return n_i


def _validate_seed(seed: Any) -> int:
    try:
        seed_i = int(seed)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"seed must be an integer (got {seed!r})") from exc
    if not (0 <= seed_i < 2 ** 32):
        raise ValueError(f"seed must be in [0, 2^32) (got {seed_i})")
    return seed_i


def random_points_in_polygons(
    geojson: dict,
    n: int,
    *,
    seed: int = SAMPLING_SEED_DEFAULT,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """逐多边形简单随机抽样（每面 n 个随机点；SRS 设计）。

    每个有面积的多边形内独立撒 n 个均匀随机点（投影后度量空间拒绝
    采样）。零面积多边形跳过并披露。输出属性：``polygon_index``、
    ``sample_in_polygon``（面内序号 0..n-1）、``sample_id``（全局序号）。

    返回 (FeatureCollection, meta)。meta：n_per_polygon / polygon_count /
    zero_area_skipped / seed / design="srs_per_polygon" / attempts。
    """
    n_i = _validate_n(n)
    seed_i = _validate_seed(seed)
    gdf, areas, utm_crs = _parse_polygon_gdf(geojson)
    geoms = list(gdf.geometry)
    zero_area = int(np.sum(areas <= 0))
    valid_idx = [i for i in range(len(geoms)) if areas[i] > 0]
    if not valid_idx:
        raise DegenerateData(
            "all polygons have zero area in projected space",
            correction_hint="check the sampling frame geometry",
        )
    rng = np.random.default_rng(seed_i)
    xs: List[float] = []
    ys: List[float] = []
    poly_idx: List[int] = []
    within: List[int] = []
    attempts_total = 0
    for i in valid_idx:
        checkpoint()
        gx, gy, att = _sample_uniform_in_geom(geoms[i], n_i, rng,
                                              label=f"polygon {i}")
        xs.extend(gx.tolist())
        ys.extend(gy.tolist())
        poly_idx.extend([i] * len(gx))
        within.extend(range(len(gx)))
        attempts_total += att
    meta = {
        "design": "srs_per_polygon",
        "n_per_polygon": n_i,
        "polygon_count": len(valid_idx),
        "zero_area_skipped": zero_area,
        "sample_count": len(xs),
        "seed": seed_i,
        "attempts": attempts_total,
    }
    fc = _to_feature_collection(
        np.asarray(xs), np.asarray(ys), utm_crs,
        {
            "polygon_index": poly_idx,
            "sample_in_polygon": within,
            "sample_id": list(range(len(xs))),
        },
    )
    return fc, meta


def systematic_grid_points(
    geojson: dict,
    spacing: float,
    *,
    seed: int = SAMPLING_SEED_DEFAULT,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """系统网格抽样（度量间距规则格网，随机起点偏移，裁剪到面内）。

    全框 bbox 上以 ``spacing``（米）铺规则格网；随机起点偏移由 ``seed``
    决定（避免与坐标轴对齐的周期性偏差，Cochran 1977 系统抽样惯例）；
    仅保留落入任一多边形的格点。输出属性：``polygon_index``（命中的
    多边形，多点重叠取最小索引）、``row``、``col``、``sample_id``。
    """
    from shapely import points as shp_points
    from shapely import prepare

    if not np.isfinite(spacing) or spacing <= 0:
        raise ValueError(f"spacing must be a positive number (got {spacing!r})")
    seed_i = _validate_seed(seed)
    gdf, areas, utm_crs = _parse_polygon_gdf(geojson)
    if not (areas > 0).any():
        raise DegenerateData(
            "all polygons have zero area in projected space",
            correction_hint="check the sampling frame geometry",
        )
    union = gdf.geometry.union_all()
    min_x, min_y, max_x, max_y = union.bounds
    span_x = max_x - min_x
    span_y = max_y - min_y
    n_cols = int(span_x // spacing) + 1
    n_rows = int(span_y // spacing) + 1
    if n_cols * n_rows > 4_000_000:
        raise InsufficientSamples(
            f"spacing {spacing} yields {n_cols * n_rows} grid cells over the "
            "frame extent (guard 4,000,000); increase spacing or clip the frame",
            correction_hint="raise spacing above the extent/budget ratio",
        )
    rng = np.random.default_rng(seed_i)
    off_x = rng.uniform(0.0, spacing)
    off_y = rng.uniform(0.0, spacing)

    # 网格全量矢量化构造（行×列 meshgrid）+ 逐多边形 prepared covers
    #（索引序首中，与逐点 STRtree 查询语义逐位一致 —— 含重叠面归属）。
    col_ids = np.arange(n_cols)
    xs_grid = min_x + off_x + col_ids * spacing
    col_ids = col_ids[xs_grid <= max_x]
    xs_grid = xs_grid[xs_grid <= max_x]
    row_ids = np.arange(n_rows)
    ys_grid = min_y + off_y + row_ids * spacing
    row_ids = row_ids[ys_grid <= max_y]
    ys_grid = ys_grid[ys_grid <= max_y]
    n_cols = int(len(col_ids))
    n_rows = int(len(row_ids))
    XX, YY = np.meshgrid(xs_grid, ys_grid)
    pts = shp_points(np.column_stack([XX.ravel(), YY.ravel()]))
    pt_rows, pt_cols = np.meshgrid(row_ids, col_ids, indexing="ij")
    pt_rows = pt_rows.ravel()
    pt_cols = pt_cols.ravel()

    geoms = list(gdf.geometry)
    for g in geoms:
        prepare(g)
    taken = np.zeros(len(pts), dtype=bool)
    xs: List[float] = []
    ys: List[float] = []
    rows: List[int] = []
    cols: List[int] = []
    poly_idx: List[int] = []
    for gi, geom in enumerate(geoms):
        if taken.all():
            break
        candidate = (~taken) & geom.covers(pts)
        if not candidate.any():
            continue
        idx = np.flatnonzero(candidate)
        xs.extend(XX.ravel()[idx].tolist())
        ys.extend(YY.ravel()[idx].tolist())
        rows.extend(pt_rows[idx].tolist())
        cols.extend(pt_cols[idx].tolist())
        poly_idx.extend([gi] * len(idx))
        taken |= candidate
    meta = {
        "design": "systematic_grid",
        "spacing": float(spacing),
        "n_rows": n_rows,
        "n_cols": n_cols,
        "sample_count": len(xs),
        "seed": seed_i,
        "random_start_offset": True,
    }
    fc = _to_feature_collection(
        np.asarray(xs, dtype=float), np.asarray(ys, dtype=float), utm_crs,
        {
            "polygon_index": poly_idx,
            "row": rows,
            "col": cols,
            "sample_id": list(range(len(xs))),
        },
    )
    return fc, meta


def stratified_points_in_polygons(
    geojson: dict,
    stratum_field: str,
    n_per_stratum: int,
    *,
    allocation: str = "equal",
    seed: int = SAMPLING_SEED_DEFAULT,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """分层抽样（按 ``stratum_field`` 分层；equal / proportional 分配）。

    - ``equal``：每层 n_per_stratum 个点；
    - ``proportional``：按层面积权重分配 n_per_stratum·Σ 个点
      （大盘尼分配 + 下限 1；层零面积跳过）。

    每层内做面级再分（层内每个多边形按其面积占比承担该层的样本数，
    大盘尼分配），层内面级样本仍为均匀随机。输出属性：``stratum``、
    ``polygon_index``、``sample_id``。
    """
    if allocation not in ("equal", "proportional"):
        raise ValueError(
            f"allocation must be 'equal' or 'proportional' (got {allocation!r})")
    n_total = _validate_n(n_per_stratum)
    seed_i = _validate_seed(seed)
    gdf, areas, utm_crs = _parse_polygon_gdf(geojson)
    if stratum_field not in gdf.columns:
        raise MissingRequiredField(
            f"stratum field '{stratum_field}' not found on features",
            correction_hint=f"add a '{stratum_field}' property to the sampling frame",
        )
    strata = gdf[stratum_field].astype(str).to_numpy()
    geoms = list(gdf.geometry)
    uniq = sorted(set(strata.tolist()))
    rng = np.random.default_rng(seed_i)

    xs: List[float] = []
    ys: List[float] = []
    stratum_labels: List[str] = []
    poly_idx: List[int] = []
    allocation_table: Dict[str, int] = {}
    zero_area_skipped = 0

    for s in uniq:
        checkpoint()
        members = [i for i in range(len(geoms))
                   if strata[i] == s and areas[i] > 0]
        zero_area_skipped += int(np.sum(
            (strata == s) & (areas <= 0)))
        if not members:
            allocation_table[s] = 0
            continue
        if allocation == "equal":
            # 等分配：n 平摊到层内多边形（整数基线 + 余数按索引升序 +1）
            base, rem = divmod(n_total, len(members))
            quotas = {i: base + (1 if k < rem else 0)
                      for k, i in enumerate(members)}
        else:
            # 比例分配（面积权重）：floor + 大盘尼余数（小数部分降序，
            # 平列按索引升序）—— 确定性且总量恰为 n。
            area_sum = float(sum(areas[i] for i in members))
            quotas = {i: n_total * areas[i] / area_sum for i in members}
            floors = {i: int(np.floor(q)) for i, q in quotas.items()}
            remaining = n_total - sum(floors.values())
            order = sorted(members,
                           key=lambda i: (-(quotas[i] - np.floor(quotas[i])), i))
            for i in order[:remaining]:
                floors[i] += 1
            quotas = floors
        for i in members:
            k = int(quotas[i])
            if k <= 0:
                continue
            gx, gy, _ = _sample_uniform_in_geom(geoms[i], k, rng,
                                                label=f"polygon {i} (stratum {s})")
            xs.extend(gx.tolist())
            ys.extend(gy.tolist())
            stratum_labels.extend([s] * len(gx))
            poly_idx.extend([i] * len(gx))
        allocation_table[s] = int(sum(int(quotas[i]) for i in members))

    meta = {
        "design": "stratified",
        "allocation": allocation,
        "stratum_field": stratum_field,
        "n_strata": len(uniq),
        "allocation_table": allocation_table,
        "zero_area_skipped": int(zero_area_skipped),
        "sample_count": len(xs),
        "seed": seed_i,
    }
    fc = _to_feature_collection(
        np.asarray(xs, dtype=float), np.asarray(ys, dtype=float), utm_crs,
        {
            "stratum": stratum_labels,
            "polygon_index": poly_idx,
            "sample_id": list(range(len(xs))),
        },
    )
    return fc, meta
