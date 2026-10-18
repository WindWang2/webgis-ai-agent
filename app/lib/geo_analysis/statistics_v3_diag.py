"""空间统计 Foundation V3 诊断面（自 statistics.py 抽出，god-modules 瘦身）。

- ``local_join_count_narrated``：局部 Join Count（Anselin & Li 2019）；
- ``weights_diagnostics_narrated``：空间权重结构体检表。

对 statistics.py 私有 IO/权重助手的依赖经函数体内惰性 import（零
import 期环；与仓库懒加载惯例一致）。statistics.py 侧 re-export 保持
``from app.lib.geo_analysis.statistics import ...`` 的既有 import 面。
"""
from __future__ import annotations

import numpy as np
from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_analysis._scaffold import (
    Failure,
    require_min_n,
    validated_input,
    validated_numeric_frame,
)
from app.lib.geo_analysis.spatial_weights import (
    WEIGHT_SCHEMES,
    build_contiguity_weights,
    build_distance_band_weights,
    build_knn_weights,
)
from app.lib.geo_analysis.spatial_regression import multiple_testing_correction
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    MissingRequiredField,
    NoValidObservations,
    UnsupportedMethod,
)
from app.lib.gis.uncertainty import StatisticalSignificance
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
    # statistics.py 私有助手：函数体内惰性引入（statistics 底部 re-export
    # 本模块，顶层互引会成环）。
    from app.lib.geo_analysis.statistics import (
        _CORRECTION_LABELS,
        _CORRECTION_METHODS,
        _PERMUTATION_SEED,
        _assemble_features,
        _validate_permutations,
            auto_band_8nn,
)
    gdf, values = validated_numeric_frame(
        geojson,
        binary_field,
        min_n=4,
        invalid=Failure(
            "invalid GeoJSON or no features found",
            exc=NoValidObservations,
            hint="pass a FeatureCollection with a binary (0/1) field",
        ),
        missing=Failure(
            f"field '{binary_field}' is missing or non-numeric",
            exc=MissingRequiredField,
            hint=f"provide a binary (0/1) property '{binary_field}' "
                 "on every feature",
        ),
        too_few=lambda n: Failure(
            f"local join count needs at least 4 valid features (got {n})",
            exc=InsufficientSamples,
            hint="add observations",
        ),
    )
    n = len(values)
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
    # statistics.py 私有助手：函数体内惰性引入（statistics 底部 re-export
    # 本模块，顶层互引会成环）。
    from app.lib.geo_analysis.statistics import (
        _autocorr_weights,
    )
    import networkx as nx

    vsi = validated_input(
        geojson,
        failure=Failure(
            "invalid GeoJSON or no features found",
            exc=NoValidObservations,
            hint="pass a FeatureCollection with at least 1 feature",
        ),
    )
    gdf = vsi.gdf
    n = require_min_n(
        len(gdf), 1,
        failure=Failure(
            "weights diagnostics needs at least 1 feature",
            exc=InsufficientSamples,
            hint="pass a non-empty FeatureCollection",
        ),
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
