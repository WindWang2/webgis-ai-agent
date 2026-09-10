import hashlib
import math
import threading
from collections import OrderedDict
from typing import Any, Callable, Optional
import numpy as np
import pandas as pd
import geopandas as gpd
from scipy import sparse
from shapely.geometry import Point, Polygon, mapping
from scipy import stats as sps
from scipy.stats import norm
from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_processor.core import to_utm_gdf
from app.lib.geo_analysis._vector import extract_centroids
from app.lib.geo_analysis.spatial_weights import (
    WEIGHT_SCHEMES,
    auto_band_8nn,
    build_contiguity_weights,
    build_distance_band_weights,
    build_knn_weights,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    MissingRequiredField,
    NoValidObservations,
    ResourceScaleMismatch,
    UnsupportedMethod,
)
from app.lib.gis.uncertainty import (
    MonteCarloSummary,
    SensitivityEnvelope,
    StatisticalSignificance,
)
# ADR-0052: 协作式取消检查点。cancellable() 在 chunk 边界读一次 contextvar，
# 未绑定 token 时开销为零；用户取消后长循环立即抛 OperationCancelled 退出，
# 真正释放 CPU 而不是只改 UI 状态。
from app.lib.cancellation import cancellable
# Foundation V2（A1）：多重校正独立实现在 spatial_regression（bh 与本模块
# _bh_qvalues 同语义）—— 从那边导入，本模块不反向导出，避免循环。
from app.lib.geo_analysis.spatial_regression import multiple_testing_correction


def _feature_props(row: "pd.Series") -> dict:
    """GeoPandas 行属性 → JSON 可序列化 dict。

    ``row.items()`` 对数值列返回 numpy 标量（int64/float64 等），标准库
    ``json.dumps`` 不认识 —— 结果入 ref 库（session_data_redis.store）即
    ``TypeError: Object of type int64 is not JSON serializable``，整个工具
    调用被误报为执行异常（2026-08-25 会话：h3_lisa 982 校网格即此崩法）。
    """
    return {
        k: (v.item() if isinstance(v, np.generic) else v)
        for k, v in row.items()
        if k != "geometry"
    }


def _assemble_features(
    gdf_wgs84: "gpd.GeoDataFrame",
    extra_props: dict,
) -> list:
    """列式组装 FeatureCollection（#1063）。

    旧的 ``gdf.iloc[i]`` 逐行循环每行物化一个 pandas Series（50k 特性
    实测 2.40s vs 列式 0.95s，~2.5×）。属性走一次 ``to_dict("records")``，
    统计列以 list zip 进来（调用方负责 round/类型归一，保证与旧输出
    golden 等价）。numpy 标量的 ``.item()`` 归一与 ``_feature_props``
    相同。
    """
    n = len(gdf_wgs84)
    if n == 0:
        return []
    # pandas：0 列 DataFrame（要素 properties 全空）的 to_dict("records")
    # 返回 [] 而非 n 个空 dict —— 逐行兜底，保证 records 与行数对齐。
    props_records = gdf_wgs84.drop(columns="geometry").to_dict("records")
    if len(props_records) != n:
        props_records = [
            props_records[i] if i < len(props_records) else {}
            for i in range(n)
        ]
    geoms = [mapping(g) for g in gdf_wgs84.geometry]
    extras = {k: list(v)[:n] for k, v in extra_props.items()}
    out = []
    for i in range(n):
        p = {
            key: (v.item() if isinstance(v, np.generic) else v)
            for key, v in props_records[i].items()
        }
        for k, vals in extras.items():
            p[k] = vals[i]
        out.append({"type": "Feature", "geometry": geoms[i], "properties": p})
    return out


def _bh_qvalues(p: "np.ndarray") -> "np.ndarray":
    """BH-FDR 校正的 q 值（G-6/#870）。

    逐点 p<0.05 的显著性判定在 n 个单元上独立检验时，随机数据期望产出
    ~0.05n 个假显著；q 值控制 FDR 后再判定"可断言热点"。
    """
    p = np.asarray(p, dtype=float)
    n = p.size
    if n == 0:
        return p
    nan_mask = np.isnan(p)
    p_clean = np.where(nan_mask, 1.0, p)
    order = np.argsort(p_clean)
    ranked = p_clean[order] * n / (np.arange(n) + 1)
    q_sorted = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n, dtype=float)
    out[order] = np.clip(q_sorted, 0.0, 1.0)
    out[nan_mask] = 1.0
    return out

def _build_weights(gdf: gpd.GeoDataFrame, k: int = 8) -> sparse.coo_matrix:
    """Build spatial weights matrix using KNN via cKDTree.

    Returns a sparse COO matrix (n×n) with 1.0 for K-nearest neighbors.
    Uses O(n log n) cKDTree query instead of O(n²) distance_matrix.

    Self-exclusion is explicit (E-4): the previous code assumed column 0 of
    the query result was always self and dropped it, but with duplicate
    coordinates a coincident point can tie-break ahead of self and land in
    column 0 — inserting a self-loop (w_ii=1) and dropping a real neighbour.
    We now drop the actual self column per row instead.
    """
    from scipy.spatial import cKDTree
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    n = len(coords)
    if n == 0:
        return sparse.coo_matrix((0, 0))
    if n == 1:
        # No neighbours possible; return a 1x1 zero matrix (avoids leaving the
        # cols array uninitialized — review finding).
        return sparse.coo_matrix((np.zeros(0), (np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))), shape=(1, 1))
    k_actual = min(k, n - 1)
    tree = cKDTree(coords)
    # Query one extra neighbour so dropping self always leaves k_actual.
    _, idx = tree.query(coords, k=k_actual + 1)
    # Vectorized self-exclusion (E-4): each row's k_actual+1 nearest contains
    # self exactly once (self is distance 0), so masking self out leaves
    # exactly k_actual neighbours per row, in distance order. Boolean-index
    # flattens row-major with column order preserved — O(n·k), no Python loop
    # (review G: the per-row loop was a 46x stage regression at n=10k).
    mask = idx != np.arange(n)[:, None]
    cols = idx[mask]  # row-major, per-row variable length
    # 评审 MAJOR-1：重合点簇 >k+1 时 tie-break 可能把 self 排出 k+1 邻域，
    # 逐行贡献数不再恒为 k_actual —— rows 从逐行计数派生（与
    # spatial_weights.build_knn_weights 同一修复）。
    per_row = mask.sum(axis=1)
    rows = np.repeat(np.arange(n), per_row)
    data = np.ones(len(rows), dtype=float)
    return sparse.coo_matrix((data, (rows, cols)), shape=(n, n))


# ── VNext（ADR-0099）全局空间自相关族的共享基础设施 ──────────────────
# Moran / Geary / General G 共用：权重方案分发、固定种子置换分布、
# 置换数契约。默认路径（knn, k=8, 99 perms, seed 42）与 #1002 时代的
# moran_i_narrated 逐位一致 —— 种子参考测试
# （test_moran_i_pvalue_matches_seeded_scalar_reference）钉住该流。

#: 允许的置换次数（参数契约 moran_i_analysis / geary_c_analysis / general_g_analysis）。
_PERMUTATION_CHOICES = (99, 199, 499, 999)
_PERMUTATION_SEED = 42


def _validate_permutations(permutations: int) -> int:
    """Constrain permutations to the contracted choice set {99,199,499,999}."""
    try:
        p = int(permutations)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"permutations must be one of {_PERMUTATION_CHOICES} "
            f"(got {permutations!r})") from exc
    if p not in _PERMUTATION_CHOICES:
        raise ValueError(
            f"permutations must be one of {_PERMUTATION_CHOICES} (got {permutations})")
    return p


def _autocorr_weights(
    gdf: gpd.GeoDataFrame,
    n: int,
    weights_scheme: str,
    k: int,
    distance_band: float,
):
    """Dispatch the weights scheme for the global autocorrelation family.

    Returns a row-standardized :class:`WeightsMatrix` (islands keep zero
    rows). queen/rook need polygonal units — anything else is an honest
    ``UnsupportedMethod`` with a correction hint, not a silent fallback.
    """
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    scheme = str(weights_scheme or "knn").lower()
    if scheme == "knn":
        # Same semantics as the historical default: binary kNN symmetrized by
        # union (#1002), then row-standardized.
        return build_knn_weights(coords, k=min(int(k), n - 1))
    if scheme in ("queen", "rook"):
        return build_contiguity_weights(gdf, scheme=scheme, row_standardized=True)
    if scheme == "distance_band":
        if distance_band and float(distance_band) > 0:
            threshold = float(distance_band)
        else:
            # E-7 auto-band rule (shared with hotspot_narrated): the 8-NN mean
            # distance keeps most observations connected.
            threshold = auto_band_8nn(coords)
        return build_distance_band_weights(
            coords, threshold=threshold, include_self=False,
            row_standardized=True,
        )
    raise ValueError(
        f"unknown weights_scheme {weights_scheme!r}; "
        f"expected one of {WEIGHT_SCHEMES}")


def _permutation_stats(
    stat_of_values: Callable[[np.ndarray], float],
    values: np.ndarray,
    perms: int,
) -> np.ndarray:
    """Fixed-seed (42) permutation reference distribution.

    ``stat_of_values`` receives a permuted copy of ``values`` and must apply
    the exact same arithmetic as the observed statistic. Vectorized over the
    sparse weight entries per draw — never materializes an n×perms matrix.
    """
    rng = np.random.default_rng(_PERMUTATION_SEED)
    out = np.empty(perms, dtype=float)
    for t in cancellable(range(perms)):
        out[t] = stat_of_values(rng.permutation(values))
    return out


def _two_sided_permutation_pvalue(
    perm_stats: np.ndarray, observed: float, expected: float, perms: int,
) -> float:
    """Two-sided permutation p with the +1 correction (E-8).

    The raw fraction can return exactly 0.0, implying certainty; the
    (count+1)/(perms+1) form bounds it away from zero.
    """
    deviations = np.abs(perm_stats - expected) >= np.abs(observed - expected)
    return (int(np.sum(deviations)) + 1) / (perms + 1)





def _filter_numeric_gdf(
    gdf: gpd.GeoDataFrame, value_field: str
) -> tuple[gpd.GeoDataFrame, np.ndarray] | None:
    """Return (gdf_filtered, values) aligned by row.

    Keeps only rows where ``value_field`` is a valid numeric value, applying the
    same coercion logic as :func:`_extract_numeric_values`. The returned gdf and
    values array are guaranteed to share the same length and row order, so they
    can be safely indexed together when building spatial weights.

    Returns None when the field is missing entirely.
    """
    if value_field not in gdf.columns:
        return None
    series = gdf[value_field]
    if not np.issubdtype(series.dtype, np.number):
        series = pd.to_numeric(series, errors="coerce")
    # Drop NaN AND non-finite (±inf) values: inf poisons mean/std and yields
    # NaN-laden autocorrelation statistics silently labelled significant
    # (audit E-11 / E-2 / E-6).
    valid_mask = series.notna() & np.isfinite(series.astype(float))
    gdf_valid = gdf[valid_mask].reset_index(drop=True)
    values = series[valid_mask].astype(float).values
    return gdf_valid, values

def calculate_sde(geojson: dict) -> GeoAnalysisResult:
    """
    Calculate the Standard Deviational Ellipse (SDE) for a set of points.
    Returns a GeoAnalysisResult with the ellipse polygon and a directional insight.
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        return GeoAnalysisResult(False, None, "Invalid input or no features found", error_type="ValueError")
    
    gdf, utm_crs = res
    if len(gdf) < 3:
        return GeoAnalysisResult(False, None, "At least 3 points required", error_type="InsufficientData")

    # Ensure we only work with point geometries for SDE
    points = gdf[gdf.geometry.type == 'Point']
    if len(points) < 3:
        # Try to use centroids if they aren't all points
        coords = extract_centroids(gdf)
    else:
        coords = extract_centroids(points)
        
    n = len(coords)
    mean_x, mean_y = coords.mean(axis=0)
    x_prime = coords[:, 0] - mean_x
    y_prime = coords[:, 1] - mean_y

    sum_x2 = np.sum(x_prime**2)
    sum_y2 = np.sum(y_prime**2)
    sum_xy = np.sum(x_prime * y_prime)

    # Angle calculation (Gi* degenerate branch: delta==0 with negative
    # covariance is the NW-SE diagonal, theta=-45°).
    delta = sum_x2 - sum_y2
    if delta == 0:
        if sum_xy > 0:
            theta = np.pi / 4
        elif sum_xy < 0:
            theta = -np.pi / 4
        else:
            theta = 0
    else:
        theta = 0.5 * np.arctan2(2 * sum_xy, delta)

    # Standard deviations along the rotated axes
    sin_t = np.sin(theta)
    cos_t = np.cos(theta)
    
    sigma_x_2 = 2 * np.sum((x_prime * cos_t + y_prime * sin_t)**2) / n
    sigma_y_2 = 2 * np.sum((x_prime * sin_t - y_prime * cos_t)**2) / n
    
    sigma_x = np.sqrt(max(sigma_x_2, 0))
    sigma_y = np.sqrt(max(sigma_y_2, 0))

    # Create ellipse polygon
    t = np.linspace(0, 2*np.pi, 100)
    ell_x = sigma_x * np.cos(t)
    ell_y = sigma_y * np.sin(t)
    
    rot_x = mean_x + ell_x * cos_t - ell_y * sin_t
    rot_y = mean_y + ell_x * sin_t + ell_y * cos_t
    
    ellipse_poly = Polygon(np.column_stack([rot_x, rot_y]))
    if not ellipse_poly.is_valid:
        ellipse_poly = ellipse_poly.buffer(0)
        
    ellipse_wgs84 = gpd.GeoSeries([ellipse_poly], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
    
    deg = np.degrees(theta) % 180
    if 67.5 <= deg < 112.5: 
        direction = "North-South"
    elif 22.5 <= deg < 67.5: 
        direction = "North-East to South-West"
    elif 112.5 <= deg < 157.5: 
        direction = "North-West to South-East"
    else: 
        direction = "East-West"
    
    area_km2 = ellipse_poly.area / 1e6
    summary = f"Directional Insight: The points show a clear {direction} directional trend, covering an area of {area_km2:.2f} sq km."
    
    center_wgs84 = gpd.GeoSeries([Point(float(mean_x), float(mean_y))], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
    data_out = {
        "type": "Feature",
        "geometry": mapping(ellipse_wgs84),
        "properties": {
            "center": [float(center_wgs84.x), float(center_wgs84.y)],
            "sigma_x": float(sigma_x),
            "sigma_y": float(sigma_y),
            "angle_deg": float(deg),
            "area_km2": float(area_km2),
            "direction": direction
        }
    }
    
    return GeoAnalysisResult(True, data_out, summary)

def moran_i_narrated(
    geojson: dict,
    value_field: str,
    weights_scheme: str = "knn",
    k: int = 8,
    distance_band: float = 0,
    permutations: int = 99,
) -> GeoAnalysisResult:
    """
    Global Moran's I spatial autocorrelation test with narrative summary.

    ``weights_scheme``（VNext 参数化，默认 ``knn`` 保持历史行为逐位不变）：

    - ``knn``: k 近邻二值权重，对称并集 + 行标准化（#1002 语义，k 默认 8）；
    - ``queen`` / ``rook``: 面要素邻接（libpysal），需要 Polygon 输入，
      点/线输入抛 ``UnsupportedMethod``；
    - ``distance_band``: 距离阈值二值权重（``distance_band`` 米，0 = 按
      8 近邻平均距离自动，E-7 规则）。

    ``permutations`` ∈ {99, 199, 499, 999}（固定种子 42，双侧
    (count+1)/(perms+1) 校正，E-8）。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        return GeoAnalysisResult(False, None, "Invalid GeoJSON or no features found")

    gdf, _ = res
    # BUG-01: drop non-numeric rows BEFORE building weights so the n×n weights
    # matrix is aligned with the (possibly shorter) values array.
    aligned = _filter_numeric_gdf(gdf, value_field)
    if aligned is None:
        return GeoAnalysisResult(False, None, f"Field '{value_field}' missing or non-numeric")
    gdf, values = aligned
    if len(values) == 0:
        return GeoAnalysisResult(False, None, f"Field '{value_field}' missing or non-numeric")

    n = len(values)
    if n < 3:
        return GeoAnalysisResult(False, None, "At least 3 features required for Moran's I")

    # Constant-value guard (E-2): a zero-variance field makes the Moran's I
    # denominator zero and previously produced I=0.0 / p=1.0 / "random" — a
    # fabricated result. (inf is already dropped by _filter_numeric_gdf.)
    if float(np.ptp(values)) == 0.0:
        return GeoAnalysisResult(
            False, None,
            f"All '{value_field}' values are identical; Moran's I is undefined.",
            error_type="ValueError",
        )

    perms = _validate_permutations(permutations)

    # #1002: KNN weights are directional (i being j's k-nearest does not imply
    # j is i's). Global Moran's I requires symmetric weights — otherwise it is
    # systematically biased vs the PySAL reference and inconsistent with the
    # symmetric Queen contiguity weights h3_lisa uses. The default (knn) path
    # symmetrizes by union (elementwise maximum), then row-standardizes —
    # exactly the historical op sequence, so the default stays bit-comparable.
    wm = _autocorr_weights(gdf, n, weights_scheme, k, distance_band)
    w = wm.matrix.tocoo()
    w_sum = float(w.sum())
    if w_sum == 0:
        return GeoAnalysisResult(False, None, "Spatial weights matrix is empty")

    z = values - values.mean()
    s0 = w_sum
    # Keep sparse: compute numerator only over non-zero weight pairs
    # (avoids O(n²) dense outer product that would negate cKDTree benefit)
    w_vals = w.data
    i_idx = w.row
    j_idx = w.col
    numerator = float(np.sum(w_vals * z[i_idx] * z[j_idx]))
    denominator = np.sum(z**2)

    moran_i_val = (n / s0) * (numerator / denominator) if denominator > 0 else 0
    expected_i = -1.0 / (n - 1)

    # Simplified permutation test for p-value (fixed seed 42, E-8 +1
    # correction, two-sided |I_perm − E| ≥ |I_obs − E|). The stat callable
    # replays the historical per-draw arithmetic verbatim so the default RNG
    # stream and float results are unchanged.
    def _moran_stat(pv: np.ndarray) -> float:
        pz = pv - pv.mean()
        p_num = np.sum(w_vals * pz[i_idx] * pz[j_idx])
        p_den = np.sum(pz**2)
        return (n / s0) * (p_num / p_den) if p_den > 0 else 0

    perm_is = _permutation_stats(_moran_stat, values, perms)
    p_value = _two_sided_permutation_pvalue(perm_is, moran_i_val, expected_i, perms)

    if p_value < 0.05:
        pattern = "clustering" if moran_i_val > expected_i else "dispersion"
    else:
        pattern = "random"

    if pattern == "clustering":
        narrative = f"There is a statistically significant clustering of {value_field} values (Moran's I: {moran_i_val:.4f}, p = {p_value:.4f}). Similar values tend to be near each other."
    elif pattern == "dispersion":
        narrative = f"There is a statistically significant spatial dispersion of {value_field} values (Moran's I: {moran_i_val:.4f}, p = {p_value:.4f}). High and low values tend to be alternated."
    else:
        narrative = f"The distribution of {value_field} appears to be spatially random (Moran's I: {moran_i_val:.4f}, p = {p_value:.4f}). No clear spatial pattern was detected."

    data_out = {
        "moran_i": float(moran_i_val),
        "expected_i": float(expected_i),
        "p_value": float(p_value),
        "pattern": pattern,
        "n_features": n,
        # VNext 披露：置换数 / 权重方案元数据 / 类型化不确定性块。
        "permutations": perms,
        "weights": wm.metadata(),
        "uncertainty": StatisticalSignificance(
            target="morans_i",
            statistic_name="Moran's I",
            statistic_value=float(moran_i_val),
            p_value=float(p_value),
            method="permutation",
            permutations=perms,
            alternative="two-sided",
        ).to_evidence(),
    }

    return GeoAnalysisResult(True, data_out, narrative)


def geary_c_narrated(
    geojson: dict,
    value_field: str,
    weights_scheme: str = "knn",
    k: int = 8,
    distance_band: float = 0,
    permutations: int = 99,
    analytic_variance: bool = False,
) -> GeoAnalysisResult:
    """Global Geary's C spatial autocorrelation (Geary 1954).

    C = (n−1)·Σᵢⱼ wᵢⱼ(xᵢ−xⱼ)² / (2·S₀·Σᵢ(zᵢ)²)（行标准化权重；esda.Geary
    同式）。C < 1 正自相关（相似值邻接），C > 1 负自相关（checkerboard →
    C = 2 − 2/n）。置换推断与 Moran 同策略：固定种子 42、双侧
    (count+1)/(perms+1)；``analytic_variance=True`` 追加正态假设下的解析
    方差 / z / p（Cliff-Ord 公式，与 esda.Geary.VC_norm 一致）。

    科学性失败抛类型化错误（InsufficientSamples / DegenerateData /
    MissingRequiredField / NoValidObservations / UnsupportedMethod），
    correction_hint 随错误传递。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection with at least 3 numeric features",
        )

    gdf, _ = res
    aligned = _filter_numeric_gdf(gdf, value_field)
    if aligned is None or len(aligned[1]) == 0:
        raise MissingRequiredField(
            f"field '{value_field}' is missing or non-numeric",
            correction_hint=f"provide a numeric property '{value_field}' on every feature",
        )
    gdf, values = aligned

    n = len(values)
    if n < 3:
        raise InsufficientSamples(
            f"Geary's C needs at least 3 valid numeric features (got {n})",
            correction_hint="add observations or use a method valid at this sample size",
        )
    # 与 Moran E-2 同理：零方差 → 统计量无定义（分母为 0）。
    if float(np.ptp(values)) == 0.0:
        raise DegenerateData(
            f"all '{value_field}' values are identical; Geary's C is undefined",
            correction_hint="check the numeric field for constant values or coincident samples",
        )

    perms = _validate_permutations(permutations)
    wm = _autocorr_weights(gdf, n, weights_scheme, k, distance_band)
    if wm.s0 == 0:
        raise DegenerateData(
            "spatial weights matrix is empty (every observation is an island)",
            correction_hint="increase the distance band / k, or check geometry connectivity",
        )

    w = wm.matrix.tocoo()
    w_vals, i_idx, j_idx = w.data, w.row, w.col
    s0 = float(w.sum())

    z = values - values.mean()
    denominator = float(np.sum(z**2))
    num = float(np.sum(w_vals * (values[i_idx] - values[j_idx]) ** 2))
    geary_c_val = (n - 1) * num / (2.0 * s0 * denominator)
    expected_c = 1.0

    def _geary_stat(pv: np.ndarray) -> float:
        pz = pv - pv.mean()
        p_num = np.sum(w_vals * (pv[i_idx] - pv[j_idx]) ** 2)
        p_den = np.sum(pz**2)
        return (n - 1) * p_num / (2.0 * s0 * p_den) if p_den > 0 else 0.0

    perm_cs = _permutation_stats(_geary_stat, values, perms)
    p_value = _two_sided_permutation_pvalue(perm_cs, geary_c_val, expected_c, perms)

    # 可选解析方差（正态假设，Cliff-Ord / esda.Geary.VC_norm 同式）：
    # VC_norm = [ (2S₁ + S₂)(n−1) − 4S₀² ] / (2(n+1)S₀²)。
    analytic: dict | None = None
    if analytic_variance:
        w_csr = wm.matrix.tocsr()
        s1 = 0.5 * float(((w_csr + w_csr.transpose()).data ** 2).sum())
        row_sums = np.asarray(w_csr.sum(axis=1)).ravel()
        col_sums = np.asarray(w_csr.sum(axis=0)).ravel()
        s2 = float(np.sum((row_sums + col_sums) ** 2))
        vc_norm = (
            (2.0 * s1 + s2) * (n - 1) - 4.0 * s0 * s0
        ) / (2.0 * (n + 1) * s0 * s0)
        if vc_norm > 0:
            z_norm = (geary_c_val - expected_c) / np.sqrt(vc_norm)
            analytic = {
                "variance_norm": float(vc_norm),
                "z_norm": float(z_norm),
                "p_norm": float(2.0 * norm.sf(abs(z_norm))),
                "method": "analytic variance under normality (Cliff & Ord 1981)",
            }

    if p_value < 0.05:
        pattern = "clustering" if geary_c_val < expected_c else "dispersion"
    else:
        pattern = "random"

    if pattern == "clustering":
        narrative = (
            f"There is a statistically significant clustering of {value_field} values "
            f"(Geary's C: {geary_c_val:.4f}, expected 1.0, p = {p_value:.4f}). "
            f"解读：C<1 且显著 —— 相似值（高-高 / 低-低）在空间上邻接聚集。"
        )
    elif pattern == "dispersion":
        narrative = (
            f"There is a statistically significant spatial dispersion of {value_field} values "
            f"(Geary's C: {geary_c_val:.4f}, expected 1.0, p = {p_value:.4f}). "
            f"解读：C>1 且显著 —— 高低值交替（棋盘式负自相关）。"
        )
    else:
        narrative = (
            f"The distribution of {value_field} appears to be spatially random "
            f"(Geary's C: {geary_c_val:.4f}, expected 1.0, p = {p_value:.4f}). "
            f"解读：未检测到显著空间自相关。"
        )

    data_out = {
        "gearys_c": float(geary_c_val),
        "expected_c": float(expected_c),
        "p_value": float(p_value),
        "pattern": pattern,
        "n_features": n,
        "permutations": perms,
        "weights": wm.metadata(),
        "uncertainty": StatisticalSignificance(
            target="gearys_c",
            statistic_name="Geary's C",
            statistic_value=float(geary_c_val),
            p_value=float(p_value),
            method="permutation",
            permutations=perms,
            alternative="two-sided",
        ).to_evidence(),
    }
    if analytic is not None:
        data_out["analytic_variance"] = analytic

    return GeoAnalysisResult(True, data_out, narrative)


def general_g_narrated(
    geojson: dict,
    value_field: str,
    distance_band: float = 0,
    permutations: int = 99,
) -> GeoAnalysisResult:
    """Getis-Ord General G（Ord & Getis 1995）——高值聚集的全局检验。

    G = Σᵢ≠ⱼ wᵢⱼxᵢxⱼ / Σᵢ≠ⱼ xᵢxⱼ（二值距离阈值权重，w_ii=0）。G 显著
    高于期望 S₀/(n(n−1)) = 高值与高值邻接聚集（clustered-high）；显著低于
    期望 = 低值聚集 / 高值分散（clustered-low）——**不是**"无聚集"的镜像
    陈述，披露在叙事里。值必须非负（计数/强度语义）；负值抛
    UnsupportedMethod。置换推断：固定种子 42，双侧 min 侧翻倍。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection with at least 3 numeric features",
        )

    gdf, _ = res
    aligned = _filter_numeric_gdf(gdf, value_field)
    if aligned is None or len(aligned[1]) == 0:
        raise MissingRequiredField(
            f"field '{value_field}' is missing or non-numeric",
            correction_hint=f"provide a numeric property '{value_field}' on every feature",
        )
    gdf, values = aligned

    n = len(values)
    if n < 3:
        raise InsufficientSamples(
            f"General G needs at least 3 valid numeric features (got {n})",
            correction_hint="add observations or use a method valid at this sample size",
        )
    if float(np.min(values)) < 0:
        raise UnsupportedMethod(
            "General G requires non-negative values "
            f"(min of '{value_field}' is {float(np.min(values)):.4g})",
            correction_hint=(
                "shift the field to non-negative (add the |min|), or use "
                "moran_i / geary_c which accept signed values"
            ),
        )
    if float(np.sum(values)) == 0.0:
        raise DegenerateData(
            f"all '{value_field}' values are zero; General G is undefined (0/0)",
            correction_hint="check the numeric field for constant zero values",
        )

    perms = _validate_permutations(permutations)
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    if distance_band and float(distance_band) > 0:
        threshold = float(distance_band)
    else:
        threshold = auto_band_8nn(coords)  # E-7 auto-band rule
    wm = build_distance_band_weights(
        coords, threshold=threshold, include_self=False, row_standardized=False)
    if wm.s0 == 0:
        raise DegenerateData(
            f"no neighbour pairs within the distance band ({threshold:.1f} m)",
            correction_hint="increase distance_band or check coordinate units (metres expected)",
        )

    w = wm.matrix.tocoo()
    w_vals, i_idx, j_idx = w.data, w.row, w.col
    s0 = wm.s0
    # Σ_{i≠j} x_i x_j = (Σx)² − Σx² —— 置换不变量，一次预计算。
    den_sum = float(np.sum(values) ** 2 - np.sum(values**2))
    if den_sum <= 0:
        raise DegenerateData(
            "General G denominator Σ_{i≠j} x_i x_j ≤ 0 (single nonzero value?)",
            correction_hint="General G needs at least two nonzero observations",
        )

    g_val = float(np.sum(w_vals * values[i_idx] * values[j_idx]) / den_sum)
    expected_g = s0 / (n * (n - 1))

    def _g_stat(pv: np.ndarray) -> float:
        return float(np.sum(w_vals * pv[i_idx] * pv[j_idx]) / den_sum)

    perm_gs = _permutation_stats(_g_stat, values, perms)
    # 双侧（min 侧翻倍，含 +1 校正）：G 偏高 / 偏低都是对 CSR 的偏离。
    p_greater = (int(np.sum(perm_gs >= g_val)) + 1) / (perms + 1)
    p_smaller = (int(np.sum(perm_gs <= g_val)) + 1) / (perms + 1)
    p_value = min(1.0, 2.0 * min(p_greater, p_smaller))

    if p_value < 0.05:
        pattern = "clustered_high" if g_val > expected_g else "clustered_low"
    else:
        pattern = "random"

    if pattern == "clustered_high":
        narrative = (
            f"High values of {value_field} cluster together "
            f"(General G: {g_val:.4f}, expected {expected_g:.4f}, p = {p_value:.4f}). "
            f"解读：G 显著偏高 —— 高值彼此邻接形成高值聚集区（clustered-high）。"
        )
    elif pattern == "clustered_low":
        narrative = (
            f"Low values of {value_field} cluster together "
            f"(General G: {g_val:.4f}, expected {expected_g:.4f}, p = {p_value:.4f}). "
            f"解读：G 显著偏低 —— 低值彼此邻接（clustered-low）；等价地，高值被"
            f"彼此隔开，不可解读为『高值聚集』。"
        )
    else:
        narrative = (
            f"No significant clustering of {value_field} values "
            f"(General G: {g_val:.4f}, expected {expected_g:.4f}, p = {p_value:.4f}). "
            f"解读：与完全空间随机（CSR）无显著差异。"
        )

    data_out = {
        "general_g": float(g_val),
        "expected_g": float(expected_g),
        "p_value": float(p_value),
        "pattern": pattern,
        "n_features": n,
        "permutations": perms,
        "distance_band_m": round(float(threshold), 2),
        "weights": wm.metadata(),
        "uncertainty": StatisticalSignificance(
            target="general_g",
            statistic_name="General G",
            statistic_value=float(g_val),
            p_value=float(p_value),
            method="permutation",
            permutations=perms,
            alternative="two-sided",
        ).to_evidence(),
    }

    return GeoAnalysisResult(True, data_out, narrative)

#: Gi* 置换显著性路径的规模上限（999 次 × O(nnz) 条件随机化的内存/耗时
#: 防线；超限先抛 ResourceScaleMismatch，不做赌博）。
HOTSPOT_PERMUTATION_MAX_N = 5000


def hotspot_narrated(
    geojson: dict,
    value_field: str,
    distance_band: float = 0,
    significance_method: str = "normal",
    permutations: int = 999,
) -> GeoAnalysisResult:
    """
    Getis-Ord Gi* local spatial autocorrelation (hotspot analysis) with narrative summary.

    ``significance_method``（Foundation V3，additive；默认 "normal" 与既有
    行为逐位一致）：normal=解析正态 p（既有路径，输出键不变）；
    permutation=条件随机化置换 p（固定种子 42，(count+1)/(perms+1)，n≤5000
    守卫）—— 输出在既有键之上附加 p_value_permutation /
    p_value_normal / significance_method / permutations。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        return GeoAnalysisResult(False, None, "Invalid GeoJSON or no features found")

    gdf, utm_crs = res
    # BUG-01: drop non-numeric rows BEFORE deriving coords/values so the gdf,
    # coords, and values arrays are all aligned (no IndexError / wrong results).
    aligned = _filter_numeric_gdf(gdf, value_field)
    if aligned is None:
        return GeoAnalysisResult(False, None, f"Field '{value_field}' missing or non-numeric")
    gdf, values = aligned
    if len(values) == 0:
        return GeoAnalysisResult(False, None, f"Field '{value_field}' missing or non-numeric")

    n = len(values)
    if n < 3:
        return GeoAnalysisResult(False, None, "At least 3 features required for hotspot analysis")

    # Foundation V3：显著性方法（normal=既有解析路径，行为逐位不变）
    sig_method = str(significance_method or "normal").lower()
    if sig_method not in ("normal", "permutation"):
        raise ValueError(
            "significance_method must be 'normal' or 'permutation' "
            f"(got {significance_method!r})")

    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    
    if distance_band <= 0:
        # Auto-calculate distance band using the k-th nearest-neighbour
        # distance (E-7): the mean 1st-NN distance is the scale where each
        # point has ~1 neighbour, leaving ~half the points disconnected and
        # silently finding no hotspots on clustered data. The 8th-NN band
        # ensures most points have several neighbours.
        from scipy.spatial import cKDTree
        tree = cKDTree(coords)
        k_band = min(8, n - 1)
        nn_dist, _ = tree.query(coords, k=k_band + 1)
        bw = float(nn_dist[:, k_band].mean())
        if bw <= 0:
            bw = 1.0
    else:
        bw = distance_band
    
    # Build binary weights matrix using cKDTree sparse distance matrix.
    # #385: keep the weights sparse end-to-end. The old code densified the
    # COO into an n×n float64 array (~8·n² bytes: 800MB at 10k features,
    # 7.2GB at 30k — worker OOM). Getis-Ord only needs w @ values, row sums,
    # and squared row sums, all natively supported by CSR.
    from scipy.spatial import cKDTree
    tree = cKDTree(coords)
    binary_weights_coo = tree.sparse_distance_matrix(tree, max_distance=bw, output_type="coo_matrix")
    # Gi* (Getis-Ord) requires w_ii = 1 (include self). sparse_distance_matrix
    # includes all (i,i) self pairs at distance 0, so keep the full matrix
    # (binary 1 for every pair within the band, including the diagonal).
    w = sparse.csr_matrix(
        (np.ones(len(binary_weights_coo.data)), (binary_weights_coo.row, binary_weights_coo.col)),
        shape=(n, n),
    )
    
    x_bar = values.mean()
    s = values.std(ddof=0)
    if s == 0:
        return GeoAnalysisResult(False, None, "All values are identical, cannot perform hotspot analysis")
    
    # Vectorized Gi* computation (audit S40: O(n) instead of O(n) Python loop).
    # All reductions stay in sparse form (#385): CSR row sums, elementwise
    # square (binary weights, so w² == w — kept general for clarity), and the
    # sparse matvec w @ values. Each yields an (n,) array.
    sum_wi = np.asarray(w.sum(axis=1)).ravel()
    sum_wi2 = np.asarray(w.multiply(w).sum(axis=1)).ravel()
    numerators = np.asarray(w @ values).ravel() - x_bar * sum_wi
    denom_inners = (n * sum_wi2 - sum_wi**2) / (n - 1)
    denominators = np.where(denom_inners > 0, s * np.sqrt(denom_inners), 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        gi_stars = np.where(denominators != 0, numerators / denominators, 0)
    # MINOR-4（科学评审）：解析 p 在 |Gi*| 极大时下溢为精确 0 ——
    # 分支的 E-8「永不精确零」哲学同样适用于解析路径。
    _TINY_P = 1e-16
    p_normal = np.maximum(2 * (1 - norm.cdf(np.abs(gi_stars))), _TINY_P)
    p_vals = p_normal
    p_value_permutation: Optional[np.ndarray] = None
    perms_used = 0
    if sig_method == "permutation":
        # Foundation V3：条件随机化显著性（固定种子 42，双侧 (count+1)/
        # (perms+1)）。与 esda crand 同一条件化哲学：全局矩（x̄、s）取观测值
        # 固定不动，只随机化邻域取值 —— 置换向量的自身贡献被还原为 y_i
        # （Gi* 的核心条件），邻居样本是无放回抽取（多重集组成与严格的
        # y_{−i} 抽样差一项，Monte-Carlo 近似，披露于叙事）。二值权重
        # w_ii=1 ⇒ (W·pv)_i − pv_i + y_i 即条件化邻域和。规模守卫先行。
        if n > HOTSPOT_PERMUTATION_MAX_N:
            raise ResourceScaleMismatch(
                f"Gi* permutation significance needs {permutations} "
                f"conditional randomizations at n={n}",
                estimated=f"{permutations} × O(nnz) sparse matvecs at n={n}",
                limit=f"n ≤ {HOTSPOT_PERMUTATION_MAX_N}",
                correction_hint="use significance_method='normal' (analytic "
                                "normal approximation), or aggregate the data",
            )
        perms_used = _validate_permutations(permutations)
        rng = np.random.default_rng(_PERMUTATION_SEED)
        extreme = np.zeros(n, dtype=np.int64)
        obs_abs = np.abs(gi_stars)
        for _ in cancellable(range(perms_used)):
            pv = rng.permutation(values)
            wpv = np.asarray(w @ pv).ravel()
            numerators_p = wpv - pv + values - x_bar * sum_wi
            gz = np.where(denominators != 0,
                          numerators_p / np.where(denominators != 0,
                                                  denominators, 1.0), 0)
            extreme += np.abs(gz) >= obs_abs
        p_value_permutation = (extreme + 1) / (perms_used + 1)
        p_vals = p_value_permutation

    # G-6（#870）：BH-FDR 校正 —— n 个单元各按 α=0.05 独立检验时，完全
    # 随机数据也期望产出 0.05×n 个"显著"热点并直接上图。q 值随要素输出，
    # 信封披露期望假阳性数；hot_count 不再高于期望假阳性时叙事降级。
    q_vals = _bh_qvalues(p_vals)
    expected_false_pos = round(0.05 * len(p_vals), 1)
    fdr_hot_count = int(np.sum((q_vals < 0.05) & (gi_stars > 0)))

    hot_count = int(np.sum((p_vals < 0.05) & (gi_stars > 0)))
    cold_count = int(np.sum((p_vals < 0.05) & (gi_stars < 0)))
    
    # Batch reproject once (audit S40: O(1) instead of O(n) CRS transforms)
    gdf_wgs84 = gdf.to_crs("EPSG:4326")
    
    # Vectorized hotspot type / confidence classification (audit S40: same
    # p-value thresholds 0.05/0.01/0.1, same gi_star sign logic, same priority
    # order, same confidence tiers as the scalar spec)
    sig_mask = p_vals < 0.05
    borderline_mask = p_vals < 0.1
    hot_mask = gi_stars > 0
    hotspot_types = np.select(
        [sig_mask & hot_mask, sig_mask & ~hot_mask,
         borderline_mask & hot_mask, borderline_mask & ~hot_mask],
        ["Hot Spot", "Cold Spot", "Hot Spot", "Cold Spot"],
        default="Not Significant",
    ).tolist()
    confidences = np.select(
        [sig_mask & (p_vals < 0.01), sig_mask, borderline_mask],
        ["99%", "95%", "90%"],
        default="Not Significant",
    ).tolist()

    extra_props = {
        "gi_star": [round(float(v), 4) for v in gi_stars],
        "p_value": [round(float(v), 6) for v in p_vals],
        "q_value_fdr": [round(float(v), 6) for v in q_vals],
        "hotspot_type": hotspot_types,
        "confidence": confidences,
    }
    if p_value_permutation is not None:
        # additive：置换路径附加解析对照 p 与方法标记（normal 路径不带）
        extra_props["p_value_permutation"] = [
            round(float(v), 6) for v in p_value_permutation]
        extra_props["p_value_normal"] = [
            round(float(v), 6) for v in p_normal]
    features = _assemble_features(gdf_wgs84, extra_props)
        
    summary = f"Hotspot analysis identified {hot_count} statistically significant hot spots and {cold_count} cold spots."
    if hot_count > 0 or cold_count > 0:
        summary += f" Significant clusters of high/low values were detected using a distance band of {bw:.1f} meters."
    else:
        summary += " No significant hotspots were detected at the 90% confidence level."
    # G-6（#870）：多重比较披露 —— 未校正显著数接近随机期望时降级叙述。
    summary += (
        f"（BH-FDR 校正后 {fdr_hot_count} 个热点 q<0.05；"
        f"未校正 α=0.05 下 {len(p_vals)} 个单元的随机期望假阳性 ≈{expected_false_pos} 个）"
    )
    if hot_count > 0 and hot_count <= expected_false_pos:
        summary += " 显著数不高于随机期望，热点结论不可靠，请谨慎叙述。"

    data_out = {
        "type": "FeatureCollection",
        "features": features,
        "hot_spots_count": hot_count,
        "cold_spots_count": cold_count,
        "distance_band_m": round(bw, 2),
        "fdr_hot_spots_count": fdr_hot_count,
        "expected_false_positives": expected_false_pos,
    }
    if p_value_permutation is not None:
        # additive（仅置换路径）：方法与置换元数据进载荷
        data_out["significance_method"] = "permutation"
        data_out["permutations"] = perms_used

    # 审计 F-3：descriptor 声明 statistical_significance —— 证据块真实落
    # data_out（p 值/置换数/多重校正真实填充；块级统计量取最强局地检验
    # max|Gi*| 与其 min-p 配对，逐格 p/q 已在 feature properties）。
    data_out["uncertainty"] = [StatisticalSignificance(
        target="gi_star_local",
        statistic_name="Getis-Ord Gi*",
        statistic_value=float(np.max(np.abs(gi_stars))),
        p_value=float(np.min(p_vals)),
        method=("permutation" if p_value_permutation is not None
                else "analytic_normal"),
        permutations=(perms_used if p_value_permutation is not None else None),
        multiple_testing="BH-FDR",
        alternative="two-sided",
    ).to_evidence()]

    summary = f"Hotspot analysis identified {hot_count} statistically significant hot spots and {cold_count} cold spots."
    if hot_count > 0 or cold_count > 0:
        summary += f" Significant clusters of high/low values were detected using a distance band of {bw:.1f} meters."
    else:
        summary += " No significant hotspots were detected at the 90% confidence level."
    # G-6（#870）：多重比较披露 —— 未校正显著数接近随机期望时降级叙述。
    summary += (
        f"（BH-FDR 校正后 {fdr_hot_count} 个热点 q<0.05；"
        f"未校正 α=0.05 下 {len(p_vals)} 个单元的随机期望假阳性 ≈{expected_false_pos} 个）"
    )
    if sig_method == "permutation":
        summary += (
            f" 显著性用条件随机化置换 p（{perms_used} 次、固定种子 42，"
            "解析正态 p 一并给出对照）。")
    if hot_count > 0 and hot_count <= expected_false_pos:
        summary += " 显著数不高于随机期望，热点结论不可靠，请谨慎叙述。"

    return GeoAnalysisResult(True, data_out, summary)

def calculate_nearest(geojson: dict) -> GeoAnalysisResult:
    """Nearest neighbor analysis with narrative summary (O(n log n) via cKDTree).

    Returns {mean_nearest_distance, expected, R, pattern} plus aliases
    {mean_distance, r_ratio} and extras {std_distance, min/max_distance}
    for backwards compatibility. mean_nearest_distance == mean_distance,
    expected is the CSR expectation, R == r_ratio.

    Foundation V2 (A3): additive Clark-Evans normal-approximation test —
    {nni_z, nni_p_value, nni_method} with SE(mean NN distance) =
    sqrt((4−π)/(4πnρ)), ρ = n/A (bbox). Two-sided p via erfc; the test is
    additive — existing contract keys are unchanged. Under a degenerate
    (zero-area) window the z-test is unavailable: nni_z/nni_p_value are
    None and nni_test_note discloses it.
    """
    from scipy.spatial import cKDTree
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        return GeoAnalysisResult(False, None, "Invalid input or no features found")

    gdf, working_crs = res
    if len(gdf) < 2:
        return GeoAnalysisResult(False, None, "At least 2 points required for nearest neighbor analysis")

    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    tree = cKDTree(coords)
    nn_dist, _ = tree.query(coords, k=2)  # k=1 is self (dist=0), k=2 is true nearest neighbor
    nn_dist = nn_dist[:, 1]

    mean_dist = float(nn_dist.mean())
    std_dist = float(nn_dist.std())
    n_points = len(coords)

    # Simple pattern recognition
    # Expected mean distance for random distribution (Poisson process)
    # R = Observed / Expected
    # Expected = 0.5 * sqrt(Area / N)
    xmin, ymin, xmax, ymax = gdf.total_bounds
    area = (xmax - xmin) * (ymax - ymin)
    expected_mean = 0.5 * np.sqrt(area / n_points)
    if expected_mean == 0 or mean_dist == 0:
        r_ratio = 0.0
    else:
        r_ratio = mean_dist / expected_mean

    # Clark-Evans (1954) normal approximation: z = (R̄ − E(R̄))/σ with
    # E(mean NN dist) = 1/(2√ρ), SE = sqrt((4−π)/(4πnρ)), ρ = n/A.
    nni_z: Optional[float] = None
    nni_p: Optional[float] = None
    nni_test_note = ""
    if area > 0:
        se = float(np.sqrt((4.0 - np.pi) / (4.0 * np.pi * n_points * (n_points / area))))
        if se > 0:
            nni_z = float((mean_dist - expected_mean) / se)
            nni_p = float(math.erfc(abs(nni_z) / math.sqrt(2.0)))  # two-sided
        else:
            nni_test_note = "Clark-Evans SE degenerated to 0; z-test unavailable"
    else:
        nni_test_note = (
            "degenerate window (zero bbox area): Clark-Evans z-test unavailable")

    pattern = "random"
    if r_ratio < 0.7:
        pattern = "clustered"
    elif r_ratio > 1.3:
        pattern = "dispersed"

    summary = f"Nearest Neighbor Insight: The mean distance to the nearest neighbor is {mean_dist:.2f} meters. The distribution pattern appears to be {pattern} (R ratio: {r_ratio:.2f})."
    if nni_z is not None:
        summary += (
            f" Clark-Evans normal approximation: z={nni_z:.3f}, "
            f"two-sided p={nni_p:.4f} (small samples / edge effects bias this test)."
        )

    data = {
        # Contract keys (docstring / ticket 9): mean_nearest_distance, expected, R
        "mean_nearest_distance": mean_dist,
        "expected": float(expected_mean),
        "R": r_ratio,
        # Aliases kept for backwards compatibility
        "mean_distance": mean_dist,
        "r_ratio": r_ratio,
        # Extras
        "std_distance": std_dist,
        "min_distance": float(nn_dist.min()),
        "max_distance": float(nn_dist.max()),
        "pattern": pattern,
        # Foundation V2 (A3): Clark-Evans normal-approximation test (additive)
        "nni_method": "clark_evans_normal_approx",
        "nni_z": nni_z,
        "nni_p_value": nni_p,
    }
    if nni_test_note:
        data["nni_test_note"] = nni_test_note
    from app.lib.geo_analysis.evidence import build_quality_evidence

    evidence_extra = {
        "pattern": pattern,
        "r_ratio": round(float(r_ratio), 6),
        "nni_method": "clark_evans_normal_approx",
    }
    if nni_z is not None:
        evidence_extra["nni_z"] = round(float(nni_z), 6)
        evidence_extra["nni_p_value"] = round(float(nni_p), 6)

    return GeoAnalysisResult(
        True, data, summary,
        evidence=build_quality_evidence(
            input_count=len(gdf),
            working_crs=str(working_crs),
            extra=evidence_extra,
        ),
    )

def calculate_central_feature(geojson: dict, method: str = "mean_center") -> GeoAnalysisResult:
    """Find the central feature or mean center."""
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        return GeoAnalysisResult(False, None, "Invalid input or no features found")
    
    gdf, utm_crs = res
    coords = extract_centroids(gdf)
    
    if method == "mean_center":
        mc = coords.mean(axis=0)
        center_pt = Point(mc[0], mc[1])
        summary = f"Mean Center: The average geographic center is at {mc[0]:.2f}, {mc[1]:.2f} (UTM)."
    else:
        # Central Feature: point with minimum total distance to all other points
        # Use batched cKDTree queries to avoid O(n²) memory allocation.
        from scipy.spatial import cKDTree
        n = len(coords)
        # Guard: central_feature requires all-pairs distances; cap at 5000 features
        if n > 5000:
            return GeoAnalysisResult(
                False, None,
                f"Too many features ({n}) for central_feature analysis (max 5000). Use mean_center instead.",
                error_type="InsufficientData",
            )
        tree = cKDTree(coords)
        # Batch query: avoid allocating full n×n distance matrix at once
        batch_size = 500
        dist_sums = np.zeros(n)
        for start in range(0, n, batch_size):
            end = min(start + batch_size, n)
            dists, _ = tree.query(coords[start:end], k=n)
            # #383: rank 0 is the self-match (the query point is itself in the
            # tree); columns are neighbor ranks, NOT point indices. Sum ranks
            # 1..k. The old code zeroed dists[row, global_point_index] — a
            # real neighbor's distance — corrupting every row for n ≥ 2.
            dist_sums[start:end] = dists[:, 1:].sum(axis=1)
        idx = int(np.argmin(dist_sums))
        center_pt = gdf.geometry.iloc[idx]
        summary = f"Central Feature: The feature at index {idx} is identified as the central feature (minimum total distance to others)."
        
    center_wgs84 = gpd.GeoSeries([center_pt], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
    data = {
        "type": "Feature",
        "geometry": mapping(center_wgs84),
        "properties": {"method": method, "summary": summary}
    }
    return GeoAnalysisResult(True, data, summary)

def cluster_narrated(
    geojson: dict,
    method: str = "dbscan",
    n_clusters: int = 5,
    eps: float = 1000,
    min_samples: int = 5,
    value_field: str = "",
    value_weight: float = 1.0,
) -> GeoAnalysisResult:
    """
    Perform spatial clustering (DBSCAN or K-Means) with narrative summary.

    value_weight scales the standardized value dimension relative to metric
    coords when value_field is set. Default 1.0 is conservative (equal weight);
    callers doing unit-aware blending should tune it explicitly. The weight is
    reported in the result so downstream consumers know the mixing semantics.
    """
    try:
        from sklearn.cluster import DBSCAN, KMeans
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return GeoAnalysisResult(False, None, "scikit-learn not installed")

    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        return GeoAnalysisResult(False, None, "Invalid input or no features found")
    
    gdf, utm_crs = res
    if len(gdf) < 3:
        return GeoAnalysisResult(False, None, "At least 3 features required for clustering")

    coords = extract_centroids(gdf)

    if value_field:
        filtered_gdf = _filter_numeric_gdf(gdf, value_field)
        # GIS-12: _filter_numeric_gdf returns None (field missing) or a
        # (gdf, values) tuple — never an empty GeoDataFrame. The previous
        # `filtered_gdf.empty` check raised AttributeError on both shapes
        # (None.empty / tuple.empty), masking the helpful error and crashing.
        # It can also return a 0-row tuple when the field is all-null.
        if filtered_gdf is None or len(filtered_gdf[0]) == 0:
            return GeoAnalysisResult(
                False, None, f"Field '{value_field}' is not numeric or contains only nulls"
            )
        gdf, _ = filtered_gdf
        coords = extract_centroids(gdf)
        vals = gdf[value_field].to_numpy(dtype=float)
        scaler = StandardScaler()
        # G-3（#867）：值维缩放到与坐标（米）可比的尺度。此前标准化值维
        # （σ=1，无量纲）与 UTM 米坐标直接拼接，默认 value_weight=1.0 下
        # 值维贡献仅 ~1 米（城市坐标 σ≈8-20km），"值感知聚类"退化为纯空间
        # 聚类且无披露。现在值维 σ = value_weight × 空间坐标 σ ——
        # value_weight=1 表示值维与空间维同量级参与距离。
        coords_std = float(np.std(coords, axis=0).mean()) if len(coords) else 0.0
        vals_scaled = scaler.fit_transform(vals.reshape(-1, 1)) * (
            float(value_weight) * max(coords_std, 1.0)
        )
        features = np.column_stack([coords, vals_scaled])
        value_effective_scale = float(value_weight) * max(coords_std, 1.0)
    else:
        features = coords
        value_effective_scale = None

    if method == "kmeans":
        # Guard (E-10): n_clusters<=0 or > n raises an opaque sklearn error.
        nk = max(1, min(int(n_clusters or 1), len(gdf)))
        model = KMeans(n_clusters=nk, random_state=42, n_init=10)
        labels = model.fit_predict(features)
        summary = f"K-Means clustering identified {len(set(labels))} groups."
    else:
        if eps <= 0 or min_samples <= 0:
            return GeoAnalysisResult(
                False, None,
                f"DBSCAN requires eps>0 and min_samples>0 (got eps={eps}, min_samples={min_samples})",
                error_type="ValueError",
            )
        model = DBSCAN(eps=eps, min_samples=min_samples)
        labels = model.fit_predict(features)
        n_clusters_found = len(set(labels)) - (1 if -1 in labels else 0)
        n_noise = list(labels).count(-1)
        summary = f"DBSCAN identified {n_clusters_found} clusters and {n_noise} noise points."

    # Batch reproject once (audit S40)
    gdf_wgs84 = gdf.to_crs("EPSG:4326")

    out_features = _assemble_features(
        gdf_wgs84, {"cluster_id": [int(v) for v in labels]},
    )

    # JSON-safe cluster counts (E-1): np.int64 dict keys crash json.dumps in
    # the dispatch layer; coerce both keys and counts to native int.
    _uniq_labels, _uniq_counts = np.unique(labels, return_counts=True)
    cluster_counts = {int(k): int(v) for k, v in zip(_uniq_labels, _uniq_counts)}

    data_out = {
        "type": "FeatureCollection",
        "features": out_features,
        "cluster_stats": cluster_counts,
        "method": method,
        "n_clusters": len(set(labels)) - (1 if -1 in labels else 0),
    }
    # G-3（#867）：披露值维的实际参与尺度（米），调用方能感知量纲语义。
    if value_effective_scale is not None:
        data_out["value_dim_effective_scale_m"] = round(value_effective_scale, 2)

    return GeoAnalysisResult(True, data_out, summary)

def h3_lisa(h3_geojson: dict, value_field: str) -> GeoAnalysisResult:
    """
    Calculate Local Indicators of Spatial Association (LISA) for H3 hex bins.
    """
    try:
        from libpysal.weights import Queen
        from esda.moran import Moran_Local
    except ImportError:
        return GeoAnalysisResult(False, None, "libpysal or esda not installed", error_type="ImportError")

    res = to_utm_gdf(h3_geojson)
    if res is None or res[0] is None:
        return GeoAnalysisResult(False, None, "Invalid GeoJSON or no features found", error_type="ValueError")
    
    gdf, utm_crs = res
    aligned = _filter_numeric_gdf(gdf, value_field)
    if aligned is None:
        return GeoAnalysisResult(False, None, f"Field '{value_field}' missing or non-numeric", error_type="ValueError")
    gdf, values = aligned
    if len(values) < 3:
        return GeoAnalysisResult(False, None, "At least 3 features required for LISA", error_type="InsufficientData")

    # Constant-value guard (E-3): esda's Moran_Local returns Is=NaN but q=3 /
    # p_sim=0.001 on constant input, which the classifier then labelled as
    # "significant LL coldspots" for every cell — a wholly fabricated result.
    if float(np.ptp(values)) == 0.0:
        return GeoAnalysisResult(
            False, None,
            f"All '{value_field}' values are identical; LISA is undefined.",
            error_type="ValueError",
        )

    # Use original geometries (hexagons) to build weights
    # We must ensure there is no index duplication
    gdf = gdf.reset_index(drop=True)
    w = Queen.from_dataframe(gdf)
    w.transform = 'r'

    # Island guard (#927): Queen creates island weights (0 neighbors) for
    # geographically disconnected hexes; esda.crand.compute_chunk cannot
    # broadcast empty neighbor arrays and crashes. Exclude islands from
    # Moran_Local and give them neutral results, preserving row alignment.
    island_ids = set(getattr(w, "islands", []) or [])
    if island_ids:
        n_total = len(gdf)
        non_island_idx = [i for i in range(n_total) if i not in island_ids]
        if len(non_island_idx) < 3:
            p_sim = np.ones(n_total, dtype=float)
            q_arr = np.zeros(n_total, dtype=int)
        else:
            values_conn = values[np.array(non_island_idx)]
            gdf_conn = gdf.iloc[non_island_idx].reset_index(drop=True)
            w2 = Queen.from_dataframe(gdf_conn)
            w2.transform = 'r'
            w2_islands = set(getattr(w2, "islands", []) or [])
            if w2_islands:
                # Subset still contains islands (rare); filter again
                w2_non = [i for i in range(len(gdf_conn)) if i not in w2_islands]
                if len(w2_non) < 3:
                    p_sim = np.ones(n_total, dtype=float)
                    q_arr = np.zeros(n_total, dtype=int)
                else:
                    values_conn2 = values_conn[np.array(w2_non)]
                    gdf_conn2 = gdf_conn.iloc[w2_non].reset_index(drop=True)
                    w3 = Queen.from_dataframe(gdf_conn2)
                    w3.transform = 'r'
                    lisa2 = Moran_Local(values_conn2, w3, seed=42)
                    p_tmp = np.asarray(lisa2.p_sim)
                    q_tmp = np.asarray(lisa2.q)
                    p_conn2 = np.ones(len(values_conn), dtype=float)
                    q_conn2 = np.zeros(len(values_conn), dtype=int)
                    for li, gi in enumerate(w2_non):
                        p_conn2[gi] = float(p_tmp[li])
                        q_conn2[gi] = int(q_tmp[li])
                    p_sim = np.ones(n_total, dtype=float)
                    q_arr = np.zeros(n_total, dtype=int)
                    for li, gi in enumerate(non_island_idx):
                        p_sim[gi] = float(p_conn2[li])
                        q_arr[gi] = int(q_conn2[li])
            else:
                lisa = Moran_Local(values_conn, w2, seed=42)
                p_conn = np.asarray(lisa.p_sim)
                q_conn = np.asarray(lisa.q)
                p_sim = np.ones(n_total, dtype=float)
                q_arr = np.zeros(n_total, dtype=int)
                for li, gi in enumerate(non_island_idx):
                    p_sim[gi] = float(p_conn[li])
                    q_arr[gi] = int(q_conn[li])
    else:
        # Calculate LISA (with seed=42 for deterministic permutations)
        lisa = Moran_Local(values, w, seed=42)
        p_sim = np.asarray(lisa.p_sim)
        q_arr = np.asarray(lisa.q)
    significant = p_sim < 0.05
    # 审计 F-3 / §9.4：LISA 逐格置换 p 补 BH-FDR 校正 q（与 Gi* 路径同一
    # _bh_qvalues 语义）—— evidence 块的 multiple_testing 字段因此真实。
    q_vals = _bh_qvalues(np.asarray(p_sim, dtype=float))
    cluster_labels = ["HH", "LH", "LL", "HL", "NS"]  # label_codes index 0..4
    label_codes = np.select(
        [significant & (q_arr == 1),
         significant & (q_arr == 2),
         significant & (q_arr == 3),
         significant & (q_arr == 4)],
        [0, 1, 2, 3],
        default=4,
    )
    clusters = [cluster_labels[c] for c in label_codes.tolist()]
    # G-6（#870）：多重比较披露 —— p_sim<0.05 的逐格判定在随机数据下期望
    # 产出 ~0.05n 个"显著"格子，信封披露期望假阳性数供叙述校准。
    _lisa_expected_fp = round(0.05 * len(p_sim), 1)
    label_counts = np.bincount(label_codes, minlength=5)
    cluster_counts = {
        "HH": int(label_counts[0]),
        "LL": int(label_counts[2]),
        "HL": int(label_counts[3]),
        "LH": int(label_counts[1]),
        "NS": int(label_counts[4]),
    }
        
    # Batch reproject once (audit S40)
    gdf_wgs84 = gdf.to_crs("EPSG:4326")

    out_features = _assemble_features(
        gdf_wgs84,
        {"lisa_cluster": list(clusters),
         "q_value_fdr": [round(float(v), 6) for v in q_vals]},
    )

    summary_parts = []
    if cluster_counts["HH"] > 0:
        summary_parts.append(f"{cluster_counts['HH']} High-High hotspots")
    if cluster_counts["LL"] > 0:
        summary_parts.append(f"{cluster_counts['LL']} Low-Low coldspots")
    if cluster_counts["HL"] > 0:
        summary_parts.append(f"{cluster_counts['HL']} High-Low spatial outliers")
    if cluster_counts["LH"] > 0:
        summary_parts.append(f"{cluster_counts['LH']} Low-High spatial outliers")
        
    if summary_parts:
        summary = "Found " + ", ".join(summary_parts) + "."
        
        # Determine dominant pattern
        # Excluding NS
        sig_counts = {k: v for k, v in cluster_counts.items() if k != "NS" and v > 0}
        if sig_counts:
            dominant = max(sig_counts, key=sig_counts.get)
            dom_name = {"HH": "High-High clustering", "LL": "Low-Low clustering", "HL": "High-Low outliers", "LH": "Low-High outliers"}
            summary += f" Dominant pattern is {dom_name[dominant]}."
    else:
        summary = "No significant local spatial autocorrelation found."
        
    data_out = {
        "type": "FeatureCollection",
        "features": out_features,
        "cluster_stats": cluster_counts,
        # G-6（#870）：随机零假设下的期望假阳性格数（p_sim<0.05 逐格判定）。
        "expected_false_positives": _lisa_expected_fp,
        # 审计 F-3：descriptor 声明 statistical_significance —— 证据块真实
        # 落 data_out（esda.Moran_Local 条件置换、固定 seed=42；块级 p 取
        # 逐格 p_sim 的最小值，逐格 p/q 在 feature properties）。
        "uncertainty": [StatisticalSignificance(
            target="lisa_local",
            statistic_name="Local Moran's I (LISA)",
            p_value=float(np.min(p_sim)),
            method="permutation",
            permutations=999,
            multiple_testing="BH-FDR",
            alternative="two-sided",
        ).to_evidence()],
    }
    summary += (
        f"（{len(p_sim)} 个格网在 α=0.05 逐格判定下的随机期望假阳性 ≈"
        f"{_lisa_expected_fp} 个；显著数不高于该值时聚集结论应谨慎叙述）"
    )

    return GeoAnalysisResult(True, data_out, summary)


# ── Thread-Safe Pairwise Distance Matrix LRU Cache ──
_distance_matrix_cache: OrderedDict = OrderedDict()
_distance_matrix_maxsize: int = 16
_distance_matrix_hits: int = 0
_distance_matrix_misses: int = 0
_distance_matrix_lock = threading.Lock()


def clear_distance_matrix_cache() -> None:
    """Clear the pairwise distance matrix LRU cache."""
    with _distance_matrix_lock:
        _distance_matrix_cache.clear()
        global _distance_matrix_hits, _distance_matrix_misses
        _distance_matrix_hits = 0
        _distance_matrix_misses = 0


def get_distance_matrix_cache_info() -> dict:
    """Return distance matrix cache hits, misses, size, and maxsize."""
    with _distance_matrix_lock:
        return {
            "hits": _distance_matrix_hits,
            "misses": _distance_matrix_misses,
            "size": len(_distance_matrix_cache),
            "maxsize": _distance_matrix_maxsize,
        }


def compute_st_distance_matrix(
    coords: np.ndarray,
    t_seconds: np.ndarray,
    eps1_spatial_meters: float,
    eps2_temporal_seconds: float,
) -> Any:
    """Compute (or retrieve from LRU cache) normalized $L_\\infty$ spatio-temporal distance matrix."""
    global _distance_matrix_hits, _distance_matrix_misses
    # usedforsecurity=False: this digest is a non-cryptographic LRU cache key
    # (fingerprint of the coords/time arrays), not a security primitive.
    # Declaring it silences bandit B324 and documents intent.
    key_raw = f"{eps1_spatial_meters}:{eps2_temporal_seconds}:{hashlib.md5(coords.tobytes() + t_seconds.tobytes(), usedforsecurity=False).hexdigest()}"

    with _distance_matrix_lock:
        if key_raw in _distance_matrix_cache:
            _distance_matrix_hits += 1
            _distance_matrix_cache.move_to_end(key_raw)
            return _distance_matrix_cache[key_raw]

        _distance_matrix_misses += 1

    n = len(coords)
    if n <= 5000:
        from scipy.spatial.distance import pdist, squareform
        d_spatial = squareform(pdist(coords, metric="euclidean"))
        d_temporal = np.abs(t_seconds[:, None] - t_seconds[None, :])
        d_mat = np.maximum(d_spatial / max(eps1_spatial_meters, 1e-6), d_temporal / max(eps2_temporal_seconds, 1e-6))
    else:
        from scipy.spatial import cKDTree
        tree = cKDTree(coords)
        coo_spatial = tree.sparse_distance_matrix(tree, max_distance=eps1_spatial_meters, output_type="coo_matrix")

        r, c = coo_spatial.row, coo_spatial.col
        spatial_dists = coo_spatial.data
        temporal_dists = np.abs(t_seconds[r] - t_seconds[c])

        valid_edges = temporal_dists <= eps2_temporal_seconds
        r_valid, c_valid = r[valid_edges], c[valid_edges]
        combined_dists = np.maximum(
            spatial_dists[valid_edges] / max(eps1_spatial_meters, 1e-6),
            temporal_dists[valid_edges] / max(eps2_temporal_seconds, 1e-6)
        )
        d_mat = sparse.csr_matrix((combined_dists, (r_valid, c_valid)), shape=(n, n))

    # Memory guard: a dense n×n float64 matrix is ~8·n² bytes (200MB at n=5000).
    # Caching 16 of those could consume >3GB, risking OOM. Sparse results (n>5000
    # branch) are already small, so always cache those; only cache dense matrices
    # below a size threshold so the LRU's worst-case footprint stays bounded.
    _DENSE_CACHE_MAX_N = 2000
    cacheable = sparse.issparse(d_mat) or n <= _DENSE_CACHE_MAX_N

    with _distance_matrix_lock:
        if cacheable:
            _distance_matrix_cache[key_raw] = d_mat
            if len(_distance_matrix_cache) > _distance_matrix_maxsize:
                _distance_matrix_cache.popitem(last=False)

    return d_mat


def st_dbscan_narrated(
    geojson: dict,
    eps1_spatial_meters: float = 1000.0,
    eps2_temporal_seconds: float = 3600.0,
    min_samples: int = 5,
    timestamp_field: str = "timestamp",
) -> GeoAnalysisResult:
    """
    Spatio-Temporal DBSCAN (ST-DBSCAN) clustering using vectorized NumPy & Scikit-Learn.
    Evaluates spatial distance threshold eps1 (meters) and temporal distance threshold eps2 (seconds)
    simultaneously via a normalized max metric matrix.
    """
    try:
        from sklearn.cluster import DBSCAN
    except ImportError:
        return GeoAnalysisResult(False, None, "scikit-learn not installed", error_type="ImportError")

    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        return GeoAnalysisResult(False, None, "Invalid GeoJSON or no features found", error_type="ValueError")

    gdf, utm_crs = res
    if len(gdf) < min_samples:
        return GeoAnalysisResult(
            False, None,
            f"At least {min_samples} features required for ST-DBSCAN (found {len(gdf)})",
            error_type="InsufficientData",
        )

    # 1. Parse timestamps
    target_ts_field = timestamp_field
    if target_ts_field not in gdf.columns:
        possible_fields = ["timestamp", "time", "datetime", "t", "date", "created_at"]
        found = [f for f in possible_fields if f in gdf.columns]
        if found:
            target_ts_field = found[0]
        else:
            return GeoAnalysisResult(
                False, None,
                f"Timestamp field '{timestamp_field}' not found in feature properties",
                error_type="ValueError",
                correction_hint="Ensure features contain an ISO-8601 string or numeric epoch timestamp property.",
            )

    ts_series = gdf[target_ts_field]
    try:
        parsed_dt = pd.to_datetime(ts_series, errors="coerce", utc=True)
        valid_mask = parsed_dt.notna()
        if not valid_mask.any():
            return GeoAnalysisResult(
                False, None,
                f"No valid timestamps could be parsed from field '{target_ts_field}'",
                error_type="ValueError",
            )

        gdf_valid = gdf[valid_mask].reset_index(drop=True)
        parsed_dt_valid = parsed_dt[valid_mask]
        t_seconds = parsed_dt_valid.astype("int64").to_numpy() / 1e9
    except Exception as e:
        return GeoAnalysisResult(
            False, None,
            f"Failed to parse timestamp field '{target_ts_field}': {str(e)}",
            error_type="ValueError",
        )

    n = len(gdf_valid)
    if n < min_samples:
        return GeoAnalysisResult(
            False, None,
            f"Fewer than min_samples ({min_samples}) valid timestamped points found ({n})",
            error_type="InsufficientData",
        )

    coords = np.column_stack((gdf_valid.centroid.x.values, gdf_valid.centroid.y.values))

    # 2. Pairwise Distance Matrix Computation (from LRU Cache)
    d_matrix = compute_st_distance_matrix(coords, t_seconds, eps1_spatial_meters, eps2_temporal_seconds)
    db = DBSCAN(eps=1.0, min_samples=min_samples, metric="precomputed")
    labels = db.fit_predict(d_matrix)

    # 3. Compute Metrics Summary
    unique_labels = set(labels)
    n_clusters = len(unique_labels) - (1 if -1 in unique_labels else 0)
    noise_points = int(np.sum(labels == -1))
    clustered_points = int(np.sum(labels >= 0))

    t_min_sec = float(t_seconds.min())
    t_max_sec = float(t_seconds.max())
    temporal_span_hours = round((t_max_sec - t_min_sec) / 3600.0, 2)

    # 4. Format Output GeoJSON
    gdf_wgs84 = gdf_valid.to_crs("EPSG:4326")
    out_features = _assemble_features(
        gdf_wgs84, {"cluster_id": [int(v) for v in labels]},
    )

    summary_stats = {
        "total_clusters": n_clusters,
        "clustered_points": clustered_points,
        "noise_points": noise_points,
        "temporal_span_hours": temporal_span_hours,
        "eps1_spatial_meters": float(eps1_spatial_meters),
        "eps2_temporal_seconds": float(eps2_temporal_seconds),
        "min_samples": int(min_samples),
    }

    data_out = {
        "type": "FeatureCollection",
        "features": out_features,
        "cluster_stats": summary_stats,
    }

    summary = (
        f"ST-DBSCAN identified {n_clusters} spatio-temporal cluster(s) and {noise_points} noise point(s) "
        f"across a temporal span of {temporal_span_hours:.2f} hours "
        f"(eps1={eps1_spatial_meters}m, eps2={eps2_temporal_seconds}s, min_samples={min_samples})."
    )

    return GeoAnalysisResult(True, data_out, summary)


# ── Foundation V2（A1）：局部 Geary / Join Count / 双变量 Moran / ──────
#    地理探测器 / 权重敏感性。实现纪律与上方 VNext 全局族一致：固定种子
#    42 置换、双侧 (count+1)/(perms+1)、类型化科学错误、稀疏权重端到端。

_CORRECTION_METHODS = ("none", "bh", "bonferroni", "holm")
#: Multiple-testing 字段的证据块标签（与 multiple_testing_correction 对齐）。
_CORRECTION_LABELS = {
    "none": "", "bh": "BH-FDR", "bonferroni": "Bonferroni", "holm": "Holm",
}


def _filter_two_numeric_gdf(
    gdf: "gpd.GeoDataFrame", field_x: str, field_y: str
) -> tuple["gpd.GeoDataFrame", "np.ndarray", "np.ndarray"] | None:
    """两字段同时数值过滤（行对齐，NaN/±inf 丢行）；缺字段返回 None。"""
    aligned_x = _filter_numeric_gdf(gdf, field_x)
    if aligned_x is None or len(aligned_x[1]) == 0:
        return None
    gdf_x, vx = aligned_x
    aligned_xy = _filter_numeric_gdf(gdf_x, field_y)
    if aligned_xy is None or len(aligned_xy[1]) == 0:
        return None
    gdf_xy, vy = aligned_xy
    return gdf_xy, vx, vy


def _moran_from_wm(
    values: "np.ndarray", wm, perms: int,
) -> dict:
    """行标准化权重下的全局 Moran's I + 置换 p（固定种子 42，双侧）。"""
    n = len(values)
    w = wm.matrix.tocoo()
    s0 = float(w.sum())
    if s0 == 0:
        return {"moran_i": None, "p_value": None, "expected_i": None}
    z = values - values.mean()
    denom = float(np.sum(z ** 2))
    if denom <= 0:
        return {"moran_i": None, "p_value": None, "expected_i": None}
    w_vals, i_idx, j_idx = w.data, w.row, w.col

    def _stat(pv: "np.ndarray") -> float:
        pz = pv - pv.mean()
        p_den = float(np.sum(pz ** 2))
        if p_den <= 0:
            return 0.0
        return (n / s0) * float(np.sum(w_vals * pz[i_idx] * pz[j_idx])) / p_den

    observed = _stat(z)
    expected_i = -1.0 / (n - 1)
    perm_is = _permutation_stats(_stat, values, perms)
    p_value = _two_sided_permutation_pvalue(perm_is, observed, expected_i, perms)
    return {"moran_i": float(observed), "p_value": float(p_value),
            "expected_i": float(expected_i)}


def local_geary_narrated(
    geojson: dict,
    value_field: str,
    weights_scheme: str = "knn",
    k: int = 8,
    distance_band: float = 0,
    permutations: int = 99,
    correction: str = "bh",
) -> GeoAnalysisResult:
    """局部 Geary's C_i（Anselin 1995）—— 相似性聚焦的局部自相关。

    C_i = Σⱼ w_ij(z_i − z_j)²（z 为总体方差标准化，与 esda.Geary_Local 同
    式，行标准化权重）。C_i 显著低于期望 → 邻域相似（空间聚集）；显著
    高于期望 → 邻域相异（过渡/边界带）。Local Geary **不区分**高-高 vs
    低-低的方向配对（那是 LISA 的事）——标签按值象限给
    similar_high/similar_low/dissimilar/neutral 并在叙事里披露这一限制。
    置换推断：固定种子 42，双侧 (count+1)/(perms+1)；多重校正
    correction ∈ {bh(默认), bonferroni, holm, none}。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection with at least 3 numeric features",
        )
    gdf, _ = res
    aligned = _filter_numeric_gdf(gdf, value_field)
    if aligned is None or len(aligned[1]) == 0:
        raise MissingRequiredField(
            f"field '{value_field}' is missing or non-numeric",
            correction_hint=f"provide a numeric property '{value_field}' on every feature",
        )
    gdf, values = aligned
    n = len(values)
    if n < 3:
        raise InsufficientSamples(
            f"local Geary needs at least 3 valid numeric features (got {n})",
            correction_hint="add observations or use a method valid at this sample size",
        )
    if float(np.ptp(values)) == 0.0:
        raise DegenerateData(
            f"all '{value_field}' values are identical; local Geary is undefined",
            correction_hint="check the numeric field for constant values",
        )
    if str(correction).lower() not in _CORRECTION_METHODS:
        raise ValueError(
            f"correction must be one of {_CORRECTION_METHODS} (got {correction!r})")
    correction = str(correction).lower()
    perms = _validate_permutations(permutations)

    wm = _autocorr_weights(gdf, n, weights_scheme, k, distance_band)
    if wm.s0 == 0:
        raise DegenerateData(
            "spatial weights matrix is empty (every observation is an island)",
            correction_hint="increase the distance band / k, or check geometry connectivity",
        )
    w = wm.matrix.tocoo()
    w_vals, i_idx, j_idx = w.data, w.row, w.col
    # 行标准化权重 → 非孤岛行和恒 1（孤岛 0）。期望必须用行和（不是邻居
    # 计数）：C_i 以权重加权，行和才是 E[C_i] 的正确缩放。
    row_sums = np.asarray(wm.matrix.sum(axis=1)).ravel().astype(float)

    # z 标准化（总体方差，ddof=0）——与 esda.Geary_Local 的 localG 同尺度。
    z = (values - values.mean()) / values.std(ddof=0)
    c_obs = np.bincount(
        i_idx, weights=w_vals * (z[i_idx] - z[j_idx]) ** 2, minlength=n)
    # 随机化期望：E[(z_a − z_b)²] = 2n/(n−1) → E[C_i] = 行和 × 2n/(n−1)
    # （孤岛行和为 0 → C_i ≡ 0，p=1 中性）。
    expected_c = row_sums * (2.0 * n / (n - 1.0))

    rng = np.random.default_rng(_PERMUTATION_SEED)
    extreme = np.zeros(n, dtype=np.int64)
    for _ in cancellable(range(perms)):
        pz = rng.permutation(z)
        c_perm = np.bincount(
            i_idx, weights=w_vals * (pz[i_idx] - pz[j_idx]) ** 2, minlength=n)
        extreme += np.abs(c_perm - expected_c) >= np.abs(c_obs - expected_c)
    p_vals = (extreme + 1) / (perms + 1)
    p_adj = multiple_testing_correction(p_vals, correction)

    significant = p_adj < 0.05
    similar = c_obs < expected_c
    high_value = z > 0
    clusters = np.select(
        [significant & similar & high_value,
         significant & similar & ~high_value,
         significant & ~similar],
        ["similar_high", "similar_low", "dissimilar"],
        default="neutral",
    ).tolist()

    counts = {c: int(sum(1 for v in clusters if v == c))
              for c in ("similar_high", "similar_low", "dissimilar", "neutral")}
    sig_count = int(np.sum(significant))
    expected_fp = round(0.05 * n, 1)

    gdf_wgs84 = gdf.to_crs("EPSG:4326")
    features = _assemble_features(
        gdf_wgs84,
        {
            # 统计量字段保留 10 位小数：conformance 锚（vs esda.Geary_Local
            # 1e-8）要穿工具载荷比较，6 位舍入会把 agreement 卡在 5e-7。
            "local_geary_c": [round(float(v), 10) for v in c_obs],
            "p_value": [round(float(v), 6) for v in p_vals],
            f"p_{correction}" if correction != "none" else "p_value_adjusted": [
                round(float(v), 6) for v in p_adj],
            "local_geary_cluster": clusters,
        },
    )

    data_out = {
        "type": "FeatureCollection",
        "features": features,
        "local_geary_counts": counts,
        "significant_count": sig_count,
        "expected_false_positives": expected_fp,
        "correction": correction,
        "n_features": n,
        "permutations": perms,
        "weights": wm.metadata(),
        "uncertainty": StatisticalSignificance(
            target="local_geary",
            statistic_name="share of significant local Geary C_i (α=0.05)",
            statistic_value=sig_count / n,
            p_value=None,
            method="permutation",
            permutations=perms,
            multiple_testing=_CORRECTION_LABELS[correction],
        ).to_evidence(),
    }
    summary = (
        f"局部 Geary：{sig_count}/{n} 个要素校正后显著"
        f"（{correction.upper()}；未校正 α=0.05 随机期望假阳性 ≈{expected_fp} 个）。"
        f"similar_high={counts['similar_high']}、similar_low={counts['similar_low']}、"
        f"dissimilar={counts['dissimilar']}、neutral={counts['neutral']}。"
        "注意：Local Geary 只判相似/相异，高-低方向配对请用 LISA（h3_lisa）。")
    return GeoAnalysisResult(True, data_out, summary)


def local_moran_narrated(
    geojson: dict,
    value_field: str,
    weights_scheme: str = "knn",
    k: int = 8,
    distance_band: float = 0,
    permutations: int = 99,
    correction: str = "bh",
) -> GeoAnalysisResult:
    """单变量局部 Moran（LISA，Anselin 1995）—— 多边形/点权重的方向配对。

    I_i = (n−1)·z_i·(W z)_i / Σz²（esda.Moran_Local 同式同尺度；行标准化
    W 下 (W z)_i 即邻域 z 均值）。全局分解：Σ_i I_i · n/(S₀·(n−1)) = 全局
    Moran's I（S₀ 为权重矩阵实际总和），由 conformance 锚定。象限语义
    （esda q 值约定，无条件分配）：z_i>0、lag>0 → high_high（q=1）；
    z_i<0、lag>0 → low_high（q=2）；z_i<0、lag<0 → low_low（q=3）；
    z_i>0、lag<0 → high_low（q=4）；lag=0（孤岛）→ q=0。显著性独立于
    象限由 p 值表达。置换推断：固定种子 42，条件随机化（对角无自权重 →
    全局置换与 esda crand 的条件置换同分布），双侧 (count+1)/(perms+1)
    （esda 的 p_sim 是单侧 directed —— 本实现双侧更保守，conformance 以
    「p_mine ≥ p_esda（单侧 ⊆ 双侧）且 ≤ 2·p_esda + MC 容差」对账）；
    多重校正 correction ∈ {bh(默认), bonferroni, holm, none}。孤岛（无
    邻居）位置 I_i≡0、p=1 中性并显式披露计数。

    与 ``stats.h3_lisa``（H3 网格专用、esda 委托）和
    ``stats.bivariate_local_moran``（双变量）互补：本实现是任意
    knn/queen/rook/distance_band 权重下的原生 numpy 路径。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection with at least 3 numeric features",
        )
    gdf, _ = res
    aligned = _filter_numeric_gdf(gdf, value_field)
    if aligned is None or len(aligned[1]) == 0:
        raise MissingRequiredField(
            f"field '{value_field}' is missing or non-numeric",
            correction_hint=f"provide a numeric property '{value_field}' on every feature",
        )
    gdf, values = aligned
    n = len(values)
    if n < 3:
        raise InsufficientSamples(
            f"local Moran needs at least 3 valid numeric features (got {n})",
            correction_hint="add observations or use a method valid at this sample size",
        )
    if float(np.ptp(values)) == 0.0:
        raise DegenerateData(
            f"all '{value_field}' values are identical; local Moran is undefined",
            correction_hint="check the numeric field for constant values",
        )
    if str(correction).lower() not in _CORRECTION_METHODS:
        raise ValueError(
            f"correction must be one of {_CORRECTION_METHODS} (got {correction!r})")
    correction = str(correction).lower()
    perms = _validate_permutations(permutations)

    wm = _autocorr_weights(gdf, n, weights_scheme, k, distance_band)
    if wm.s0 == 0:
        raise DegenerateData(
            "spatial weights matrix is empty (every observation is an island)",
            correction_hint="increase the distance band / k, or check geometry connectivity",
        )
    w = wm.matrix.tocoo()
    w_vals, i_idx, j_idx = w.data, w.row, w.col

    # z 标准化（总体方差 ddof=0，与 esda.Moran_Local 同尺度）。
    z = (values - values.mean()) / values.std(ddof=0)
    # esda 同尺度：I_i = (n−1)·z_i·(W z)_i / Σz²（ddof=0 z 下 Σz²=n）。
    scale = (n - 1.0) / n
    lag = np.bincount(i_idx, weights=w_vals * z[j_idx], minlength=n)
    i_obs = scale * z * lag
    # S₀ = 权重矩阵实际总和（行标准化下 = 非孤岛行数）——只用于全局分解
    # 披露：Σ I_i·n/(S₀·(n−1)) = 全局 Moran's I。
    s0 = float(w.data.sum())
    # 置换期望：z 均值中心化 → E[lag_i] = 行和·mean(z) = 0 → E[I_i] ≈ 0。
    rng = np.random.default_rng(_PERMUTATION_SEED)
    extreme = np.zeros(n, dtype=np.int64)
    for _ in cancellable(range(perms)):
        pz = rng.permutation(z)
        # 对角恒 0（include_self=False 的四套权重方案）→ 置换位置 i 的值
        # 不进入 lag_i，全局置换即 esda crand 的条件随机化。置换统计量与
        # 观测同式（同 scale）。
        lag_perm = np.bincount(i_idx, weights=w_vals * pz[j_idx], minlength=n)
        extreme += np.abs(scale * z * lag_perm) >= np.abs(i_obs)
    p_vals = (extreme + 1) / (perms + 1)
    p_adj = multiple_testing_correction(p_vals, correction)

    significant = p_adj < 0.05
    z_high = z > 0
    lag_zero = lag == 0.0
    lag_high = lag > 0
    # 象限无条件分配（esda q 约定 1=HH, 2=LH, 3=LL, 4=HL）；孤岛/零滞后
    # q=0 —— 方向语义对零滞后无定义，宁给 0 不冒充象限。
    q_values = np.select(
        [lag_zero, z_high & lag_high, ~z_high & lag_high,
         ~z_high & ~lag_high, z_high & ~lag_high],
        [0, 1, 2, 3, 4],
    ).tolist()
    clusters = np.where(significant, np.select(
        [lag_zero, z_high & lag_high, ~z_high & lag_high,
         ~z_high & ~lag_high, z_high & ~lag_high],
        ["neutral", "high_high", "low_high", "low_low", "high_low"],
        default="neutral",
    ), "neutral").tolist()
    island_count = int(np.sum(np.asarray(wm.matrix.sum(axis=1)).ravel() == 0))

    counts = {c: int(sum(1 for v in clusters if v == c))
              for c in ("high_high", "low_high", "low_low", "high_low",
                        "neutral")}
    sig_count = int(np.sum(significant))
    expected_fp = round(0.05 * n, 1)

    gdf_wgs84 = gdf.to_crs("EPSG:4326")
    features = _assemble_features(
        gdf_wgs84,
        {
            # 统计量字段保留 10 位小数：conformance 锚（vs esda.Moran_Local
            # 1e-8）要穿工具载荷比较，6 位舍入会把 agreement 卡在舍入误差。
            "local_moran_i": [round(float(v), 10) for v in i_obs],
            "lisa_q": q_values,
            "p_value": [round(float(v), 6) for v in p_vals],
            f"p_{correction}" if correction != "none" else "p_value_adjusted": [
                round(float(v), 6) for v in p_adj],
            "lisa_cluster": clusters,
        },
    )

    data_out = {
        "type": "FeatureCollection",
        "features": features,
        "lisa_counts": counts,
        "significant_count": sig_count,
        "expected_false_positives": expected_fp,
        "island_count": island_count,
        "correction": correction,
        "n_features": n,
        "permutations": perms,
        # 全局分解（Σ I_i·n/(S₀·(n−1)) = 全局 Moran's I；conformance 锚消费）。
        "global_moran_from_local": (
            float(np.sum(i_obs) * n / (s0 * (n - 1.0))) if s0 > 0 and n > 1
            else 0.0
        ),
        "weights": wm.metadata(),
        "uncertainty": StatisticalSignificance(
            target="local_moran",
            statistic_name="share of significant local Moran I_i (α=0.05)",
            statistic_value=sig_count / n,
            p_value=None,
            method="permutation",
            permutations=perms,
            multiple_testing=_CORRECTION_LABELS[correction],
        ).to_evidence(),
    }
    summary = (
        f"LISA 局部 Moran：{sig_count}/{n} 个要素校正后显著"
        f"（{correction.upper()}；未校正 α=0.05 随机期望假阳性 ≈{expected_fp} 个）。"
        f"high_high={counts['high_high']}、low_low={counts['low_low']}、"
        f"high_low={counts['high_low']}、low_high={counts['low_high']}、"
        f"neutral={counts['neutral']}。"
        + (f"孤岛位置 {island_count} 个（无邻居，I_i=0 中性）。" if island_count else "")
        + "方向配对语义与 h3_lisa 一致；双变量用 bivariate_local_moran。")
    return GeoAnalysisResult(True, data_out, summary)


def join_count_narrated(
    geojson: dict,
    binary_field: str,
    weights_scheme: str = "knn",
    k: int = 8,
    distance_band: float = 0,
    permutations: int = 0,
) -> GeoAnalysisResult:
    """二元 Join Count（Cliff & Ord 1973；non-free sampling 解析推断）。

    二值场（值 ⊆ {0,1}，否则 UnsupportedMethod）在二值对称权重上统计
    n_BB / n_BW / n_WW；期望与方差按 non-free sampling（不放回、条件于
    类别边际的 Cliff-Ord 矩：p=B(B−1)/(n(n−1)) 等）解析公式，
    z + 双侧正态 p；``permutations > 0`` 时附加固定种子 42 的置换复核。
    n_BB 显著偏低（n_BW 显著偏高）= 同类不相邻（空间负关联）；反之
    n_BB / n_WW 偏高 = 同类聚集（正关联）。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection with a binary (0/1) field",
        )
    gdf, _ = res
    aligned = _filter_numeric_gdf(gdf, binary_field)
    if aligned is None or len(aligned[1]) == 0:
        raise MissingRequiredField(
            f"field '{binary_field}' is missing or non-numeric",
            correction_hint=f"provide a binary (0/1) property '{binary_field}' "
                            "on every feature",
        )
    gdf, values = aligned
    n = len(values)
    if n < 4:
        raise InsufficientSamples(
            f"join count non-free sampling variance needs n ≥ 4 (got {n})",
            correction_hint="add observations",
        )
    uniq = np.unique(values)
    if not np.all(np.isin(uniq, (0.0, 1.0))):
        raise UnsupportedMethod(
            f"field '{binary_field}' is not binary: unique values {uniq[:8].tolist()}",
            correction_hint="derive a binary field (e.g. above/below threshold), "
                            "or use moran_i / local_geary for continuous values",
        )
    if len(uniq) < 2:
        raise DegenerateData(
            f"all '{binary_field}' values are identical ({uniq[0]:.0f}); "
            "join count is undefined",
            correction_hint="the field must contain both 0 and 1",
        )

    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    scheme = str(weights_scheme or "knn").lower()
    if scheme == "knn":
        wm = build_knn_weights(coords, k=min(int(k), n - 1),
                               row_standardized=False)
    elif scheme in ("queen", "rook"):
        wm = build_contiguity_weights(gdf, scheme=scheme, row_standardized=False)
    elif scheme == "distance_band":
        threshold = float(distance_band) if distance_band and float(distance_band) > 0 \
            else auto_band_8nn(coords)
        wm = build_distance_band_weights(
            coords, threshold=threshold, include_self=False,
            row_standardized=False)
    else:
        raise ValueError(
            f"unknown weights_scheme {weights_scheme!r}; "
            f"expected one of {WEIGHT_SCHEMES}")

    w = wm.matrix.tocoo()
    upper = w.row < w.col  # 对称二值权重 → i<j 恰好枚举无序对
    wu_i, wu_j, wu_v = w.row[upper], w.col[upper], w.data[upper]
    joins = float(np.sum(wu_v))
    if joins == 0:
        raise DegenerateData(
            "no neighbour joins under this weights scheme",
            correction_hint="increase k / distance band, or check geometry connectivity",
        )
    b = values.astype(float)
    black = float(np.sum(b))
    white = n - black
    n_bb = float(np.sum(wu_v * b[wu_i] * b[wu_j]))
    n_ww = float(np.sum(wu_v * (1.0 - b[wu_i]) * (1.0 - b[wu_j])))
    n_bw = joins - n_bb - n_ww

    def _free_sampling(observed: float, num2: float, num4: float) -> dict:
        """non-free sampling 期望/方差（Cliff-Ord 1973 条件于边际）。

        p = num2/(n(n−1))（不放回：两次抽中同类的一阶矩），
        q = num4/((n(n−1))(n−2)(n−3))（四次不放回）；
        Var = J·p(1−p) + 2J(J−1)(q − p²)。（V3 review M1：此前误标为
        free sampling——free 形式应为 p=(B/n)²、q=(B/n)⁴；实现的矩是
        non-free/条件形式，属有效且更常用的检验，仅更正披露措辞。）
        """
        den2 = n * (n - 1.0)
        den4 = den2 * (n - 2.0) * (n - 3.0)
        p = num2 / den2
        expected = joins * p
        # 经典 non-free 二阶矩在稀少类别（m<4 → num4=0）或高聚集
        # 下可能为负 —— 经典式把所有 join 对当端点不相交，漏掉共享顶点
        # 的交叉矩。负方差钳零并报 p=1 是**伪造的「无证据」答案**
        # （评审 R2 MAJOR-2）：这里改为类型化拒绝解析推断，要求置换。
        var = joins * p * (1.0 - p) + 2.0 * joins * (joins - 1.0) * (
            num4 / den4 - p * p)
        if var <= 0.0:
            # 经典式漏共享顶点交叉矩，稀少类别（m<4 → num4=0）下可为负；
            # 钳零并报 p=1 是伪造的「无证据」答案（评审 R2 MAJOR-2）。
            # 诚实降级：计数/期望照常返回，解析 z/p 显式不可用（None），
            # 置换推断（permutations>0）不受影响。
            return {
                "observed": float(observed), "expected": float(expected),
                "variance": None, "z": None, "p_value": None,
                "analytic_note": (
                    "non-free sampling 方差非正（类别过稀 m<4 或分布极端）——"
                    "解析 z/p 不可用，请用 permutations 置换推断"),
            }
        z_stat = (observed - expected) / np.sqrt(var)
        return {
            "observed": float(observed), "expected": float(expected),
            "variance": float(var), "z": float(z_stat),
            "p_value": float(2.0 * norm.sf(abs(z_stat))),
        }

    stats_bb = _free_sampling(n_bb, black * (black - 1.0),
                              black * (black - 1.0) * (black - 2.0) * (black - 3.0))
    stats_ww = _free_sampling(n_ww, white * (white - 1.0),
                              white * (white - 1.0) * (white - 2.0) * (white - 3.0))
    stats_bw = _free_sampling(n_bw, 2.0 * black * white,
                              4.0 * black * (black - 1.0) * white * (white - 1.0))

    perm_p = None
    perms = int(permutations or 0)
    if perms > 0:
        perms = _validate_permutations(perms)
        rng = np.random.default_rng(_PERMUTATION_SEED)
        # 双侧：以置换统计量偏离期望不小于观测偏离计数（与解析 z 同方向）。
        obs_dev = (abs(n_bb - stats_bb["expected"]),
                   abs(n_bw - stats_bw["expected"]),
                   abs(n_ww - stats_ww["expected"]))
        extreme = np.zeros(3, dtype=np.int64)
        for _ in cancellable(range(perms)):
            bp = rng.permutation(b)
            bb_t = float(np.sum(wu_v * bp[wu_i] * bp[wu_j]))
            ww_t = float(np.sum(wu_v * (1.0 - bp[wu_i]) * (1.0 - bp[wu_j])))
            bw_t = joins - bb_t - ww_t
            devs = (abs(bb_t - stats_bb["expected"]),
                    abs(bw_t - stats_bw["expected"]),
                    abs(ww_t - stats_ww["expected"]))
            extreme += np.asarray(devs) >= np.asarray(obs_dev)
        perm_p = {
            "n_bb": float((int(extreme[0]) + 1) / (perms + 1)),
            "n_bw": float((int(extreme[1]) + 1) / (perms + 1)),
            "n_ww": float((int(extreme[2]) + 1) / (perms + 1)),
            "permutations": perms,
        }

    _analytic_ok = all(s["p_value"] is not None
                       for s in (stats_bb, stats_bw, stats_ww))
    pattern = "random"
    if _analytic_ok:
        if max(stats_bw["p_value"], 0.0) < 0.05 and stats_bw["z"] > 0:
            pattern = "negative_spatial_autocorrelation"
        elif max(stats_bb["p_value"], stats_ww["p_value"]) < 0.05:
            pattern = "positive_spatial_autocorrelation"
    elif perm_p is not None:
        # 解析方差简并（non-free 二阶矩的已知局限）→ 用置换 p 分类：
        # 置换本是零假设分布的金标准（评审 R2 MAJOR-2 修复路径）。
        if (perm_p["n_bw"] < 0.05
                and n_bw > stats_bw["expected"]):
            pattern = "negative_spatial_autocorrelation"
        elif max(perm_p["n_bb"], perm_p["n_ww"]) < 0.05:
            pattern = "positive_spatial_autocorrelation"
        else:
            pattern = "random"
    else:
        pattern = "analytic_inference_unavailable"  # 无置换则拒绝判别

    data_out = {
        "n_features": n,
        "n_black": black,
        "n_white": white,
        "joins": joins,
        "weights_scheme": wm.scheme,
        "join_counts": {"n_bb": n_bb, "n_bw": n_bw, "n_ww": n_ww},
        "expected": {"n_bb": stats_bb["expected"],
                     "n_bw": stats_bw["expected"],
                     "n_ww": stats_ww["expected"]},
        "z": {"n_bb": stats_bb["z"], "n_bw": stats_bw["z"], "n_ww": stats_ww["z"]},
        "p_value_analytic": {"n_bb": stats_bb["p_value"],
                             "n_bw": stats_bw["p_value"],
                             "n_ww": stats_ww["p_value"]},
        "pattern": pattern,
        "weights": wm.metadata(),
        "uncertainty": [
            StatisticalSignificance(
                target="join_count_bw",
                statistic_name="n_BW join count (non-free sampling z-test)",
                statistic_value=n_bw,
                p_value=stats_bw["p_value"],
                method="analytic_normal",
                alternative="two-sided",
            ).to_evidence(),
            StatisticalSignificance(
                target="join_count_bb",
                statistic_name="n_BB join count (non-free sampling z-test)",
                statistic_value=n_bb,
                p_value=stats_bb["p_value"],
                method="analytic_normal",
                alternative="two-sided",
            ).to_evidence(),
        ],
    }
    if stats_bw.get("analytic_note") or stats_bb.get("analytic_note"):
        data_out["analytic_notes"] = [
            s["analytic_note"] for s in (stats_bb, stats_bw, stats_ww)
            if s.get("analytic_note")]
    if perm_p is not None:
        data_out["p_value_permutation"] = perm_p

    def _fmt_p(s: dict) -> str:
        return f"p={s['p_value']:.4f}" if s["p_value"] is not None else "p=不可用"

    summary = (
        f"Join Count（{wm.scheme}，{joins:.0f} 个无序连接，B={black:.0f}/W={white:.0f}）："
        f"n_BB={n_bb:.0f}（期望 {stats_bb['expected']:.1f}，{_fmt_p(stats_bb)}）、"
        f"n_BW={n_bw:.0f}（期望 {stats_bw['expected']:.1f}，{_fmt_p(stats_bw)}）、"
        f"n_WW={n_ww:.0f}（期望 {stats_ww['expected']:.1f}，{_fmt_p(stats_ww)}）。")
    if pattern == "negative_spatial_autocorrelation":
        summary += " 异类连接显著偏高 —— 同类不相邻（棋盘式负关联）。"
    elif pattern == "positive_spatial_autocorrelation":
        summary += " 同类连接显著偏高 —— 同类聚集（正关联）。"
    elif pattern == "analytic_inference_unavailable":
        summary += " 解析推断不可用（non-free sampling 方差非正）—— 用 permutations 置换推断。"
    if not _analytic_ok and perm_p is not None:
        summary += "（解析方差简并，判别基于置换检验）"
    elif pattern == "random":
        summary += " 与 non-free sampling 零假设无显著差异。"
    return GeoAnalysisResult(True, data_out, summary)


def bivariate_join_count_narrated(
    geojson: dict,
    binary_field: str,
    weights_scheme: str = "knn",
    k: int = 8,
    distance_band: float = 0,
    permutations: int = 0,
) -> GeoAnalysisResult:
    """双变量/双色 Join Count（two-color join count；Cliff & Ord 1973）。

    与 :func:`join_count_narrated` 的 0/1 严格二值不同，本方法接受**任意
    恰好取两个值的类别字段**（如 {urban, rural}、{3, 7}）：值按排序映射为
    B（黑，较小值）/ W（白，较大值），在二值对称权重上统计
    n_BB（同类连接）/ n_BW（异类连接）/ n_WW；期望/方差按 non-free
    sampling（Cliff-Ord 1973，条件于类别边际的解析矩，与 join_count 同
    式）计算，z + 双侧正态 p；``permutations > 0`` 时附加固定种子 42 的
    置换复核。non-free sampling 近似忽略权重结构细节（只含连接数 J），
    在 meta 中显式披露。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection whose features carry a "
                            "two-category numeric field",
        )
    gdf, _ = res
    aligned = _filter_numeric_gdf(gdf, binary_field)
    if aligned is None or len(aligned[1]) == 0:
        raise MissingRequiredField(
            f"field '{binary_field}' is missing or non-numeric",
            correction_hint=f"provide a two-category numeric property "
                            f"'{binary_field}' on every feature",
        )
    gdf, values = aligned
    n = len(values)
    if n < 4:
        raise InsufficientSamples(
            f"bivariate join count non-free sampling variance needs n ≥ 4 "
            f"(got {n})",
            correction_hint="add observations",
        )
    uniq = np.unique(values)
    if len(uniq) > 2:
        raise UnsupportedMethod(
            f"field '{binary_field}' has {len(uniq)} distinct values "
            f"{uniq[:8].tolist()}; two-color join count needs exactly 2",
            correction_hint="collapse the categories to two classes first, or "
                            "use moran_i / local_geary for continuous values",
        )
    if len(uniq) < 2:
        raise DegenerateData(
            f"all '{binary_field}' values are identical ({uniq[0]:.4g}); "
            "two-color join count is undefined",
            correction_hint="the field must contain exactly two categories",
        )

    # 确定性颜色映射：排序后较小值 = B（黑），较大值 = W（白）。
    cat_b, cat_w = float(uniq[0]), float(uniq[1])
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    scheme = str(weights_scheme or "knn").lower()
    if scheme == "knn":
        wm = build_knn_weights(coords, k=min(int(k), n - 1),
                               row_standardized=False)
    elif scheme in ("queen", "rook"):
        wm = build_contiguity_weights(gdf, scheme=scheme, row_standardized=False)
    elif scheme == "distance_band":
        threshold = float(distance_band) if distance_band and float(distance_band) > 0 \
            else auto_band_8nn(coords)
        wm = build_distance_band_weights(
            coords, threshold=threshold, include_self=False,
            row_standardized=False)
    else:
        raise ValueError(
            f"unknown weights_scheme {weights_scheme!r}; "
            f"expected one of {WEIGHT_SCHEMES}")

    w = wm.matrix.tocoo()
    upper = w.row < w.col  # 对称二值权重 → i<j 恰好枚举无序对
    wu_i, wu_j, wu_v = w.row[upper], w.col[upper], w.data[upper]
    joins = float(np.sum(wu_v))
    if joins == 0:
        raise DegenerateData(
            "no neighbour joins under this weights scheme",
            correction_hint="increase k / distance band, or check geometry connectivity",
        )
    b = (values == cat_b).astype(float)  # B=1 / W=0
    black = float(np.sum(b))
    white = n - black
    n_bb = float(np.sum(wu_v * b[wu_i] * b[wu_j]))
    n_ww = float(np.sum(wu_v * (1.0 - b[wu_i]) * (1.0 - b[wu_j])))
    n_bw = joins - n_bb - n_ww

    def _free_sampling(observed: float, num2: float, num4: float) -> dict:
        """non-free sampling 期望/方差（Cliff-Ord 1973；与 join_count 同式）。

        p = num2/(n(n−1))，Var = J·p(1−p) + 2J(J−1)(q − p²)；二阶矩为负时
        解析推断不可用（诚实降级，与 join_count 的 R2 MAJOR-2 语义一致）。
        （审计 F-2：函数名保留为历史别名，矩公式是 non-free/条件形式，
        措辞已与 join_count 同步更正。）
        """
        den2 = n * (n - 1.0)
        den4 = den2 * (n - 2.0) * (n - 3.0)
        p = num2 / den2
        expected = joins * p
        var = joins * p * (1.0 - p) + 2.0 * joins * (joins - 1.0) * (
            num4 / den4 - p * p)
        if var <= 0.0:
            return {
                "observed": float(observed), "expected": float(expected),
                "variance": None, "z": None, "p_value": None,
                "analytic_note": (
                    "non-free sampling 方差非正（类别过稀 m<4 或分布极端）——"
                    "解析 z/p 不可用，请用 permutations 置换推断"),
            }
        z_stat = (observed - expected) / np.sqrt(var)
        return {
            "observed": float(observed), "expected": float(expected),
            "variance": float(var), "z": float(z_stat),
            "p_value": float(2.0 * norm.sf(abs(z_stat))),
        }

    stats_bb = _free_sampling(n_bb, black * (black - 1.0),
                              black * (black - 1.0) * (black - 2.0) * (black - 3.0))
    stats_ww = _free_sampling(n_ww, white * (white - 1.0),
                              white * (white - 1.0) * (white - 2.0) * (white - 3.0))
    stats_bw = _free_sampling(n_bw, 2.0 * black * white,
                              4.0 * black * (black - 1.0) * white * (white - 1.0))

    perm_p = None
    perms = int(permutations or 0)
    if perms > 0:
        perms = _validate_permutations(perms)
        rng = np.random.default_rng(_PERMUTATION_SEED)
        obs_dev = (abs(n_bb - stats_bb["expected"]),
                   abs(n_bw - stats_bw["expected"]),
                   abs(n_ww - stats_ww["expected"]))
        extreme = np.zeros(3, dtype=np.int64)
        for _ in cancellable(range(perms)):
            bp = rng.permutation(b)
            bb_t = float(np.sum(wu_v * bp[wu_i] * bp[wu_j]))
            ww_t = float(np.sum(wu_v * (1.0 - bp[wu_i]) * (1.0 - bp[wu_j])))
            bw_t = joins - bb_t - ww_t
            devs = (abs(bb_t - stats_bb["expected"]),
                    abs(bw_t - stats_bw["expected"]),
                    abs(ww_t - stats_ww["expected"]))
            extreme += np.asarray(devs) >= np.asarray(obs_dev)
        perm_p = {
            "n_bb": float((int(extreme[0]) + 1) / (perms + 1)),
            "n_bw": float((int(extreme[1]) + 1) / (perms + 1)),
            "n_ww": float((int(extreme[2]) + 1) / (perms + 1)),
            "permutations": perms,
        }

    _analytic_ok = all(s["p_value"] is not None
                       for s in (stats_bb, stats_bw, stats_ww))
    pattern = "random"
    if _analytic_ok:
        if max(stats_bw["p_value"], 0.0) < 0.05 and stats_bw["z"] > 0:
            pattern = "negative_spatial_autocorrelation"
        elif max(stats_bb["p_value"], stats_ww["p_value"]) < 0.05:
            pattern = "positive_spatial_autocorrelation"
    elif perm_p is not None:
        if perm_p["n_bw"] < 0.05 and n_bw > stats_bw["expected"]:
            pattern = "negative_spatial_autocorrelation"
        elif max(perm_p["n_bb"], perm_p["n_ww"]) < 0.05:
            pattern = "positive_spatial_autocorrelation"
        else:
            pattern = "random"
    else:
        pattern = "analytic_inference_unavailable"

    data_out = {
        "n_features": n,
        "n_black": black,
        "n_white": white,
        "category_black": cat_b,
        "category_white": cat_w,
        "joins": joins,
        "weights_scheme": wm.scheme,
        "join_counts": {"n_bb": n_bb, "n_bw": n_bw, "n_ww": n_ww},
        "expected": {"n_bb": stats_bb["expected"],
                     "n_bw": stats_bw["expected"],
                     "n_ww": stats_ww["expected"]},
        "z": {"n_bb": stats_bb["z"], "n_bw": stats_bw["z"], "n_ww": stats_ww["z"]},
        "p_value_analytic": {"n_bb": stats_bb["p_value"],
                             "n_bw": stats_bw["p_value"],
                             "n_ww": stats_ww["p_value"]},
        "assumptions_disclosed": [
            "non-free sampling：期望/方差条件于两类别边际的 Cliff-Ord 1973 "
            "解析矩（不放回），忽略权重结构细节（只含连接数 J）",
        ],
        "pattern": pattern,
        "weights": wm.metadata(),
        "uncertainty": [
            StatisticalSignificance(
                target="bivariate_join_count_bw",
                statistic_name="n_BW join count (two-color non-free sampling z-test)",
                statistic_value=n_bw,
                p_value=stats_bw["p_value"],
                method="analytic_normal",
                alternative="two-sided",
            ).to_evidence(),
            StatisticalSignificance(
                target="bivariate_join_count_bb",
                statistic_name="n_BB join count (two-color non-free sampling z-test)",
                statistic_value=n_bb,
                p_value=stats_bb["p_value"],
                method="analytic_normal",
                alternative="two-sided",
            ).to_evidence(),
        ],
    }
    if stats_bw.get("analytic_note") or stats_bb.get("analytic_note"):
        data_out["analytic_notes"] = [
            s["analytic_note"] for s in (stats_bb, stats_bw, stats_ww)
            if s.get("analytic_note")]
    if perm_p is not None:
        data_out["p_value_permutation"] = perm_p

    def _fmt_p(s: dict) -> str:
        return f"p={s['p_value']:.4f}" if s["p_value"] is not None else "p=不可用"

    summary = (
        f"双色 Join Count（{wm.scheme}，{joins:.0f} 个无序连接，"
        f"B={black:.0f}/W={white:.0f}）："
        f"n_BB={n_bb:.0f}（期望 {stats_bb['expected']:.1f}，{_fmt_p(stats_bb)}）、"
        f"n_BW={n_bw:.0f}（期望 {stats_bw['expected']:.1f}，{_fmt_p(stats_bw)}）、"
        f"n_WW={n_ww:.0f}（期望 {stats_ww['expected']:.1f}，{_fmt_p(stats_ww)}）。")
    if pattern == "negative_spatial_autocorrelation":
        summary += " 异类连接显著偏高 —— 两类别空间互斥（棋盘式负关联）。"
    elif pattern == "positive_spatial_autocorrelation":
        summary += " 同类连接显著偏高 —— 两类别各自聚集（正关联）。"
    elif pattern == "analytic_inference_unavailable":
        summary += " 解析推断不可用（non-free sampling 方差非正）—— 用 permutations 置换推断。"
    if not _analytic_ok and perm_p is not None:
        summary += "（解析方差简并，判别基于置换检验）"
    elif pattern == "random":
        summary += " 与 non-free sampling 零假设无显著差异。"
    summary += "（non-free sampling 假设：条件于类别边际，忽略权重结构细节）"
    return GeoAnalysisResult(True, data_out, summary)


def empirical_bayes_rate_smooth(
    geojson: dict,
    count_field: str,
    population_field: str,
    weights_scheme: str = "",
    k: int = 8,
    distance_band: float = 0,
) -> GeoAnalysisResult:
    """经验贝叶斯率平滑（Empirical Bayes rate smoothing；Marshall 1991 MOM）。

    分子为观测计数（count_field）、分母为风险人口（population_field）。
    先验均值/方差用**矩估计（method of moments，Marshall 1991）**：

    - 全局 EB（``weights_scheme`` 为空/none）：μ = ΣC/ΣP；
      σ² = Σ P_i(r_i−μ)²/ΣP_i − nμ/ΣP_i（扣除 Poisson 抽样噪声的均摊）；
    - 局部 EB（给定权重方案）：先验来自邻居（不含自身）的
      人口加权均值/方差，逐区 σ²_i = v_i − m_i μ_i/ΣP_j（Marshall 1991）。

    平滑率 = w·r_i + (1−w)·先验均值，收缩权重 w = σ²/(σ² + μ/P_i)。
    σ²≤0（零方差先验）时 w=0（收缩到先验均值）并披露计数。
    MOM 先验假设计数为 Poisson——披露于 meta/叙事。

    零人口区（population ≤ 0/非有限）类型化排除：不产率值，计数披露；
    全部为零人口时抛 DegenerateData。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a polygon FeatureCollection with count and "
                            "population numeric fields",
        )
    gdf, _ = res
    for f in (count_field, population_field):
        if f not in gdf.columns:
            raise MissingRequiredField(
                f"field '{f}' is missing",
                correction_hint=f"provide numeric fields '{count_field}' "
                                f"(events) and '{population_field}' (population at risk)",
            )
    counts_raw = pd.to_numeric(gdf[count_field], errors="coerce").to_numpy(dtype=float)
    pop_raw = pd.to_numeric(gdf[population_field], errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(counts_raw) & np.isfinite(pop_raw)
    n_dropped_nonfinite = int((~finite).sum())
    gdf = gdf[finite].reset_index(drop=True)
    counts = counts_raw[finite]
    pops = pop_raw[finite]
    n = int(len(counts))
    if n == 0:
        raise NoValidObservations(
            "no features with finite count/population values",
            correction_hint="check the count/population fields for all-NaN columns",
        )

    # 负计数 → 类型化拒绝（V3 review MINOR-2）：Marshall MOM 的先验均值
    # μ=ΣC/ΣP 在负计数下可为负，收缩因子 σ²/(σ²+μ/P) 会落到 [0,1] 之外
    # （率支撑之外），静默外推是伪造——计数语义上非负是方法前提。
    if bool(np.any(counts_raw[finite] < 0)):
        raise UnsupportedMethod(
            "empirical Bayes rate smoothing requires non-negative counts "
            "(negative values found)",
            correction_hint="counts are Poisson semantics; fix the field or use a "
                            "continuous-value method",
        )
    valid = pops > 0
    zero_pop_idx = np.flatnonzero(~valid).tolist()
    if not bool(valid.any()):
        raise DegenerateData(
            "every feature has zero/non-positive population at risk; "
            "no rate can be defined",
            correction_hint="provide the population denominator field, or use "
                            "count aggregation instead of rates",
        )
    idx_v = np.flatnonzero(valid)
    r = counts[idx_v] / pops[idx_v]
    p_v = pops[idx_v]
    nv = len(idx_v)
    if nv < 3:
        raise InsufficientSamples(
            f"EB rate smoothing needs at least 3 valid-rate features (got {nv})",
            correction_hint="add areas with positive population",
        )

    scheme = str(weights_scheme or "").lower()
    local = scheme not in ("", "none")
    wm = None
    if local:
        if scheme == "knn":
            wm = build_knn_weights(
                np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values)),
                k=min(int(k), n - 1), row_standardized=False)
        elif scheme in ("queen", "rook"):
            wm = build_contiguity_weights(gdf, scheme=scheme, row_standardized=False)
        elif scheme == "distance_band":
            threshold = float(distance_band) if distance_band and float(distance_band) > 0 \
                else auto_band_8nn(
                    np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values)))
            wm = build_distance_band_weights(
                np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values)),
                threshold=threshold, include_self=False, row_standardized=False)
        else:
            raise ValueError(
                f"unknown weights_scheme {weights_scheme!r}; "
                f"expected one of {WEIGHT_SCHEMES} or '' (global EB)")
        if wm.s0 == 0:
            raise DegenerateData(
                "spatial weights matrix is empty (every observation is an island)",
                correction_hint="increase k / distance band, or drop "
                                "weights_scheme for global EB",
            )

    # MOM 先验（Marshall 1991）。
    w_map = {int(gi): vi for vi, gi in enumerate(idx_v)}
    shrink = np.zeros(nv)
    prior_mean = np.zeros(nv)
    prior_var = np.zeros(nv)
    n_island = 0
    if local:
        w_csr = wm.matrix.tocsr()
        neighbors = [
            np.asarray(w_csr.getrow(gi).nonzero()[1]).ravel()
            for gi in idx_v
        ]
        n_island = 0
        for vi, nbs in enumerate(neighbors):
            nbs_valid = [w_map[int(j)] for j in nbs if int(j) in w_map]
            if not nbs_valid:
                # 孤岛：无邻居先验可用 → 不收缩（保留原始率）并披露。
                shrink[vi] = 1.0
                prior_mean[vi] = r[vi]
                prior_var[vi] = 0.0
                n_island += 1
                continue
            pj = p_v[nbs_valid]
            rj = r[nbs_valid]
            mu_i = float(np.sum(pj * rj) / np.sum(pj))
            v_i = float(np.sum(pj * (rj - mu_i) ** 2) / np.sum(pj))
            sigma2_i = v_i - mu_i * len(nbs_valid) / float(np.sum(pj))
            if sigma2_i <= 0.0:
                shrink[vi] = 0.0
                prior_mean[vi] = mu_i
                prior_var[vi] = 0.0
                continue
            prior_mean[vi] = mu_i
            prior_var[vi] = float(sigma2_i)
            shrink[vi] = float(sigma2_i / (sigma2_i + mu_i / p_v[vi]))
    else:
        mu = float(np.sum(p_v * r) / np.sum(p_v))
        v_w = float(np.sum(p_v * (r - mu) ** 2) / np.sum(p_v))
        sigma2 = v_w - mu * nv / float(np.sum(p_v))
        if sigma2 <= 0.0:
            shrink[:] = 0.0
            prior_mean[:] = mu
        else:
            prior_mean[:] = mu
            prior_var[:] = sigma2
            shrink = sigma2 / (sigma2 + mu / p_v)

    smoothed_v = shrink * r + (1.0 - shrink) * prior_mean
    n_zero_var_prior = int(np.sum(shrink == 0.0))

    # 组装 FeatureCollection（零人口/非有限行保留占位，rate=None 语义）。
    raw_full = np.full(n, np.nan)
    smooth_full = np.full(n, np.nan)
    shrink_full = np.full(n, np.nan)
    prior_mean_full = np.full(n, np.nan)
    prior_var_full = np.full(n, np.nan)
    raw_full[idx_v] = r
    smooth_full[idx_v] = smoothed_v
    shrink_full[idx_v] = shrink
    prior_mean_full[idx_v] = prior_mean
    prior_var_full[idx_v] = prior_var

    gdf_wgs84 = gdf.to_crs("EPSG:4326")

    def _nd(x):
        return None if not np.isfinite(x) else round(float(x), 6)

    extra_props = {
        "raw_rate": [_nd(v) for v in raw_full],
        "smoothed_rate": [_nd(v) for v in smooth_full],
        "shrinkage_weight": [_nd(v) for v in shrink_full],
        "prior_mean": [_nd(v) for v in prior_mean_full],
        "prior_variance": [_nd(v) for v in prior_var_full],
    }
    features = _assemble_features(gdf_wgs84, extra_props)

    method_id = "marshall1991_mom_local" if local else "marshall1991_mom_global"
    summary = (
        f"经验贝叶斯率平滑（{method_id}，{'局部邻居先验' if local else '全局先验'}）："
        f"{nv} 个有效区，收缩权重范围 [{float(np.min(shrink)):.3f}, "
        f"{float(np.max(shrink)):.3f}]。")
    if zero_pop_idx:
        summary += f" {len(zero_pop_idx)} 个零人口区被排除（不产率值）。"
    if n_zero_var_prior:
        summary += f" {n_zero_var_prior} 个区先验方差钳零（零方差先验→收缩至先验均值）。"
    if local and wm is not None and n_island:
        summary += f" {n_island} 个孤岛无邻居先验（保留原始率）。"
    summary += " MOM 先验假设计数为 Poisson——小计数区平滑依赖该假设。"

    data_out = {
        "type": "FeatureCollection",
        "features": features,
        "n_features": n,
        "n_valid_rates": nv,
        "method": method_id,
        "prior_parameters": {
            "estimator": "method_of_moments (Marshall 1991)",
            "prior_scope": "neighbor_informed" if local else "global",
            "global_prior_mean": _nd(
                float(np.sum(p_v * r) / np.sum(p_v))),
            "prior_variance_range": [
                _nd(float(np.min(prior_var))) if nv else None,
                _nd(float(np.max(prior_var))) if nv else None,
            ],
            "poisson_assumption_disclosure": (
                "MOM 先验假设各区计数近似 Poisson；方差估计扣除 Poisson 抽样噪声"),
        },
        "zero_population_excluded": len(zero_pop_idx),
        "zero_variance_prior_count": n_zero_var_prior,
        "dropped_nonfinite": n_dropped_nonfinite,
        "uncertainty": [],
    }
    if local and wm is not None:
        data_out["weights_scheme"] = wm.scheme
        data_out["weights"] = wm.metadata()
    return GeoAnalysisResult(True, data_out, summary)


def bivariate_moran_narrated(
    geojson: dict,
    value_field: str,
    lag_field: str,
    weights_scheme: str = "knn",
    k: int = 8,
    distance_band: float = 0,
    permutations: int = 99,
) -> GeoAnalysisResult:
    """双变量 Moran's I（Wartenberg 1985）：x 与 W·y 的空间共变。

    I = (n/S₀)·Σᵢ x_i (W y)_i / (‖x−x̄‖·‖y−ȳ‖)（行标准化权重；x=y 时与
    单变量 Moran 严格一致）。置换推断只打乱 y（固定种子 42，双侧 +1）。
    **限制**：这是共位相关（co-located correlation），不是因果超前-滞后
    证据 —— 叙事与 limitations 中披露。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection with two numeric fields",
        )
    gdf, _ = res
    aligned = _filter_two_numeric_gdf(gdf, value_field, lag_field)
    if aligned is None:
        raise MissingRequiredField(
            f"fields '{value_field}' / '{lag_field}' missing or non-numeric",
            correction_hint="provide both numeric properties on every feature",
        )
    gdf, vx, vy = aligned
    n = len(vx)
    if n < 3:
        raise InsufficientSamples(
            f"bivariate Moran needs at least 3 valid features (got {n})",
            correction_hint="add observations",
        )
    if float(np.ptp(vx)) == 0.0 or float(np.ptp(vy)) == 0.0:
        raise DegenerateData(
            "one of the fields has zero variance; bivariate Moran is undefined",
            correction_hint="check both fields for constant values",
        )
    perms = _validate_permutations(permutations)
    wm = _autocorr_weights(gdf, n, weights_scheme, k, distance_band)
    if wm.s0 == 0:
        raise DegenerateData(
            "spatial weights matrix is empty (every observation is an island)",
            correction_hint="increase the distance band / k, or check geometry connectivity",
        )
    w = wm.matrix.tocoo()
    s0 = float(w.sum())
    w_vals, i_idx, j_idx = w.data, w.row, w.col

    def _stat(pvy: "np.ndarray") -> float:
        zy = pvy - pvy.mean()
        lag_y = np.bincount(i_idx, weights=w_vals * zy[j_idx], minlength=n)
        zx = vx - vx.mean()
        denom = float(np.sqrt(np.sum(zx ** 2)) * np.sqrt(np.sum(zy ** 2)))
        if denom <= 0:
            return 0.0
        return (n / s0) * float(np.sum(zx * lag_y)) / denom

    observed = _stat(vy)
    perm_is = _permutation_stats(_stat, vy, perms)
    p_value = _two_sided_permutation_pvalue(perm_is, observed, 0.0, perms)

    if p_value < 0.05:
        pattern = "positive_co_location" if observed > 0 else "negative_co_location"
    else:
        pattern = "random"

    data_out = {
        "bivariate_morans_i": float(observed),
        "expected_i": 0.0,
        "p_value": float(p_value),
        "pattern": pattern,
        "value_field": str(value_field),
        "lag_field": str(lag_field),
        "n_features": n,
        "permutations": perms,
        "weights": wm.metadata(),
        "uncertainty": StatisticalSignificance(
            target="bivariate_morans_i",
            statistic_name="Bivariate Moran's I (x vs W·y)",
            statistic_value=float(observed),
            p_value=float(p_value),
            method="permutation",
            permutations=perms,
            alternative="two-sided",
        ).to_evidence(),
    }
    if pattern == "positive_co_location":
        narrative = (
            f"'{value_field}' 与 '{lag_field}' 的空间滞后显著正相关"
            f"（I={observed:.4f}, p={p_value:.4f}）：高 x 与高 y 邻域共位。")
    elif pattern == "negative_co_location":
        narrative = (
            f"'{value_field}' 与 '{lag_field}' 的空间滞后显著负相关"
            f"（I={observed:.4f}, p={p_value:.4f}）：x 与 y 邻域互斥。")
    else:
        narrative = (
            f"未检出显著的空间共变（I={observed:.4f}, p={p_value:.4f}）。")
    narrative += (
        " 注意：双变量 Moran 是共位相关，不能解释为因果/超前-滞后关系。")
    return GeoAnalysisResult(True, data_out, narrative)


def _geodetector_q(values: "np.ndarray", labels: "np.ndarray") -> float:
    """因子探测器 q = 1 − Σ N_h σ_h² / (N σ²)（总体方差，q ∈ [0,1]）。"""
    df = pd.DataFrame({"v": values, "h": labels})
    grouped = df.groupby("h", observed=True)["v"]
    counts = grouped.size().to_numpy(dtype=float)
    sse = float((grouped.var(ddof=0).to_numpy(dtype=float) * counts).sum())
    total_var = float(np.var(values, ddof=0))
    if total_var <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - sse / (len(values) * total_var)))


def _classify_interaction(q1: float, q2: float, q12: float) -> str:
    """Wang 2010 双因子交互分类（q(X₁∩X₂) 相对 q(X₁)+q(X₂) 的位置）。

    independent（q12 = q1+q2，容差 1e-9）→ nonlinear_enhanced（q12 > q1+q2）
    → bilinear_enhanced（max < q12 < q1+q2）→ nonlinear_weakened（q12 < min）
    → uni_nonlinear（其余）。
    """
    if abs(q12 - (q1 + q2)) <= 1e-9:
        return "independent"
    if q12 > q1 + q2:
        return "nonlinear_enhanced"
    if q12 > max(q1, q2):
        return "bilinear_enhanced"
    if q12 < min(q1, q2):
        return "nonlinear_weakened"
    return "uni_nonlinear"


def _geodetector_labels(series: "pd.Series", bins: int, field: str) -> "np.ndarray":
    """分层标签：数值字段按分位数分箱（bins ≥ 2）；低基数数值字段按类别。

    数值 dtype 但唯一值 ≤ 12（如 0/1 编码、评级）按类别处理 —— 硬性要求
    分箱会逼用户给本已是离散层的东西编造箱；高基数数值字段必须显式
    bins，否则 UnsupportedMethod（防止逐观测分层）。
    """
    if np.issubdtype(series.dtype, np.number):
        if int(bins) >= 2:
            binned = pd.qcut(series.astype(float), q=int(bins), duplicates="drop")
            return binned.astype(str).to_numpy()
        if series.nunique() <= 12:
            return series.astype(str).to_numpy()
        raise UnsupportedMethod(
            f"numeric strata field '{field}' has >12 unique values and needs "
            "explicit binning (bins ≥ 2)",
            correction_hint="pass bins (e.g. 5 for quintiles) or use a "
                            "categorical field",
        )
    return series.astype(str).to_numpy()


def geodetector_narrated(
    geojson: dict,
    value_field: str,
    strata_field: str,
    interaction_field: str = "",
    bins: int = 0,
    permutations: int = 99,
) -> GeoAnalysisResult:
    """地理探测器（Wang 2010）：因子探测器 q + 可选双因子交互分类。

    q = 1 − Σ N_h σ_h²/(N σ²) ∈ [0,1]（分层完全决定 y 时 q=1）；F 检验
    给解析 p，``permutations > 0`` 时附固定种子 42 的置换 p 与 q 置换分布
    摘要（MonteCarloSummary）。``interaction_field`` 给出 q(X₁∩X₂) 并按
    Wang 2010 交互表分类：independent / nonlinear_enhanced /
    bilinear_enhanced / nonlinear_weakened / uni_nonlinear。**数学事实**：
    类别交集是两个分层的公共加细，q 在加细下单调不减 —— weakened 类只在
    分层被粗化时出现（披露于 descriptor limitations）。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection with a value field and "
                            "strata field",
        )
    gdf, _ = res
    if strata_field not in gdf.columns:
        raise MissingRequiredField(
            f"strata field '{strata_field}' is missing",
            correction_hint=f"provide a categorical/numeric property '{strata_field}'",
        )
    aligned = _filter_numeric_gdf(gdf, value_field)
    if aligned is None or len(aligned[1]) == 0:
        raise MissingRequiredField(
            f"field '{value_field}' is missing or non-numeric",
            correction_hint=f"provide a numeric property '{value_field}'",
        )
    gdf, values = aligned
    strata_series = gdf[strata_field]
    keep = strata_series.notna()
    gdf = gdf[keep].reset_index(drop=True)
    strata_series = strata_series[keep].reset_index(drop=True)
    values = values[np.asarray(keep, dtype=bool)]
    n = len(values)
    if n < 10:
        raise InsufficientSamples(
            f"geodetector needs at least 10 valid observations (got {n})",
            correction_hint="add observations or reduce strata granularity",
        )
    if float(np.ptp(values)) == 0.0:
        raise DegenerateData(
            f"all '{value_field}' values are identical; q is undefined",
            correction_hint="check the value field for constant values",
        )

    labels = _geodetector_labels(strata_series, bins, strata_field)
    n_strata = len(np.unique(labels))
    if n_strata < 2:
        raise DegenerateData(
            f"strata field '{strata_field}' has a single level",
            correction_hint="the strata field must distinguish at least 2 strata",
        )
    if n_strata > n // 2:
        raise DegenerateData(
            f"strata too fine ({n_strata} strata for {n} observations)",
            correction_hint="increase bins or use a coarser categorical field",
        )

    q_val = _geodetector_q(values, labels)
    h, dof2 = n_strata, n - n_strata
    if q_val >= 1.0 - 1e-12:
        f_stat, p_f = float("inf"), 0.0
    elif q_val <= 0.0:
        f_stat, p_f = 0.0, 1.0
    else:
        f_stat = (dof2 * q_val) / ((h - 1) * (1.0 - q_val))
        p_f = float(sps.f.sf(f_stat, h - 1, dof2))

    perms = int(permutations or 0)
    q_perm_quantiles: dict = {}
    p_perm = None
    if perms > 0:
        perms = _validate_permutations(perms)
        rng = np.random.default_rng(_PERMUTATION_SEED)
        q_perm = np.empty(perms, dtype=float)
        for t in cancellable(range(perms)):
            q_perm[t] = _geodetector_q(values, rng.permutation(labels))
        p_perm = float((int(np.sum(q_perm >= q_val)) + 1) / (perms + 1))
        q_perm_quantiles = {
            "p5": float(np.percentile(q_perm, 5)),
            "p50": float(np.percentile(q_perm, 50)),
            "p95": float(np.percentile(q_perm, 95)),
        }

    factor_block = {
        "q": round(float(q_val), 6),
        "n_strata": int(n_strata),
        "f_statistic": (None if not np.isfinite(f_stat) else round(float(f_stat), 6)),
        "p_value_f": round(float(p_f), 6) if np.isfinite(p_f) else 0.0,
        "p_value_permutation": p_perm,
    }

    interaction_block: dict | None = None
    if str(interaction_field or "").strip():
        i_field = str(interaction_field).strip()
        if i_field not in gdf.columns:
            raise MissingRequiredField(
                f"interaction field '{i_field}' is missing",
                correction_hint=f"provide the property '{i_field}' or omit "
                                "interaction_field",
            )
        s2 = gdf[i_field]
        keep2 = s2.notna()
        labels2 = _geodetector_labels(
            s2[keep2].reset_index(drop=True), bins, i_field)
        v2 = values[np.asarray(keep2, dtype=bool)]
        l1 = labels[np.asarray(keep2, dtype=bool)]
        if len(v2) != n:
            raise MissingRequiredField(
                f"interaction field '{i_field}' has nulls — rows dropped would "
                "misalign the two strata",
                correction_hint=f"fill nulls in '{strata_field}'/'{i_field}' "
                                "before running interaction detection",
            )
        q1 = _geodetector_q(v2, l1)
        q2 = _geodetector_q(v2, labels2)
        q12 = _geodetector_q(v2, np.char.add(l1.astype(str), "|" + labels2.astype(str)))
        i_class = _classify_interaction(float(q1), float(q2), float(q12))
        interaction_block = {
            "field_1": strata_field,
            "field_2": i_field,
            "q_1": round(float(q1), 6),
            "q_2": round(float(q2), 6),
            "q_1_and_2": round(float(q12), 6),
            "interaction_class": i_class,
        }

    data_out = {
        "n_features": n,
        "value_field": str(value_field),
        "strata_field": str(strata_field),
        "factor": factor_block,
        "interaction": interaction_block,
        "permutations": perms,
        "uncertainty": [
            StatisticalSignificance(
                target="geodetector_q",
                statistic_name=f"q ({strata_field})",
                statistic_value=float(q_val),
                p_value=round(float(p_f), 6),
                method="analytic_normal",
                alternative="greater",
            ).to_evidence(),
        ],
    }
    if p_perm is not None:
        data_out["uncertainty"].append(
            MonteCarloSummary(
                target="geodetector_q",
                draws=perms,
                seed=_PERMUTATION_SEED,
                quantiles=q_perm_quantiles,
                probability_statements=[
                    f"P(q_perm >= q_obs) = {p_perm:.4f}（固定种子 {_PERMUTATION_SEED}）",
                ],
            ).to_evidence())

    summary = (
        f"地理探测器：'{strata_field}' 对 '{value_field}' 的解释力 "
        f"q={q_val:.4f}（{n_strata} 个分层；F 检验 p={factor_block['p_value_f']:.4f}"
        + (f"，置换 p={p_perm:.4f}" if p_perm is not None else "")
        + "）。q∈[0,1]，q=1 表示分层完全决定取值。")
    if interaction_block is not None:
        summary += (
            f"交互：q({interaction_block['field_1']}∩{interaction_block['field_2']})"
            f"={interaction_block['q_1_and_2']:.4f} → "
            f"{interaction_block['interaction_class']}（Wang 2010 交互表）。")
    return GeoAnalysisResult(True, data_out, summary)


def weights_sensitivity_narrated(
    geojson: dict,
    value_field: str,
    k: int = 8,
    distance_band: float = 0,
    permutations: int = 99,
) -> GeoAnalysisResult:
    """权重敏感性（Foundation V2 · stats.weights_sensitivity）。

    在 {knn(k), queen, rook, distance_band(auto 8nn)} 下重算全局 Moran's I：
    逐方案 I / p / 判读，queen/rook 对点输入如实跳过并披露；输出 ΔI 范围、
    结论稳定性（各方案判读与多数一致的比例）与 SensitivityEnvelope 块。
    结论跨权重方案翻转 = 空间自相关声明不可靠（诚实降级，不取"最好看"的
    权重）。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection with at least 3 numeric features",
        )
    gdf, _ = res
    aligned = _filter_numeric_gdf(gdf, value_field)
    if aligned is None or len(aligned[1]) == 0:
        raise MissingRequiredField(
            f"field '{value_field}' is missing or non-numeric",
            correction_hint=f"provide a numeric property '{value_field}'",
        )
    gdf, values = aligned
    n = len(values)
    if n < 3:
        raise InsufficientSamples(
            f"weights sensitivity needs at least 3 valid features (got {n})",
            correction_hint="add observations",
        )
    if float(np.ptp(values)) == 0.0:
        raise DegenerateData(
            f"all '{value_field}' values are identical; Moran's I is undefined",
            correction_hint="check the numeric field for constant values",
        )
    perms = _validate_permutations(permutations)
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    threshold = float(distance_band) if distance_band and float(distance_band) > 0 \
        else auto_band_8nn(coords)

    scheme_defs = [
        ("knn", lambda: build_knn_weights(coords, k=min(int(k), n - 1))),
        ("queen", lambda: build_contiguity_weights(gdf, scheme="queen",
                                                   row_standardized=True)),
        ("rook", lambda: build_contiguity_weights(gdf, scheme="rook",
                                                  row_standardized=True)),
        ("distance_band", lambda: build_distance_band_weights(
            coords, threshold=threshold, include_self=False,
            row_standardized=True)),
    ]
    scheme_results: list = []
    for scheme_name, builder in scheme_defs:
        try:
            wm = builder()
        except UnsupportedMethod as exc:
            scheme_results.append({
                "scheme": scheme_name, "status": "skipped",
                "reason": str(exc.detail or exc)[:160],
            })
            continue
        if wm.s0 == 0:
            scheme_results.append({
                "scheme": scheme_name, "status": "skipped",
                "reason": "empty weights matrix (all islands)",
            })
            continue
        moran = _moran_from_wm(values, wm, perms)
        if moran["moran_i"] is None:
            scheme_results.append({
                "scheme": scheme_name, "status": "skipped",
                "reason": "degenerate statistic",
            })
            continue
        if moran["p_value"] < 0.05:
            verdict = ("clustering" if moran["moran_i"] > moran["expected_i"]
                       else "dispersion")
        else:
            verdict = "random"
        scheme_results.append({
            "scheme": scheme_name, "status": "ok",
            "moran_i": round(float(moran["moran_i"]), 6),
            "expected_i": round(float(moran["expected_i"]), 6),
            "p_value": round(float(moran["p_value"]), 6),
            "verdict": verdict,
            "weights": wm.metadata(),
        })

    ok = [s for s in scheme_results if s["status"] == "ok"]
    if len(ok) < 2:
        raise DegenerateData(
            "fewer than 2 weights schemes produced a valid Moran's I",
            correction_hint="check geometry connectivity (polygons vs points)",
        )
    i_values = [s["moran_i"] for s in ok]
    verdicts = [s["verdict"] for s in ok]
    majority = max(set(verdicts), key=verdicts.count)
    rank_stability = verdicts.count(majority) / len(verdicts)
    dissenters = [s["scheme"] for s in ok if s["verdict"] != majority]
    rank_order = [s["scheme"] for s in sorted(ok, key=lambda s: -abs(s["moran_i"]))]

    envelope = SensitivityEnvelope(
        target="morans_i",
        perturbation_scheme=(f"weights schemes: knn(k={k})/queen/rook/"
                             "distance_band(auto 8nn)"),
        rank_stability=rank_stability,
        tipping_points=[f"{s} 判读与多数（{majority}）不一致" for s in dissenters],
        notes=f"|I| 排序 {rank_order}；ΔI={max(i_values) - min(i_values):.4f}",
    )
    stable = rank_stability == 1.0

    data_out = {
        "n_features": n,
        "value_field": str(value_field),
        "schemes": scheme_results,
        "moran_i_range": [round(min(i_values), 6), round(max(i_values), 6)],
        "delta_i": round(max(i_values) - min(i_values), 6),
        "majority_verdict": majority,
        "rank_stability": round(rank_stability, 6),
        "stable": stable,
        "rank_by_abs_i": rank_order,
        "permutations": perms,
        "uncertainty": [
            envelope.to_evidence(),
            StatisticalSignificance(
                target="morans_i_knn",
                statistic_name="Moran's I under primary scheme (knn)",
                statistic_value=ok[0]["moran_i"],
                p_value=ok[0]["p_value"],
                method="permutation",
                permutations=perms,
                alternative="two-sided",
            ).to_evidence(),
        ],
    }
    skipped = [s["scheme"] for s in scheme_results if s["status"] == "skipped"]
    summary = (
        f"权重敏感性：{len(ok)} 个方案（{', '.join(s['scheme'] for s in ok)}）"
        f"I ∈ [{min(i_values):.4f}, {max(i_values):.4f}]（ΔI="
        f"{max(i_values) - min(i_values):.4f}）；多数判读 "
        f"{majority}，稳定性 {rank_stability:.2f}。"
        + (f"跳过：{', '.join(skipped)}（点输入无面邻接）。" if skipped else "")
        + (" 结论跨权重方案稳定。"
           if stable else
           " 结论随权重方案翻转 —— 空间自相关声明不可靠，请谨慎叙述。"))
    return GeoAnalysisResult(True, data_out, summary)


# ── Foundation V3：地理探测器生态/风险探测器 ─────────────────────────
# Wang et al. (2010) 探测器家族的其余两件：生态探测器（两分层解释力的
# SSW 比较 t 检验）与风险探测器（逐分层对均值差的 Welch t + 可选置换）。


def _ssw(values: "np.ndarray", labels: "np.ndarray") -> float:
    """层内平方和 SSW = Σ_h Σ_{i∈h} (y_i − ȳ_h)²（分层的未解释变异）。"""
    df = pd.DataFrame({"v": np.asarray(values, dtype=float),
                       "h": np.asarray(labels, dtype=object)})
    grouped = df.groupby("h", observed=True)["v"]
    counts = grouped.size().to_numpy(dtype=float)
    sse = float((grouped.var(ddof=0).to_numpy(dtype=float) * counts).sum())
    return sse


def geodetector_ecological(
    y: "np.ndarray", x1_strata: "np.ndarray", x2_strata: "np.ndarray",
) -> dict:
    """生态探测器（Wang et al. 2010）：两个分层对 y 的解释力比较。

    SSW_j = Σ_h Σ_{i∈h} (y_i − ȳ_h)²；t 统计量（Wang 2010 族）：
    t = [SSW₁/(n−m₁) − SSW₂/(n−m₂)] / sqrt( (SSW₁/(n−m₁))²/(n−m₁−1)
         + (SSW₂/(n−m₂))²/(n−m₂−1) )。
    **自由度披露**：双侧 p 用 Student t、df = n − 2 —— Wang 2010 原文未
    规定统一的合成自由度，n−2 是可复核的固定选择；注意它并非严格保守
    （Welch–Satterthwaite 有效自由度可以更小）（分层自由度不同的
    精确合成需 Behrens-Fisher 类近似，超出本实现）。
    |Y₁| 显著解释占优 ⟺ 其 SSW 显著更小（t > 0 且 p < 0.05）。
    """
    y = np.asarray(y, dtype=float)
    s1 = np.asarray(x1_strata, dtype=object)
    s2 = np.asarray(x2_strata, dtype=object)
    n = len(y)
    if not (len(s1) == len(s2) == n):
        raise ValueError(
            f"y / strata length mismatch: {n} / {len(s1)} / {len(s2)}",
        )
    if n < 10:
        raise InsufficientSamples(
            f"ecological detector needs at least 10 observations (got {n})",
            correction_hint="add observations",
        )
    if float(np.ptp(y)) == 0.0:
        raise DegenerateData(
            "all y values are identical; SSW comparison is undefined",
            correction_hint="check the value field for constant values",
        )
    m1 = len(np.unique(s1))
    m2 = len(np.unique(s2))
    if m1 < 2 or m2 < 2:
        raise DegenerateData(
            f"each stratification needs >= 2 strata (got m1={m1}, m2={m2})",
            correction_hint="use a strata field that distinguishes at least 2 strata",
        )
    if max(m1, m2) > n // 2:
        raise DegenerateData(
            f"strata too fine ({max(m1, m2)} strata for {n} observations)",
            correction_hint="use a coarser stratification",
        )
    ssw1 = _ssw(y, s1)
    ssw2 = _ssw(y, s2)
    n1_rate = ssw1 / (n - m1)
    n2_rate = ssw2 / (n - m2)
    denom = float(np.sqrt(n1_rate ** 2 / (n - m1 - 1)
                          + n2_rate ** 2 / (n - m2 - 1)))
    if denom <= 0:
        # 两个分层都完全决定 y（SSW 双零）：t 无定义，诚实置 0、p=1
        t_stat, p_value = 0.0, 1.0
    else:
        t_stat = float((n1_rate - n2_rate) / denom)
        df = n - 2
        p_value = float(2.0 * sps.t.sf(abs(t_stat), df))
    if p_value < 0.05 and n1_rate < n2_rate:
        decision = "Y1_significantly_dominant"
    elif p_value < 0.05 and n2_rate < n1_rate:
        decision = "Y2_significantly_dominant"
    else:
        decision = "no_significant_difference"
    return {
        "n": int(n), "m1": int(m1), "m2": int(m2),
        "ssw1": float(ssw1), "ssw2": float(ssw2),
        "rate1": float(n1_rate), "rate2": float(n2_rate),
        "t_statistic": float(t_stat), "df": int(n - 2),
        "p_value": float(p_value), "decision": decision,
    }


def geodetector_ecological_narrated(
    geojson: dict,
    value_field: str,
    strata_field_1: str,
    strata_field_2: str,
    bins: int = 0,
) -> GeoAnalysisResult:
    """生态探测器（stats.geodetector_ecological）：两分层 SSW 的 t 比较。

    |Y₁| 的 SSW 显著更小 → Y₁ 显著解释占优。df = n−2 的固定选择（非严格
    保守，见 docstring 披露）、SSW
    只度量分层解释力不构成因果证据，均在叙事/meta 披露。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection with a value field and "
                            "two strata fields",
        )
    gdf, _ = res
    for f in (strata_field_1, strata_field_2):
        if f not in gdf.columns:
            raise MissingRequiredField(
                f"strata field '{f}' is missing",
                correction_hint=f"provide the property '{f}'",
            )
    aligned = _filter_numeric_gdf(gdf, value_field)
    if aligned is None or len(aligned[1]) == 0:
        raise MissingRequiredField(
            f"field '{value_field}' is missing or non-numeric",
            correction_hint=f"provide a numeric property '{value_field}'",
        )
    gdf, values = aligned
    keep = gdf[strata_field_1].notna() & gdf[strata_field_2].notna()
    n_dropped = int(len(gdf) - int(keep.sum()))
    gdf = gdf[keep].reset_index(drop=True)
    values = values[np.asarray(keep, dtype=bool)]
    labels1 = _geodetector_labels(
        gdf[strata_field_1].reset_index(drop=True), bins, strata_field_1)
    labels2 = _geodetector_labels(
        gdf[strata_field_2].reset_index(drop=True), bins, strata_field_2)
    out = geodetector_ecological(values, labels1, labels2)
    decision_txt = {
        "Y1_significantly_dominant":
            f"'{strata_field_1}' 的 SSW 显著更小 —— 解释力显著占优",
        "Y2_significantly_dominant":
            f"'{strata_field_2}' 的 SSW 显著更小 —— 解释力显著占优",
        "no_significant_difference":
            "两分层的解释力差异不显著",
    }[out["decision"]]
    data_out = {
        "n_features": out["n"],
        "rows_dropped_nonfinite_or_null": n_dropped,
        "value_field": str(value_field),
        "strata_field_1": str(strata_field_1),
        "strata_field_2": str(strata_field_2),
        "ssw_1": round(out["ssw1"], 6),
        "ssw_2": round(out["ssw2"], 6),
        "n_strata_1": out["m1"],
        "n_strata_2": out["m2"],
        "t_statistic": round(out["t_statistic"], 6),
        "df": out["df"],
        "p_value": round(out["p_value"], 6),
        "decision": out["decision"],
        "df_disclosure": (
            "双侧 p 用 Student t、df=n−2：Wang 2010 未规定合成自由度，"
            "该固定选择并非严格保守（有效自由度可更小），"
            "n−2 是保守可复核的选择"),
        "uncertainty": StatisticalSignificance(
            target="geodetector_ecological",
            statistic_name="t of SSW comparison (Y1 vs Y2 stratifications)",
            statistic_value=float(out["t_statistic"]),
            p_value=float(out["p_value"]),
            method="analytic_normal",
            alternative="two-sided",
        ).to_evidence(),
    }
    summary = (
        f"生态探测器：SSW({strata_field_1})={out['ssw1']:.4f} vs "
        f"SSW({strata_field_2})={out['ssw2']:.4f}；t={out['t_statistic']:.4f}"
        f"（df={out['df']}，双侧 p={out['p_value']:.4f}）—— {decision_txt}。"
        "SSW 只度量分层解释力，不是因果证据。")
    return GeoAnalysisResult(True, data_out, summary)


def geodetector_risk(
    y: "np.ndarray", strata: "np.ndarray", permutations: int = 0,
) -> dict:
    """风险探测器（Wang et al. 2010）：逐分层对的均值差显著性。

    每对分层做 Welch t 检验（scipy ttest_ind equal_var=False，对方差不等
    稳健）；``permutations > 0`` 时附固定种子 42 的标签置换双侧 p
    （(count+1)/(perms+1) 校正）。方向：显著且 mean₁ > mean₂ → higher，
    显著且 mean₁ < mean₂ → lower，否则 not_significant。
    """
    from itertools import combinations

    y = np.asarray(y, dtype=float)
    s = np.asarray(strata, dtype=object)
    n = len(y)
    if len(s) != n:
        raise ValueError(f"y / strata length mismatch: {n} / {len(s)}")
    if n < 10:
        raise InsufficientSamples(
            f"risk detector needs at least 10 observations (got {n})",
            correction_hint="add observations",
        )
    if float(np.ptp(y)) == 0.0:
        raise DegenerateData(
            "all y values are identical; risk detection is undefined",
            correction_hint="check the value field for constant values",
        )
    levels = sorted({str(v) for v in s})
    if len(levels) < 2:
        raise DegenerateData(
            "strata field has a single level; risk detection needs >= 2 strata",
            correction_hint="use a strata field with at least 2 strata",
        )
    perms = int(permutations or 0)
    if perms > 0:
        perms = _validate_permutations(perms)
    str_list = np.array([str(v) for v in s], dtype=object)
    pairs: list = []
    for a, b in combinations(levels, 2):
        va = y[str_list == a]
        vb = y[str_list == b]
        entry = {
            "stratum_1": a, "stratum_2": b,
            "n_1": int(va.size), "n_2": int(vb.size),
            "mean_1": round(float(va.mean()), 6),
            "mean_2": round(float(vb.mean()), 6),
            "mean_diff": round(float(va.mean() - vb.mean()), 6),
            "t_statistic": None, "p_value": None,
            "p_value_permutation": None, "direction": "not_significant",
        }
        if va.size < 2 or vb.size < 2:
            entry["note"] = ("分层数 < 2，方差不可估 —— Welch t 与置换 p 均"
                             "不可用（诚实留空）")
            pairs.append(entry)
            continue
        t_res = sps.ttest_ind(va, vb, equal_var=False)
        entry["t_statistic"] = round(float(t_res.statistic), 6)
        entry["p_value"] = round(float(t_res.pvalue), 6)
        if perms > 0:
            rng = np.random.default_rng(_PERMUTATION_SEED)
            pooled = np.concatenate([va, vb])
            na = va.size
            obs_diff = float(va.mean() - vb.mean())
            extreme = 0
            for _ in cancellable(range(perms)):
                pv = rng.permutation(pooled)
                if abs(pv[:na].mean() - pv[na:].mean()) >= abs(obs_diff):
                    extreme += 1
            entry["p_value_permutation"] = float(
                (extreme + 1) / (perms + 1))
        sig = entry["p_value"] < 0.05
        entry["direction"] = (
            "higher" if sig and entry["mean_diff"] > 0
            else "lower" if sig and entry["mean_diff"] < 0
            else "not_significant")
        pairs.append(entry)
    matrix = {a: {} for a in levels}
    for entry in pairs:
        # 方向矩阵按有序对存：matrix[a][b] 是「a 相对 b」的方向（a 显著
        # 更高 → higher）；[b][a] 翻转，读者不必再查 mean_diff。
        a, b = entry["stratum_1"], entry["stratum_2"]
        direction_ab = entry["direction"]
        direction_ba = {
            "higher": "lower", "lower": "higher",
            "not_significant": "not_significant",
        }[direction_ab]
        matrix[a][b] = direction_ab
        matrix[b][a] = direction_ba
    return {"n": int(n), "levels": levels, "pairs": pairs,
            "matrix": matrix, "permutations": perms}


def geodetector_risk_narrated(
    geojson: dict,
    value_field: str,
    strata_field: str,
    bins: int = 0,
    permutations: int = 0,
) -> GeoAnalysisResult:
    """风险探测器（stats.geodetector_risk）：逐分层对的均值差检验。

    输出对列表（Welch t + 可选固定种子 42 置换 p）与方向矩阵两种形式；
    p<0.05 才判 higher/lower，否则 not_significant（不夸大方向）。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection with a value field and "
                            "a strata field",
        )
    gdf, _ = res
    if strata_field not in gdf.columns:
        raise MissingRequiredField(
            f"strata field '{strata_field}' is missing",
            correction_hint=f"provide the property '{strata_field}'",
        )
    aligned = _filter_numeric_gdf(gdf, value_field)
    if aligned is None or len(aligned[1]) == 0:
        raise MissingRequiredField(
            f"field '{value_field}' is missing or non-numeric",
            correction_hint=f"provide a numeric property '{value_field}'",
        )
    gdf, values = aligned
    keep = gdf[strata_field].notna()
    gdf = gdf[keep].reset_index(drop=True)
    values = values[np.asarray(keep, dtype=bool)]
    labels = _geodetector_labels(gdf[strata_field], bins, strata_field)
    out = geodetector_risk(values, labels, permutations)
    sig_pairs = [p for p in out["pairs"] if p["direction"] != "not_significant"]
    data_out = {
        "n_features": out["n"],
        "value_field": str(value_field),
        "strata_field": str(strata_field),
        "n_strata": len(out["levels"]),
        "pairs": out["pairs"],
        "matrix": out["matrix"],
        "permutations": out["permutations"],
        "significant_pair_count": len(sig_pairs),
        "uncertainty": [
            StatisticalSignificance(
                target="geodetector_risk",
                statistic_name=f"Welch t ({p['stratum_1']} vs {p['stratum_2']})",
                statistic_value=p["t_statistic"],
                p_value=p["p_value"],
                method="analytic_normal",
                alternative="two-sided",
            ).to_evidence()
            for p in out["pairs"] if p["t_statistic"] is not None
        ],
    }
    summary = (
        f"风险探测器：'{strata_field}' 的 {len(out['levels'])} 个分层共 "
        f"{len(out['pairs'])} 个分层对，其中 {len(sig_pairs)} 对均值差显著"
        f"（Welch t，p<0.05"
        + (f"；置换复核 {out['permutations']} 次、固定种子 42"
           if out["permutations"] > 0 else "")
        + "）。方向：higher=前一分层均值显著更高。")
    return GeoAnalysisResult(True, data_out, summary)


# ── Foundation V3：局部 Join Count（Anselin & Li 2019）───────────────

def local_join_count_narrated(
    geojson: dict,
    binary_field: str,
    weights_scheme: str = "knn",
    k: int = 8,
    distance_band: float = 0,
    permutations: int = 999,
    correction: str = "bh",
) -> GeoAnalysisResult:
    """局部 Join Count（stats.local_join_count；Anselin & Li 2019）。

    二值场 y ⊆ {0,1}（违者 UnsupportedMethod）上 LJC_i = Σ_j w_ij·I(y_i=1)
    ·I(y_j=1)（二值对称权重）。y_i=0 的位置 LJC ≡ 0、p≡1（不在 1-簇族
    内）；y_i=1 的位置用条件置换推断：焦点固定为 1，其余位置从含 n₁−1
    个 1 的剩余池重排（条件随机化；每 draw 共享重排），单侧上尾
    (k+1)/(D+1)，D_i=有效条件 draw 数（随 i 不同，条件置换
    的固有性质，meta 披露）。多重校正 correction ∈ {bh(默认), bonferroni,
    holm, none}：校正族 = 焦点族（y=1 的 n₁ 个检验）——y=0 位置 LJC≡0
    结构性不显著，不进入检验族。
    """
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection with a binary (0/1) field",
        )
    gdf, _ = res
    aligned = _filter_numeric_gdf(gdf, binary_field)
    if aligned is None or len(aligned[1]) == 0:
        raise MissingRequiredField(
            f"field '{binary_field}' is missing or non-numeric",
            correction_hint=f"provide a binary (0/1) property '{binary_field}' "
                            "on every feature",
        )
    gdf, values = aligned
    n = len(values)
    if n < 4:
        raise InsufficientSamples(
            f"local join count needs at least 4 valid features (got {n})",
            correction_hint="add observations",
        )
    uniq = np.unique(values)
    if not np.all(np.isin(uniq, (0.0, 1.0))):
        raise UnsupportedMethod(
            f"field '{binary_field}' is not binary: unique values {uniq[:8].tolist()}",
            correction_hint="derive a binary field (e.g. above/below threshold), "
                            "or use local_geary / h3_lisa for continuous values",
        )
    if len(uniq) < 2:
        raise DegenerateData(
            f"all '{binary_field}' values are identical ({uniq[0]:.0f}); "
            "local join count is undefined",
            correction_hint="the field must contain both 0 and 1",
        )
    if str(correction).lower() not in _CORRECTION_METHODS:
        raise ValueError(
            f"correction must be one of {_CORRECTION_METHODS} (got {correction!r})")
    correction = str(correction).lower()
    perms = _validate_permutations(permutations)

    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    scheme = str(weights_scheme or "knn").lower()
    if scheme == "knn":
        wm = build_knn_weights(coords, k=min(int(k), n - 1),
                               row_standardized=False)
    elif scheme in ("queen", "rook"):
        wm = build_contiguity_weights(gdf, scheme=scheme, row_standardized=False)
    elif scheme == "distance_band":
        threshold = float(distance_band) if distance_band and float(distance_band) > 0 \
            else auto_band_8nn(coords)
        wm = build_distance_band_weights(
            coords, threshold=threshold, include_self=False,
            row_standardized=False)
    else:
        raise ValueError(
            f"unknown weights_scheme {weights_scheme!r}; "
            f"expected one of {WEIGHT_SCHEMES}")
    if wm.s0 == 0:
        raise DegenerateData(
            "spatial weights matrix is empty (every observation is an island)",
            correction_hint="increase the distance band / k, or check geometry connectivity",
        )

    b = values.astype(float)
    # LJC_i = I(y_i=1)·Σ_j w_ij·I(y_j=1)（y_i=0 → 恒 0）
    ljc = b * np.asarray(wm.matrix @ b).ravel()

    # 条件置换（Anselin 1995 条件随机化；V3 review B1 修正）：焦点 i 的
    # 条件零假设 = 保持 y_i=1，其余 n−1 个位置带 n₁−1 个 1 均匀重排。
    # 共享置换流逐 draw 生成全标签随机排列 pb（计数守恒）；对焦点 i 只
    # 取 pb_i=0 的 draw——由可交换性，该条件下的邻居和 Σ_j w_ij·pb_j
    # （权重无 w_ii 自环）恰为条件零假设分布的精确采样。p_i=(k+1)/(D+1)，
    # D_i=pb_i=0 的有效 draw 数（随 i 不同，meta 披露）；D_i=0 → p=1
    # （保守）。此前对全标签置换不区分 pb_i，p 被压低约 n₁/n 倍——已修复
    # 并由独立复算锁定。固定种子 42 + 固定分块 → 确定性。
    rng = np.random.default_rng(_PERMUTATION_SEED)
    ones_mask = b == 1.0
    focal = np.where(ones_mask)[0]
    obs_sum = np.asarray(wm.matrix @ b).ravel()
    extreme = np.zeros(n, dtype=np.int64)
    valid = np.zeros(n, dtype=np.int64)
    chunk = max(1, min(int(perms), int(2_000_000 // max(n, 1))))
    w_t = wm.matrix.T
    drawn = 0
    while drawn < int(perms):
        m = min(chunk, int(perms) - drawn)
        P = np.empty((m, n))
        for r in range(m):
            P[r] = rng.permutation(b)
        S = P @ w_t                      # S[r, i] = Σ_j w_ij·P[r, j]
        Pv0 = P[:, focal] == 0.0         # 合法条件 draw：焦点处抽到 0
        extreme[focal] += np.sum(Pv0 & (S[:, focal] >= obs_sum[focal]), axis=0)
        valid[focal] += Pv0.sum(axis=0)
        drawn += m
    denom = np.where(valid > 0, valid, 1)
    p_vals = np.where(ones_mask, (extreme + 1) / (denom + 1), 1.0)
    # 多重校正族 = 焦点族（y=1 的 n₁ 个检验）：y=0 位置 LJC≡0 结构性
    # 不显著（p≡1），计入族只会稀释 BH 门槛（V3 review 修正，meta 披露）。
    p_adj_focal = multiple_testing_correction(p_vals[ones_mask], correction)
    p_adj = np.ones(n)
    p_adj[ones_mask] = p_adj_focal

    significant = (p_adj < 0.05) & ones_mask
    clusters = np.where(significant, "co_location_cluster", "neutral").tolist()
    counts = {"co_location_cluster": int(np.sum(significant)),
              "neutral": int(n - int(np.sum(significant)))}
    sig_count = int(np.sum(significant))
    expected_fp = round(0.05 * n, 1)

    gdf_wgs84 = gdf.to_crs("EPSG:4326")
    features = _assemble_features(
        gdf_wgs84,
        {
            "local_join_count": [round(float(v), 6) for v in ljc],
            "p_value": [round(float(v), 6) for v in p_vals],
            f"p_{correction}" if correction != "none" else "p_value_adjusted": [
                round(float(v), 6) for v in p_adj],
            "local_join_count_cluster": clusters,
        },
    )

    data_out = {
        "type": "FeatureCollection",
        "features": features,
        "local_join_count_counts": counts,
        "significant_count": sig_count,
        "expected_false_positives": expected_fp,
        "correction": correction,
        "n_features": n,
        "n_ones": int(np.sum(b)),
        "permutations": perms,
        "weights": wm.metadata(),
        "uncertainty": StatisticalSignificance(
            target="local_join_count",
            statistic_name="share of significant local join count locations (α=0.05)",
            statistic_value=sig_count / n,
            p_value=None,
            method="permutation",
            permutations=perms,
            multiple_testing=_CORRECTION_LABELS[correction],
        ).to_evidence(),
    }
    summary = (
        f"局部 Join Count（Anselin & Li 2019）：{int(np.sum(b))} 个 y=1 位置中 "
        f"校正后 {counts['co_location_cluster']} 个显著共位簇（{correction.upper()}；"
        f"未校正 α=0.05 随机期望假阳性 ≈{expected_fp} 个）。"
        "y=0 位置 LJC≡0、p≡1（不在 1-簇族内）。")
    return GeoAnalysisResult(True, data_out, summary)


# ── Foundation V3：双变量局部 Moran（esda.Moran_Local_BV 委托）───────

_BV_LM_QUAD_LABELS = {1: "HH", 2: "LH", 3: "LL", 4: "HL"}


def bivariate_local_moran_narrated(
    geojson: dict,
    value_field: str,
    lag_field: str,
    weights_scheme: str = "knn",
    k: int = 8,
    distance_band: float = 0,
    permutations: int = 999,
) -> GeoAnalysisResult:
    """双变量局部 Moran（stats.bivariate_local_moran；esda 委托，seed=42）。

    I_i^BV = z(x1)_i · Σ_j w_ij z(x2)_j / S₀（esda.Moran_Local_BV，行标准化
    权重，固定种子 42 条件随机化）。标签 HH/LH/LL/HL 取 p_sim<0.05，
    BH q 值随要素输出。**孤岛权重警示**（与既有双变量 Moran 同款披露）：
    无邻居位置在行标准化权重下贡献为 0、结果中性；与 esda 归一化的对齐
    仅在无 island 权重时严格成立。共位相关 ≠ 因果/超前-滞后。
    """
    import esda as _esda
    import libpysal as _libpysal

    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection with two numeric fields",
        )
    gdf, _ = res
    aligned = _filter_two_numeric_gdf(gdf, value_field, lag_field)
    if aligned is None:
        raise MissingRequiredField(
            f"fields '{value_field}' / '{lag_field}' missing or non-numeric",
            correction_hint="provide both numeric properties on every feature",
        )
    gdf, vx, vy = aligned
    n = len(vx)
    if n < 8:
        raise InsufficientSamples(
            f"bivariate local Moran needs at least 8 valid features (got {n})",
            correction_hint="add observations",
        )
    if float(np.ptp(vx)) == 0.0 or float(np.ptp(vy)) == 0.0:
        raise DegenerateData(
            "one of the fields has zero variance; "
            "bivariate local Moran is undefined",
            correction_hint="check both fields for constant values",
        )
    perms = _validate_permutations(permutations)

    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    scheme = str(weights_scheme or "knn").lower()
    if scheme == "knn":
        wm = build_knn_weights(coords, k=min(int(k), n - 1),
                               row_standardized=False)
    elif scheme in ("queen", "rook"):
        wm = build_contiguity_weights(gdf, scheme=scheme, row_standardized=False)
    elif scheme == "distance_band":
        threshold = float(distance_band) if distance_band and float(distance_band) > 0 \
            else auto_band_8nn(coords)
        wm = build_distance_band_weights(
            coords, threshold=threshold, include_self=False,
            row_standardized=False)
    else:
        raise ValueError(
            f"unknown weights_scheme {weights_scheme!r}; "
            f"expected one of {WEIGHT_SCHEMES}")
    if wm.s0 == 0:
        raise DegenerateData(
            "spatial weights matrix is empty (every observation is an island)",
            correction_hint="increase the distance band / k, or check geometry connectivity",
        )
    # esda 需要 libpysal W（二值矩阵 + transformation='r' 由 esda 行标准化）
    w_lib = _libpysal.weights.WSP(wm.matrix.tocsr()).to_W()
    ref = _esda.Moran_Local_BV(
        vx, vy, w_lib, transformation="r", permutations=perms, seed=42)

    p_sim = np.asarray(ref.p_sim, dtype=float)
    p_adj = multiple_testing_correction(p_sim, "bh")
    significant = p_sim < 0.05
    quads = np.asarray(ref.q, dtype=int)
    labels = np.where(
        significant,
        np.array([_BV_LM_QUAD_LABELS.get(q, "neutral") for q in quads]),
        "not_significant",
    ).tolist()
    counts = {lbl: int(sum(1 for v in labels if v == lbl))
              for lbl in ("HH", "LH", "LL", "HL", "not_significant")}
    sig_count = int(np.sum(significant))
    expected_fp = round(0.05 * n, 1)
    island_note = (
        f"权重含 {len(wm.islands)} 个孤岛位置（无邻居）：孤岛在行标准化下"
        "贡献为 0、结果中性" if wm.islands else "权重无孤岛")

    gdf_wgs84 = gdf.to_crs("EPSG:4326")
    features = _assemble_features(
        gdf_wgs84,
        {
            "local_moran_bv": [round(float(v), 6) for v in np.asarray(ref.Is)],
            "p_value": [round(float(v), 6) for v in p_sim],
            "p_bh": [round(float(v), 6) for v in p_adj],
            "bivariate_local_moran_label": labels,
        },
    )

    data_out = {
        "type": "FeatureCollection",
        "features": features,
        "label_counts": counts,
        "significant_count": sig_count,
        "expected_false_positives": expected_fp,
        "n_features": n,
        "value_field": str(value_field),
        "lag_field": str(lag_field),
        "permutations": perms,
        "island_caveat": island_note,
        "weights": wm.metadata(),
        "uncertainty": StatisticalSignificance(
            target="bivariate_local_moran",
            statistic_name="share of significant bivariate local Moran locations",
            statistic_value=sig_count / n,
            p_value=None,
            method="permutation",
            permutations=perms,
            multiple_testing="BH-FDR",
        ).to_evidence(),
    }
    summary = (
        f"双变量局部 Moran（'{value_field}' vs '{lag_field}' 的空间滞后，"
        f"esda 委托、固定种子 42）：{sig_count}/{n} 个位置 p_sim<0.05 —— "
        f"HH={counts['HH']}、LH={counts['LH']}、LL={counts['LL']}、"
        f"HL={counts['HL']}、not_significant={counts['not_significant']}"
        f"（BH-FDR 后随机期望假阳性 ≈{expected_fp} 个；{island_note}）。"
        "注意：共位相关不能解释为因果/超前-滞后关系。")
    return GeoAnalysisResult(True, data_out, summary)


# ── Foundation V3：空间权重诊断 ─────────────────────────────────────

def weights_diagnostics_narrated(
    geojson: dict,
    weights_scheme: str = "knn",
    k: int = 8,
    distance_band: float = 0,
) -> GeoAnalysisResult:
    """空间权重诊断（stats.weights_diagnostics）：权重结构体检表。

    输出 n / 稀疏度 / 对称性（存储矩阵与二值邻接）/ 行标准化标记 /
    邻居数统计（mean/min/max）/ 孤岛数与孤岛 id / 连通分量
    （networkx，二值邻接无向图）/ 结构警告。确定性、零随机成分。
    """
    import networkx as nx

    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection with at least 1 feature",
        )
    gdf, _ = res
    n = len(gdf)
    if n < 1:
        raise InsufficientSamples(
            "weights diagnostics needs at least 1 feature",
            correction_hint="pass a non-empty FeatureCollection",
        )
    wm = _autocorr_weights(gdf, n, weights_scheme, k, distance_band)
    m = wm.matrix.tocsr()
    row_counts = np.diff(m.indptr).astype(int)

    # 对称性：存储矩阵与（更有意义的）二值邻接各查一次
    symmetric_stored = (m != m.T).nnz == 0
    binary = m.copy()
    binary.data = np.ones_like(binary.data)
    binary_symmetric = (binary != binary.T).nnz == 0

    # 连通分量：二值邻接无向化（w_ij>0 视为边）
    a_sym = binary.maximum(binary.T)
    graph = nx.from_scipy_sparse_array(a_sym, edge_attribute=None)
    component_sizes = sorted(
        (len(c) for c in nx.connected_components(graph)), reverse=True)
    n_components = len(component_sizes)

    islands = list(wm.islands)
    warnings: list = []
    if islands:
        warnings.append(
            f"权重矩阵含 {len(islands)} 个孤岛（无邻居）：相关/回归类统计量"
            "对孤岛退化为中性或 0 权重（位置："
            f"{islands[:16]}{'…' if len(islands) > 16 else ''}）")
    if not binary_symmetric:
        warnings.append(
            "二值邻接不对称 —— 要求对称权重的统计量（Moran/Geary/Join Count/"
            "SAR-ML）会先做对称化或不可用，请确认这是期望的权重结构")
    if n_components > 1:
        warnings.append(
            f"权重图有 {n_components} 个连通分量（最大分量仅覆盖 "
            f"{component_sizes[0] / n:.1%}）—— 跨分量无空间关联，全局统计量"
            "解释需谨慎")

    data_out = {
        "n_features": int(n),
        "scheme": wm.scheme,
        "row_standardized": bool(wm.row_standardized),
        "sparsity": round(float(m.nnz) / float(n * n), 8) if n else 0.0,
        "symmetric": bool(symmetric_stored),
        "binary_symmetric": bool(binary_symmetric),
        "neighbors": {
            "mean": round(float(row_counts.mean()), 4),
            "min": int(row_counts.min()),
            "max": int(row_counts.max()),
        },
        "island_count": len(islands),
        "island_ids": islands,
        "connected_components": {
            "count": n_components,
            "sizes": component_sizes[:16],
            "largest_share": round(component_sizes[0] / n, 6) if n else 0.0,
        },
        "nonzero_weights": int(m.nnz),
        "warnings": warnings,
        "weights": wm.metadata(),
    }
    summary = (
        f"权重诊断（{wm.scheme}，行标准化={wm.row_standardized}）：n={n}，"
        f"稀疏度={data_out['sparsity']:.2e}，邻居数 mean/min/max="
        f"{data_out['neighbors']['mean']}/{data_out['neighbors']['min']}/"
        f"{data_out['neighbors']['max']}，孤岛 {len(islands)} 个，"
        f"连通分量 {n_components} 个（最大占 "
        f"{data_out['connected_components']['largest_share']:.1%}），"
        f"二值邻接对称={binary_symmetric}。"
        + ("；".join(warnings) if warnings else " 无结构警告。"))
    return GeoAnalysisResult(True, data_out, summary)
