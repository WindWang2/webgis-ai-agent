"""Emerging Hot Spot Analysis（EHA，时空热点演化；science-v3 R1 / 审计 03 §8）。

对「已聚合的空间箱 × 时间期」计数量矩阵（space-time cube，调用方经
H3/格网聚合产出，或输入天然分箱的 period 计数）做三段式热点演化分析：

1. **逐期 Getis-Ord Gi\***：对每期计数在空间箱质心间计算 Gi\* z 值
   （距离段二值权重、含自身 w_ii=1，与 ``hotspot_narrated`` 同式），
   双侧解析 p 经 **BH-FDR 校正**（与 ArcGIS Emerging Hot Spot Analysis
   的 FDR 口径一致）后判定该期显著热点/冷点；
2. **逐箱 Mann-Kendall**：对每个空间箱的 Gi\* z 值时序跑本仓已验证的
   ``mann_kendall``（tie 校正方差 + 连续性校正，app/services/temporal/
   trend.py——单一事实源，不重实现）；n_periods < 4 时趋势不可得，
   分类退化为纯形态学规则并披露；
3. **ESRI EHA 分类**：按 ArcGIS《Emerging Hot Spot Analysis》决策树
   输出 17 类 + none（互斥完备）：new/consecutive/intensifying/
   persistent/diminishing/sporadic/oscillating/historical 的热点与
   冷点镜像 + no_pattern（类别码 ±1..±8、0 与 ArcGIS 栅格码一致）。

确定性：无模拟、无置换（Gi\* 解析正态 + MK 解析正态），同输入逐位
可复现。参考：getis_ord1992、mann1945、kendall1975（均在册）、
esri_eha（ArcGIS Pro 工具参考，分类法定义）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

import numpy as np
from scipy.spatial import cKDTree

from app.lib.gis.crs_safety import classify_crs
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    InvalidCRS,
    ResourceScaleMismatch,
)
from app.lib.geo_analysis.statistics import _bh_qvalues

#: EHA 分类码（与 ArcGIS Emerging Hot Spot Analysis 栅格属性码一致）。
EHA_CATEGORY_CODES: Dict[str, int] = {
    "new_hot_spot": 1,
    "consecutive_hot_spot": 2,
    "intensifying_hot_spot": 3,
    "persistent_hot_spot": 4,
    "diminishing_hot_spot": 5,
    "sporadic_hot_spot": 6,
    "oscillating_hot_spot": 7,
    "historical_hot_spot": 8,
    "no_pattern": 0,
    "new_cold_spot": -1,
    "consecutive_cold_spot": -2,
    "intensifying_cold_spot": -3,
    "persistent_cold_spot": -4,
    "diminishing_cold_spot": -5,
    "sporadic_cold_spot": -6,
    "oscillating_cold_spot": -7,
    "historical_cold_spot": -8,
}

#: ESRI 口径：「绝大多数期显著」的分位阈（≥90% 的期显著才算
#: intensifying/persistent/diminishing/historical 候选）。
_MAJORITY_FRACTION = 0.9

#: 空间箱数上限（稀疏 W 下 O(n) Matvec × 期数；与 Knox/K_st 同量级诚实上限）。
_MAX_EHA_LOCATIONS = 20000

#: 最长期数（防滥用；正常空间时序立方远低于此）。
_MAX_EHA_PERIODS = 2000

_TINY_P = 1e-16


# ── 分类核心（纯规则，单箱级别；互斥完备由构造保证）────────────────────

def _trailing_run(mask: np.ndarray) -> int:
    """mask 末尾连续 True 的长度（至少 0）。"""
    run = 0
    for t in range(mask.size - 1, -1, -1):
        if mask[t]:
            run += 1
        else:
            break
    return run


def _classify_one(hot: np.ndarray, cold: np.ndarray,
                  mk_up: bool, mk_dn: bool) -> str:
    """单箱 EHA 分类（ESRI 决策树；hot/cold 为逐期布尔，末位=最末期）。

    分支互斥（每个 if-return 链只走一条）且完备（no_pattern 兜底）：
    末期热 → 90% 分位族（趋势分 intensifying/persistent/diminishing）
    或 <90% 族（末期前无热 → consecutive/new；有冷史 → oscillating；
    否则 sporadic）；末期冷为镜像（冷点「增强」= z 走低，故 up/dn
    互换）；末期中性 → 90% 先前热/冷史 → historical，否则 no_pattern。
    """
    n_periods = hot.size
    if hot[-1]:
        if hot.mean() >= _MAJORITY_FRACTION:
            if mk_up:
                return "intensifying_hot_spot"
            if mk_dn:
                return "diminishing_hot_spot"
            return "persistent_hot_spot"
        run = _trailing_run(hot)
        prior_hot = bool(hot[:n_periods - run].any())
        if not prior_hot:
            return "consecutive_hot_spot" if run >= 2 else "new_hot_spot"
        if cold.any():
            return "oscillating_hot_spot"
        return "sporadic_hot_spot"
    if cold[-1]:
        if cold.mean() >= _MAJORITY_FRACTION:
            # 冷点增强 = z 值走低 → mk_dn 为「增强」。
            if mk_dn:
                return "intensifying_cold_spot"
            if mk_up:
                return "diminishing_cold_spot"
            return "persistent_cold_spot"
        run = _trailing_run(cold)
        prior_cold = bool(cold[:n_periods - run].any())
        if not prior_cold:
            return "consecutive_cold_spot" if run >= 2 else "new_cold_spot"
        if hot.any():
            return "oscillating_cold_spot"
        return "sporadic_cold_spot"
    if hot[:-1].size and hot[:-1].any() and hot[:-1].mean() >= _MAJORITY_FRACTION:
        return "historical_hot_spot"
    if cold[:-1].size and cold[:-1].any() and cold[:-1].mean() >= _MAJORITY_FRACTION:
        return "historical_cold_spot"
    return "no_pattern"


# ── 主入口 ────────────────────────────────────────────────────────────

def emerging_hotspot_narrated(
    counts: Sequence[Sequence[float]],
    coords: Sequence[Sequence[float]],
    crs: str = "",
    distance_band: float = 0.0,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """时空热点演化分析（Emerging Hot Spot Analysis）。

    参数
    ----
    counts: (n_locations, n_periods) 已聚合计数量矩阵（space-time cube；
        某期缺失的箱按 0 计入——调用方聚合时披露）。逐期做全箱 Gi\*。
    coords: (n_locations, 2) 箱质心**米制**坐标（经纬度输入经 crs 声明
        为地理坐标时拒绝——工具层先自动投影 UTM）。
    crs: 声明 CRS（可选；geographic 类型直接 InvalidCRS）。
    distance_band: 空间权重距离段（米，含自身 w_ii=1）。0 = 自动
        （平均 8-NN 距离，与 ``hotspot_narrated`` 的 E-7 同口径），
        自动值在输出中披露。
    alpha: 显著性水平（BH-FDR 校正后 q < alpha 判显著）。

    返回
    ----
    dict：逐箱逐期 Gi\* z / 解析 p / BH q、逐箱 MK 趋势、EHA 分类
    （互斥完备 17+1 类）与叙事 summary。
    """
    mat = np.asarray(counts, dtype=float)
    if mat.ndim != 2:
        raise ValueError(
            f"counts must be a 2-D (locations x periods) matrix "
            f"(got ndim={mat.ndim})")
    xy = np.asarray(coords, dtype=float)
    n, n_periods = mat.shape
    if xy.ndim != 2 or xy.shape != (n, 2):
        raise ValueError(
            f"coords must be an (n_locations, 2) array matching counts rows "
            f"(got shape {xy.shape}, counts={mat.shape})")
    if n < 3:
        raise InsufficientSamples(
            f"EHA needs at least 3 spatial bins for Gi* (got {n})",
            correction_hint="aggregate points to more bins (h3_binning / grid), or merge periods",
        )
    if n_periods < 2:
        raise ValueError(
            f"EHA needs at least 2 time periods (got {n_periods}); "
            "a single snapshot has no temporal evolution to classify")
    if n > _MAX_EHA_LOCATIONS:
        raise ResourceScaleMismatch(
            f"EHA builds an n x n spatial weights matrix at n={n} bins",
            estimated=f"{n} x {n} sparse W + {n_periods} sparse matvecs",
            limit=f"n <= {_MAX_EHA_LOCATIONS} bins",
            correction_hint="coarsen the binning (larger H3 res / bigger grid cells)",
        )
    if n_periods > _MAX_EHA_PERIODS:
        raise ResourceScaleMismatch(
            f"EHA runs one Gi* pass per period (T={n_periods})",
            estimated=f"{n_periods} x O(nnz) matvecs at n={n}",
            limit=f"T <= {_MAX_EHA_PERIODS}",
            correction_hint="aggregate to coarser periods (weeks instead of days)",
        )
    if not 0.0 < float(alpha) < 1.0:
        raise ValueError(f"alpha must be within (0, 1), got {alpha}")
    if crs and classify_crs(crs) == "geographic":
        raise InvalidCRS(
            f"EHA needs metric bin centroids (got geographic crs={crs})",
            correction_hint="let the tool layer auto-project to local UTM first",
        )
    if not np.isfinite(mat).all():
        n_bad = int((~np.isfinite(mat)).sum())
        raise ValueError(
            f"counts contain {n_bad} NaN/Inf entries — aggregate step must "
            "emit finite counts (use 0 for absent bin-period combinations)")
    if not np.isfinite(xy).all():
        raise ValueError("coords contain NaN/Inf entries")
    if (mat < 0).any():
        raise ValueError(
            "counts must be non-negative (bin-period aggregate counts); "
            "signed measures are not a space-time cube")
    if n > 1:
        uniq = np.unique(xy, axis=0)
        if uniq.shape[0] < n:
            raise DegenerateData(
                f"{n - uniq.shape[0]} duplicate bin centroids — Gi* weights "
                "are undefined for coincident bins",
                correction_hint="deduplicate bins or use a finer binning resolution",
            )

    # ── 空间权重（距离段二值、含自身；稀疏 CSR 端到端）────────────────
    distance_band = float(distance_band)
    if distance_band < 0:
        raise ValueError(
            f"distance_band must be >= 0 (0 = auto), got {distance_band}")
    tree = cKDTree(xy)
    distance_band_auto = distance_band <= 0
    if distance_band_auto:
        k_band = min(8, n - 1)
        nn_dist, _ = tree.query(xy, k=k_band + 1)
        distance_band = float(nn_dist[:, k_band].mean())
        if distance_band <= 0:
            distance_band = 1.0
    coo = tree.sparse_distance_matrix(tree, max_distance=distance_band,
                                      output_type="coo_matrix")
    from scipy.sparse import csr_matrix

    w = csr_matrix(
        (np.ones(len(coo.data)), (coo.row, coo.col)), shape=(n, n))
    sum_w = np.asarray(w.sum(axis=1)).ravel()
    sum_w2 = np.asarray(w.multiply(w).sum(axis=1)).ravel()
    denom_inner = (n * sum_w2 - sum_w ** 2) / (n - 1)
    safe_inner = np.where(denom_inner > 0, np.sqrt(denom_inner), 0.0)

    # ── 逐期 Gi* z + 解析双侧 p + BH-FDR q ───────────────────────────
    from scipy.stats import norm

    z_mat = np.zeros((n, n_periods))
    p_mat = np.ones((n, n_periods))
    q_mat = np.ones((n, n_periods))
    degenerate_periods: List[int] = []
    for t in range(n_periods):
        x = mat[:, t]
        x_bar = float(x.mean())
        s = float(x.std(ddof=0))
        if s == 0:
            degenerate_periods.append(t)
            continue
        numerators = np.asarray(w @ x).ravel() - x_bar * sum_w
        denominators = s * safe_inner
        z = np.where(denominators > 0, numerators / denominators, 0.0)
        p = np.maximum(2.0 * norm.sf(np.abs(z)), _TINY_P)
        z_mat[:, t] = z
        p_mat[:, t] = p
        q_mat[:, t] = _bh_qvalues(p)
    if len(degenerate_periods) == n_periods:
        raise DegenerateData(
            "every period has constant counts across bins — no spatial "
            "contrast for Gi* at any period",
            correction_hint="check the aggregation: counts must vary across bins",
        )

    # ── 逐箱 Mann-Kendall（复用 services/temporal 已验证实现）─────────
    from app.services.temporal.trend import mann_kendall

    mk_trend: List[Dict[str, Any]] = []
    mk_up = np.zeros(n, dtype=bool)
    mk_dn = np.zeros(n, dtype=bool)
    n_trend_available = 0
    for i in range(n):
        try:
            mk = mann_kendall(z_mat[i, :], alpha)
        except InsufficientSamples:
            mk_trend.append({
                "available": False,
                "reason": f"MK needs >= 4 periods (n_periods={n_periods})",
                "z": None, "p_value": None, "direction": None,
                "significant": None,
            })
            continue
        n_trend_available += 1
        sig_up = bool(mk["significant"] and mk["z"] > 0)
        sig_dn = bool(mk["significant"] and mk["z"] < 0)
        mk_up[i] = sig_up
        mk_dn[i] = sig_dn
        mk_trend.append({
            "available": True,
            "reason": None,
            "z": round(float(mk["z"]), 6),
            "p_value": round(float(mk["p_value"]), 6),
            "direction": mk["direction"],
            "significant": bool(mk["significant"]),
        })

    # ── 分类（互斥完备 17+1）────────────────────────────────────────
    hot = (q_mat < alpha) & (z_mat > 0)
    cold = (q_mat < alpha) & (z_mat < 0)
    categories = [_classify_one(hot[i], cold[i], bool(mk_up[i]), bool(mk_dn[i]))
                  for i in range(n)]
    codes = [EHA_CATEGORY_CODES[c] for c in categories]
    category_counts: Dict[str, int] = {}
    for c in categories:
        category_counts[c] = category_counts.get(c, 0) + 1

    warnings: List[str] = []
    if degenerate_periods:
        warnings.append(
            f"期 {degenerate_periods} 各箱计数全同（零方差）——该期 Gi* 无 "
            "空间对比，z 置 0 不参与显著性（诚实披露）")
    if n_trend_available == 0:
        warnings.append(
            f"n_periods={n_periods} < 4：Mann-Kendall 趋势不可得，分类仅按"
            "显著热点/冷点形态学规则（intensifying/diminishing 不可能出现）")
    elif n_trend_available < n:
        warnings.append(
            f"{n - n_trend_available} 个箱趋势不可得（同期数不足），按形态学规则分类")
    if n < 8:
        warnings.append(
            f"空间箱数 n={n} < 8：逐期 Gi* 正态近似偏保守，分类结果仅描述性解读")
    mk_small = sum(1 for m in mk_trend if m["available"] and n_periods < 8)
    if mk_small:
        warnings.append(
            f"{mk_small} 个箱的 MK 基于 n_periods={n_periods} < 8 的短序列"
            "（仅描述性解读）")

    top = sorted(category_counts.items(), key=lambda kv: -kv[1])
    top_text = "、".join(f"{k}×{v}" for k, v in top[:5])
    summary = (
        f"Emerging Hot Spot Analysis：{n} 个空间箱 × {n_periods} 期，"
        f"逐期 Gi*（distance_band={distance_band:.1f} m，BH-FDR q<{alpha}）"
        f"+ 逐箱 MK 趋势（{n_trend_available}/{n} 可得）。分类：{top_text}。"
    )

    return {
        "success": True,
        "n_locations": int(n),
        "n_periods": int(n_periods),
        "alpha": float(alpha),
        "distance_band": round(distance_band, 4),
        "distance_band_auto": bool(distance_band_auto),
        "gi_star_z": [[round(float(v), 4) for v in row] for row in z_mat],
        "gi_star_p": [[round(float(v), 6) for v in row] for row in p_mat],
        "gi_star_q_fdr": [[round(float(v), 6) for v in row] for row in q_mat],
        "categories": categories,
        "category_codes": codes,
        "category_counts": category_counts,
        "category_legend": dict(EHA_CATEGORY_CODES),
        "mk_trend": mk_trend,
        "n_trend_available": int(n_trend_available),
        "degenerate_periods": degenerate_periods,
        "warnings": warnings,
        "method": (
            "per-period Getis-Ord Gi* (binary distance-band W, w_ii=1, "
            "BH-FDR) + per-location Mann-Kendall on the z-series; "
            "ESRI Emerging Hot Spot Analysis taxonomy (17 + none)"),
        "seed_policy": "deterministic",
        "summary": summary,
    }
