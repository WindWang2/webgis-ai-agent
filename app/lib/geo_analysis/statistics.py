import hashlib
import threading
from collections import OrderedDict
from typing import Any, Callable
import numpy as np
import pandas as pd
import geopandas as gpd
from scipy import sparse
from shapely.geometry import Point, Polygon, mapping
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
    UnsupportedMethod,
)
from app.lib.gis.uncertainty import StatisticalSignificance
# ADR-0052: 协作式取消检查点。cancellable() 在 chunk 边界读一次 contextvar，
# 未绑定 token 时开销为零；用户取消后长循环立即抛 OperationCancelled 退出，
# 真正释放 CPU 而不是只改 UI 状态。
from app.lib.cancellation import cancellable


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

def hotspot_narrated(geojson: dict, value_field: str, distance_band: float = 0) -> GeoAnalysisResult:
    """
    Getis-Ord Gi* local spatial autocorrelation (hotspot analysis) with narrative summary.
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
    p_vals = np.maximum(2 * (1 - norm.cdf(np.abs(gi_stars))), _TINY_P)

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

    features = _assemble_features(
        gdf_wgs84,
        {
            "gi_star": [round(float(v), 4) for v in gi_stars],
            "p_value": [round(float(v), 6) for v in p_vals],
            "q_value_fdr": [round(float(v), 6) for v in q_vals],
            "hotspot_type": hotspot_types,
            "confidence": confidences,
        },
    )
        
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
    
    return GeoAnalysisResult(True, data_out, summary)

def calculate_nearest(geojson: dict) -> GeoAnalysisResult:
    """Nearest neighbor analysis with narrative summary (O(n log n) via cKDTree).

    Returns {mean_nearest_distance, expected, R, pattern} plus aliases
    {mean_distance, r_ratio} and extras {std_distance, min/max_distance}
    for backwards compatibility. mean_nearest_distance == mean_distance,
    expected is the CSR expectation, R == r_ratio.
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
    
    # Simple pattern recognition
    # Expected mean distance for random distribution (Poisson process)
    # R = Observed / Expected
    # Expected = 0.5 * sqrt(Area / N)
    xmin, ymin, xmax, ymax = gdf.total_bounds
    area = (xmax - xmin) * (ymax - ymin)
    expected_mean = 0.5 * np.sqrt(area / len(gdf))
    if expected_mean == 0 or mean_dist == 0:
        r_ratio = 0.0
    else:
        r_ratio = mean_dist / expected_mean

    pattern = "random"
    if r_ratio < 0.7:
        pattern = "clustered"
    elif r_ratio > 1.3:
        pattern = "dispersed"
    
    summary = f"Nearest Neighbor Insight: The mean distance to the nearest neighbor is {mean_dist:.2f} meters. The distribution pattern appears to be {pattern} (R ratio: {r_ratio:.2f})."
    
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
    }
    from app.lib.geo_analysis.evidence import build_quality_evidence

    return GeoAnalysisResult(
        True, data, summary,
        evidence=build_quality_evidence(
            input_count=len(gdf),
            working_crs=str(working_crs),
            extra={"pattern": pattern, "r_ratio": round(float(r_ratio), 6)},
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
        gdf_wgs84, {"lisa_cluster": list(clusters)},
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
