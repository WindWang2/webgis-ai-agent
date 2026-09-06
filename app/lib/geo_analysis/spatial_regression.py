"""空间回归族（Spatial Algorithms Foundation V2 · A1 / ADR-0099 延伸）。

本模块是 statistics 域的回归侧实现层：OLS + 空间诊断（JB 正态性 / BP 异
方差 / VIF 多重共线 / 残差 Moran's I / LM-lag、LM-error 及其稳健版）、
SLX（空间滞后 X）、SAR-ML / SEM-ML（Ord 1975 特征值 log-det + 有界标量
优化）、GWR（自适应 bisquare 核 + kNN 带宽）以及多重检验校正
（bonferroni / holm / bh）。

设计约束（与 geo_analysis/statistics.py 同一套家规）：

- 科学性失败抛类型化错误（scientific_errors），绝不静默回退；规模超限
  （SAR 特征值 O(n³)、GWR 全系数面）在**分配内存之前**先抛
  ``ResourceScaleMismatch``；
- 置换推断一律固定种子 42、双侧 (count+1)/(perms+1) —— 与既有
  ``_permutation_stats`` / ``_two_sided_permutation_pvalue`` 同约定；
  本模块独立实现同语义版本，避免与 statistics.py 循环导入；
- 确定性：ML 优化用 ``scipy.optimize.minimize_scalar(method="bounded")``
  （Brent，无随机成分）；带宽 CV 在有界候选网格上穷举；
- LM 系列公式与 spreg ``LMtests`` 逐式对齐（Burridge 1980 / Anselin 1988
  / Anselin-Bera-Florax-Yoon 1996），见 ``_lm_spatial_diagnostics``。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy import sparse
from scipy import stats as sps
from scipy.optimize import minimize_scalar

from app.lib.cancellation import cancellable
from app.lib.geo_processor.core import GeoAnalysisResult, to_utm_gdf
from app.lib.geo_analysis.spatial_weights import (
    WEIGHT_SCHEMES,
    WeightsMatrix,
    auto_band_8nn,
    build_contiguity_weights,
    build_distance_band_weights,
    build_knn_weights,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    IllConditionedSystem,
    InsufficientSamples,
    MissingRequiredField,
    NoValidObservations,
    ResourceScaleMismatch,
    UnsupportedMethod,
)
from app.lib.gis.uncertainty import (
    FieldUncertainty,
    SensitivityEnvelope,
    StatisticalSignificance,
    UncertaintyMeasure,
    ValidationMetrics,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import geopandas as gpd

#: 置换推断的档位与种子（与 statistics._PERMUTATION_* 同词表；独立声明以
#: 避免 statistics.py ↔ spatial_regression.py 循环导入）。
_PERMUTATION_CHOICES = (99, 199, 499, 999)
_PERMUTATION_SEED = 42

#: SAR/SEM 的特征值路径上限：对称相似变换后的稠密 ``eigvalsh`` 是 O(n³) /
#: O(n²) 内存 —— 超限先抛 ResourceScaleMismatch，不做 OOM 赌博。
SAR_EIGEN_MAX_N = 4000

#: GWR 全系数面（逐观测系数列表）的输出上限；超过则只回摘要 + 披露旗标。
GWR_FULL_SURFACE_MAX_N = 2000

#: GWR 带宽 CV 的有界候选网格（确定性穷举；运行时裁剪到 [5, n//2]）。
_GWR_BW_CANDIDATES = (5, 10, 15, 20, 30, 45, 60, 90, 120)


# ── 多重检验校正（独立实现；bh 与 statistics._bh_qvalues 同语义）───────

def multiple_testing_correction(
    p_values: "np.ndarray | Sequence[float]", method: str = "bh",
) -> np.ndarray:
    """逐点 p 值 → 多重校正后的调整 p（向量内逐元素）。

    - ``bonferroni``: p·n（上限 1）；最保守；
    - ``holm``: 逐步下降 Bonferroni（Holm 1979）；调整 p ≤ bonferroni；
    - ``bh``: 逐步上升 FDR（Benjamini-Hochberg 1995）；与
      ``statistics._bh_qvalues`` 同语义（含 NaN → 1.0 的免疫处理）；
    - ``none``: 原样返回（副本）。
    """
    method = str(method or "none").lower()
    if method not in ("none", "bh", "bonferroni", "holm"):
        raise UnsupportedMethod(
            f"unknown multiple-testing correction {method!r}",
            correction_hint="use one of none/bh/bonferroni/holm",
        )
    p = np.asarray(p_values, dtype=float)
    n = p.size
    if n == 0:
        return p
    nan_mask = np.isnan(p)
    p_clean = np.where(nan_mask, 1.0, p)
    if method == "none":
        return p.copy()
    if method == "bonferroni":
        out = np.clip(p_clean * n, 0.0, 1.0)
    elif method == "holm":
        order = np.argsort(p_clean, kind="stable")
        ranked = p_clean[order] * (n - np.arange(n))
        # 逐步下降的累积最大值保证调整 p 单调非降（Holm 1979 的族错误率）。
        ranked = np.maximum.accumulate(ranked)
        out = np.empty(n, dtype=float)
        out[order] = np.clip(ranked, 0.0, 1.0)
    else:  # bh
        order = np.argsort(p_clean, kind="stable")
        ranked = p_clean[order] * n / (np.arange(n) + 1)
        ranked = np.minimum.accumulate(ranked[::-1])[::-1]
        out = np.empty(n, dtype=float)
        out[order] = np.clip(ranked, 0.0, 1.0)
    return np.where(nan_mask, 1.0, out)


# ── 输入收敛（多字段数值过滤 + 权重分发）────────────────────────────

def _validate_permutation_count(permutations: int) -> int:
    """与 statistics._validate_permutations 同词表的防御性校验。"""
    try:
        p = int(permutations)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"permutations must be one of {_PERMUTATION_CHOICES} "
            f"(got {permutations!r})") from exc
    if p not in _PERMUTATION_CHOICES:
        raise ValueError(
            f"permutations must be one of {_PERMUTATION_CHOICES} (got {p})")
    return p


def _filter_fields_gdf(
    gdf: "gpd.GeoDataFrame", fields: Sequence[str],
) -> Optional[Tuple["gpd.GeoDataFrame", np.ndarray]]:
    """按多个数值字段同时过滤行（任一字段 NaN/inf/不可数值即丢行）。

    返回 (gdf_filtered, columns_matrix)，两者行对齐；字段缺失返回 None。
    """
    g = gdf
    for f in fields:
        if f not in g.columns:
            return None
    keep: Optional[pd.Series] = None
    for f in fields:
        s = g[f]
        if not np.issubdtype(s.dtype, np.number):
            s = pd.to_numeric(s, errors="coerce")
        mask = s.notna() & np.isfinite(s.astype(float))
        keep = mask if keep is None else (keep & mask)
        g = g.assign(**{f: s})
    if keep is None:
        return None
    g_valid = g[keep].reset_index(drop=True)
    cols = np.column_stack(
        [g_valid[f].astype(float).values for f in fields])
    return g_valid, cols


def _regression_weights(
    gdf: "gpd.GeoDataFrame",
    n: int,
    weights_scheme: str,
    k: int,
    distance_band: float,
) -> WeightsMatrix:
    """回归族权重：knn/queen/rook/distance_band，二值对称 → 行标准化。

    返回行标准化后的 :class:`WeightsMatrix`（孤岛行保持 0）。queen/rook
    需要面要素，点/线输入抛 UnsupportedMethod（不静默回退）。
    """
    scheme = str(weights_scheme or "knn").lower()
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    if scheme == "knn":
        return build_knn_weights(coords, k=min(int(k), n - 1))
    if scheme in ("queen", "rook"):
        return build_contiguity_weights(gdf, scheme=scheme, row_standardized=True)
    if scheme == "distance_band":
        if distance_band and float(distance_band) > 0:
            threshold = float(distance_band)
        else:
            threshold = auto_band_8nn(coords)
        return build_distance_band_weights(
            coords, threshold=threshold, include_self=False,
            row_standardized=True,
        )
    raise ValueError(
        f"unknown weights_scheme {weights_scheme!r}; "
        f"expected one of {WEIGHT_SCHEMES}")


def _regression_inputs(
    geojson: dict,
    target_field: str,
    explanatory_fields: Sequence[str],
) -> Tuple["gpd.GeoDataFrame", np.ndarray, np.ndarray, List[str]]:
    """GeoJSON → (UTM gdf, y, X, 字段名) 的公共收敛路径（类型化错误）。"""
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        raise NoValidObservations(
            "invalid GeoJSON or no features found",
            correction_hint="pass a FeatureCollection with numeric target and "
                            "explanatory fields",
        )
    gdf, _ = res
    fields = [target_field, *explanatory_fields]
    aligned = _filter_fields_gdf(gdf, fields)
    if aligned is None:
        missing = [f for f in fields if f not in gdf.columns]
        raise MissingRequiredField(
            f"fields missing or non-numeric: {missing or fields}",
            correction_hint="provide numeric target/explanatory fields on "
                            "every feature",
        )
    gdf, cols = aligned
    if len(cols) == 0:
        raise NoValidObservations(
            "no features with complete numeric fields",
            correction_hint="check for nulls in the target/explanatory fields",
        )
    y = cols[:, 0]
    x_raw = cols[:, 1:]
    names = [str(f) for f in explanatory_fields]
    return gdf, y, x_raw, names


def _design_matrix(
    x_raw: np.ndarray, names: List[str],
) -> Tuple[np.ndarray, List[str]]:
    """加截距项 + 零方差防御 → (X, names)。"""
    x_raw = np.asarray(x_raw, dtype=float)
    if x_raw.size == 0 or x_raw.shape[1] == 0:
        raise InsufficientSamples(
            "at least one explanatory field is required",
            correction_hint="pass explanatory_fields (comma-separated)",
        )
    ptp = np.ptp(x_raw, axis=0)
    if np.any(ptp == 0.0):
        bad = [names[i] for i in np.flatnonzero(ptp == 0.0)]
        raise DegenerateData(
            f"explanatory field(s) with zero variance: {bad}",
            correction_hint="drop constant columns or check the input fields",
        )
    x_mat = np.column_stack([np.ones(len(x_raw)), x_raw])
    return x_mat, ["intercept", *names]


def _check_min_samples(n: int, n_params: int) -> None:
    if n < 2 * n_params + 2:
        raise InsufficientSamples(
            f"n={n} is below the regression minimum 2p+2={2 * n_params + 2} "
            f"(p={n_params} fitted parameters)",
            correction_hint="add observations or reduce explanatory fields",
        )


# ── OLS 核心 + 诊断 ──────────────────────────────────────────────────

def _ols_core(y: np.ndarray, x_mat: np.ndarray) -> Dict:
    """普通最小二乘（np.linalg.lstsq）+ 标准误 / t / p / R² / AIC。"""
    n, p = x_mat.shape
    beta, *_ = np.linalg.lstsq(x_mat, y, rcond=None)
    resid = y - x_mat @ beta
    sse = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    dof = max(n - p, 1)
    sigma2 = sse / dof
    r2 = 1.0 - sse / ss_tot if ss_tot > 0 else 0.0
    adj_r2 = 1.0 - (1.0 - r2) * (n - 1) / dof
    xtx_inv = np.linalg.pinv(x_mat.T @ x_mat)
    se = np.sqrt(np.maximum(np.diag(xtx_inv) * sigma2, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        t_stats = np.where(se > 0, beta / se, np.nan)
    p_vals = 2.0 * sps.t.sf(np.abs(t_stats), df=dof)
    # 高斯似然（σ² 用 n 除，与 ML/空间模型可比）：
    sigma2_ml = sse / n
    log_lik = -0.5 * n * (np.log(2.0 * np.pi) + np.log(sigma2_ml))
    aic = -2.0 * log_lik + 2.0 * (p + 1)  # +1 计 σ²
    f_stat = float("nan")
    f_p = float("nan")
    if p > 1 and (1.0 - r2) > 0:
        f_stat = float((r2 / (p - 1)) / ((1.0 - r2) / dof))
        f_p = float(sps.f.sf(f_stat, p - 1, dof))
    return {
        "beta": beta, "se": se, "t": t_stats, "p": p_vals,
        "residuals": resid, "sse": sse,
        "r2": float(r2), "adj_r2": float(adj_r2),
        "log_lik": float(log_lik), "aic": float(aic),
        "f_stat": f_stat, "f_p": f_p, "n_obs": n, "n_params": p,
    }


def _jarque_bera(resid: np.ndarray) -> Dict:
    """残差正态性 JB 检验（scipy.stats.jarque_bera，Jarque-Bera 1980）。"""
    jb = sps.jarque_bera(resid)
    return {"statistic": float(jb.statistic), "p_value": float(jb.pvalue)}


def _breusch_pagan(resid: np.ndarray, x_mat: np.ndarray) -> Dict:
    """Koenker 学生化 BP 异方差检验：e²/mean(e²) 对 X 回归的 n·R²。"""
    e2 = resid ** 2
    denom = float(e2.mean())
    df = max(x_mat.shape[1] - 1, 1)
    if denom <= 0:
        return {"statistic": 0.0, "p_value": 1.0, "df": df}
    z = e2 / denom
    aux = _ols_core(z, x_mat)
    stat = float(len(resid) * aux["r2"])
    return {"statistic": stat, "p_value": float(sps.chi2.sf(stat, df)), "df": df}


def _vif(x_mat: np.ndarray, names: List[str]) -> List[Dict]:
    """方差膨胀因子：每列对其余列（含截距）回归的 1/(1−R²)。

    仅一个解释变量时（除截距外）无定义，返回空表 —— 不编造。
    """
    out: List[Dict] = []
    if x_mat.shape[1] < 3:
        return out
    for j in range(1, x_mat.shape[1]):
        others = np.column_stack([x_mat[:, 0], np.delete(x_mat, j, axis=1)])
        aux = _ols_core(x_mat[:, j], others)
        r2j = float(np.clip(aux["r2"], 0.0, 1.0 - 1e-12))
        out.append({"name": names[j], "vif": round(1.0 / (1.0 - r2j), 6)})
    return out


def _lm_spatial_diagnostics(
    resid: np.ndarray, y: np.ndarray, x_mat: np.ndarray,
    wm: WeightsMatrix,
) -> Dict:
    """LM-error / LM-lag / 稳健 LM / SARMA（Anselin 1988, ch.9）。

    公式与 spreg ``LMtests``（diagnostics_sp）逐式一致：

    - σ̃² = e'e/n；T = tr(W'W + W²)；uW = e'We/σ̃²；uWy = e'Wy/σ̃²；
    - J = [(WXβ̂)'M(WXβ̂) + T·σ̃²] / (n·σ̃²)；
    - LM_err = uW²/T（Burridge 1980）；LM_lag = uWy²/(n·J)；
    - RLM_err = (uW − T·uWy/(nJ))² / (T·(1 − T/(nJ)))；
    - RLM_lag = (uWy − uW)² / (nJ − T)；SARMA = RLM_lag + LM_err（df=2）。
    """
    n = len(resid)
    w = wm.matrix.tocsr()
    sigma2n = float(resid @ resid) / n
    utwu = float(resid @ (w @ resid)) / sigma2n
    utwy = float(resid @ (w @ y)) / sigma2n
    prod = (w.T + w) @ w
    t_term = float(prod.diagonal().sum())
    wxb = w @ (x_mat @ np.linalg.lstsq(x_mat, y, rcond=None)[0])
    xtxi = np.linalg.pinv(x_mat.T @ x_mat)
    num1 = float(wxb @ wxb - (wxb @ x_mat @ xtxi) @ x_mat.T @ wxb)
    j_term = (num1 + t_term * sigma2n) / (n * sigma2n)

    def _chi1(stat: Optional[float]) -> Dict:
        if stat is None:
            return {"statistic": None, "p_value": None}
        stat = max(float(stat), 0.0)
        return {"statistic": stat, "p_value": float(sps.chi2.sf(stat, 1))}

    out: Dict[str, Dict] = {}
    if t_term <= 0 or sigma2n <= 0:
        # 全岛权重 / 零残差：LM 系列无定义 —— 诚实报空而不是编 0。
        empty = {"statistic": None, "p_value": None}
        out.update({"lm_error": empty, "lm_lag": empty,
                    "robust_lm_error": empty, "robust_lm_lag": empty,
                    "sarma_lm": empty})
        return out
    nj = n * j_term
    out["lm_error"] = _chi1(utwu * utwu / t_term)
    out["lm_lag"] = _chi1(utwy * utwy / nj if nj > 0 else None)
    rlm_err_den = t_term * (1.0 - t_term / nj) if nj > 0 else 0.0
    rlm_lag_den = nj - t_term
    # 稳健版分母非正时不可得（小样本/权重结构退化）——诚实报 None。
    out["robust_lm_error"] = _chi1(
        (utwu - t_term * utwy / nj) ** 2 / rlm_err_den
        if rlm_err_den > 0 else None)
    out["robust_lm_lag"] = _chi1(
        (utwy - utwu) ** 2 / rlm_lag_den if rlm_lag_den > 0 else None)
    if rlm_lag_den > 0:
        sarma = (utwy - utwu) ** 2 / rlm_lag_den + utwu * utwu / t_term
        sarma = max(float(sarma), 0.0)
        out["sarma_lm"] = {"statistic": sarma,
                           "p_value": float(sps.chi2.sf(sarma, 2))}
    else:
        out["sarma_lm"] = {"statistic": None, "p_value": None}
    return out


def _residual_morans_i(
    resid: np.ndarray, wm: WeightsMatrix, perms: int,
) -> Dict:
    """残差空间自相关 Moran's I（固定种子 42，双侧 +1 校正）。"""
    n = len(resid)
    w = wm.matrix.tocoo()
    s0 = float(w.sum())
    if s0 == 0:
        return {"moran_i": None, "p_value": None, "permutations": perms}
    z = resid - resid.mean()
    denom = float(z @ z)
    if denom <= 0:
        return {"moran_i": None, "p_value": None, "permutations": perms}
    w_vals, i_idx, j_idx = w.data, w.row, w.col

    def _stat(pv: np.ndarray) -> float:
        pz = pv - pv.mean()
        p_den = float(pz @ pz)
        if p_den <= 0:
            return 0.0
        return (n / s0) * float(np.sum(w_vals * pz[i_idx] * pz[j_idx])) / p_den

    observed = _stat(z)
    expected = -1.0 / (n - 1)
    rng = np.random.default_rng(_PERMUTATION_SEED)
    extreme = 0
    for _ in cancellable(range(perms)):
        stat = _stat(rng.permutation(z))
        if abs(stat - expected) >= abs(observed - expected):
            extreme += 1
    p_value = (extreme + 1) / (perms + 1)
    return {"moran_i": float(observed), "p_value": float(p_value),
            "expected_i": float(expected), "permutations": int(perms)}


# ── 特征值路径（SAR / SEM，Ord 1975）────────────────────────────────

def _similar_to_symmetric_eigenvalues(wm_std: WeightsMatrix) -> np.ndarray:
    """行标准化 W = D⁻¹A（A 对称二值）→ 相似对称阵 S = D^{-1/2}AD^{-1/2}。

    W 与 S 相似 → 特征值相同且全实；log|I−ρW| = Σ ln(1−ρκᵢ)。
    调用前必须已过 ``SAR_EIGEN_MAX_N`` 规模门（先拒绝，再分配）。
    """
    a = wm_std.matrix.tocsr().copy()
    a.data = np.ones_like(a.data)  # 剥回二值邻接 A
    d = np.asarray(a.sum(axis=1)).ravel()
    inv_sqrt = np.zeros_like(d)
    np.divide(1.0, np.sqrt(np.where(d > 0, d, 1.0)), out=inv_sqrt, where=d > 0)
    s_sym = sparse.diags(inv_sqrt) @ a @ sparse.diags(inv_sqrt)
    return np.linalg.eigvalsh(s_sym.toarray())


def _log_jacobian(rho: float, kappa: np.ndarray) -> float:
    """Σ ln(1−ρκᵢ)；可行域外（1−ρκ ≤ 0）返回 −inf（优化器避开）。"""
    terms = 1.0 - rho * kappa
    if np.any(terms <= 0):
        return float("-inf")
    return float(np.log(terms).sum())


def _feasible_interval(kappa: np.ndarray) -> Tuple[float, float]:
    """平稳域 ρ ∈ (1/κ_min, 1/κ_max)（各向收缩一个安全边距）。"""
    k_min, k_max = float(kappa.min()), float(kappa.max())
    if k_max <= 1e-10:
        raise IllConditionedSystem(
            f"weights matrix has no positive connectivity (λ_max={k_max:.3g})",
            correction_hint="check weights connectivity or use ols_regression",
        )
    lo = 1.0 / k_min if k_min < 0 else -0.999
    hi = 1.0 / k_max
    span = hi - lo
    if not np.isfinite(span) or span <= 1e-12:
        raise IllConditionedSystem(
            f"degenerate ρ feasible interval [{lo:.3g}, {hi:.3g}]",
            correction_hint="check weights connectivity or use ols_regression",
        )
    margin = 1e-6 * span
    return lo + margin, hi - margin


def _ml_lag_fit(
    y: np.ndarray, x_mat: np.ndarray, wm: WeightsMatrix,
    kappa: np.ndarray,
) -> Tuple[float, float, np.ndarray, float]:
    """SAR 的 ρ 剖面似然有界最大化（Brent，确定性）。

    SAR：Ay = Xβ + ε（A = I − ρW）→ 给定 ρ 的 ML-β 是 **Ay 对 X** 的 OLS；
    SSE(ρ) 取变换残差平方和。返回 (ρ̂, logL_max, β(ρ̂), SSE(ρ̂))。
    """
    n = len(y)
    w = wm.matrix.tocsr()
    wy = w @ y

    def _neg_ll(rho: float) -> float:
        a_y = y - rho * wy
        beta, *_ = np.linalg.lstsq(x_mat, a_y, rcond=None)
        e = a_y - x_mat @ beta
        sse = float(e @ e)
        if sse <= 0:
            return float("inf")
        ld = _log_jacobian(rho, kappa)
        if not np.isfinite(ld):
            return float("inf")
        ll = -0.5 * n * (np.log(2.0 * np.pi) + np.log(sse / n)) + ld
        return -ll

    lo, hi = _feasible_interval(kappa)
    opt = minimize_scalar(_neg_ll, bounds=(lo, hi), method="bounded",
                          options={"xatol": 1e-10})
    rho_hat = float(opt.x)
    ll_max = float(-opt.fun)
    a_y = y - rho_hat * wy
    beta, *_ = np.linalg.lstsq(x_mat, a_y, rcond=None)
    e = a_y - x_mat @ beta
    return rho_hat, ll_max, beta, float(e @ e)


def _ml_error_fit(
    y: np.ndarray, x_mat: np.ndarray, wm: WeightsMatrix,
    kappa: np.ndarray,
) -> Tuple[float, float, np.ndarray, float]:
    """SEM 的 λ 剖面似然有界最大化（精确 GLS 剖面）。

    SEM：Cy = CXβ + ε（C = I − λW）→ 给定 λ 的 ML-β 是 **Cy 对 CX** 的
    OLS；SSE(λ) 取变换残差平方和（Ω⁻¹ = C'C 只需乘法，无需稀疏 LU）。
    返回 (λ̂, logL_max, β(λ̂), SSE(λ̂))。
    """
    n = len(y)
    w = wm.matrix.tocsr()
    wy = w @ y
    wx = w @ x_mat

    def _neg_ll(lam: float) -> float:
        c_y = y - lam * wy
        c_x = x_mat - lam * wx
        beta, *_ = np.linalg.lstsq(c_x, c_y, rcond=None)
        e = c_y - c_x @ beta
        sse = float(e @ e)
        if sse <= 0:
            return float("inf")
        ld = _log_jacobian(lam, kappa)
        if not np.isfinite(ld):
            return float("inf")
        ll = -0.5 * n * (np.log(2.0 * np.pi) + np.log(sse / n)) + ld
        return -ll

    lo, hi = _feasible_interval(kappa)
    opt = minimize_scalar(_neg_ll, bounds=(lo, hi), method="bounded",
                          options={"xatol": 1e-10})
    lam_hat = float(opt.x)
    ll_max = float(-opt.fun)
    c_y = y - lam_hat * wy
    c_x = x_mat - lam_hat * wx
    beta, *_ = np.linalg.lstsq(c_x, c_y, rcond=None)
    e = c_y - c_x @ beta
    return lam_hat, ll_max, beta, float(e @ e)


def _lr_test(ll_model: float, ll_ols: float) -> Dict:
    lr = max(2.0 * (ll_model - ll_ols), 0.0)
    return {"lr": float(lr), "df": 1,
            "p_value": float(sps.chi2.sf(lr, 1))}


# ── 叙事化入口（narrated；GeoAnalysisResult 契约同 statistics 族）────

def _coef_table(
    beta: np.ndarray, se: np.ndarray, t: np.ndarray, p: np.ndarray,
    names: List[str], vifs: Optional[List[Dict]] = None,
) -> List[Dict]:
    vif_by_name = {v["name"]: v["vif"] for v in (vifs or [])}
    table = []
    for i, name in enumerate(names):
        row = {
            "name": name,
            "coef": float(beta[i]),
            "std_error": float(se[i]) if np.isfinite(se[i]) else None,
            "t_stat": float(t[i]) if np.isfinite(t[i]) else None,
            "p_value": float(p[i]) if np.isfinite(p[i]) else None,
        }
        if vif_by_name:
            row["vif"] = vif_by_name.get(name)
        table.append(row)
    return table


def _spatial_model_suggestion(lm: Dict) -> str:
    """LM/稳健 LM 决策规则（Anselin 1988 决策树）→ 建议 id 或空串。"""

    def _sig(key: str) -> Optional[bool]:
        entry = lm.get(key) or {}
        pv = entry.get("p_value")
        return None if pv is None else bool(pv < 0.05)

    lag_sig, err_sig = _sig("lm_lag"), _sig("lm_error")
    rlag_sig, rerr_sig = _sig("robust_lm_lag"), _sig("robust_lm_error")
    if lag_sig and err_sig:
        if rlag_sig and not rerr_sig:
            return "spatial.sar_ml"
        if rerr_sig and not rlag_sig:
            return "spatial.sem_ml"
        return "spatial.sar_ml 或 spatial.sem_ml（稳健检验并存，比较 LR）"
    if lag_sig:
        return "spatial.sar_ml"
    if err_sig:
        return "spatial.sem_ml"
    return ""


def ols_regression_narrated(
    geojson: dict,
    target_field: str,
    explanatory_fields: Sequence[str],
    weights_scheme: str = "knn",
    k: int = 8,
    distance_band: float = 0.0,
    permutations: int = 99,
) -> GeoAnalysisResult:
    """OLS 线性回归 + 完整空间诊断（Foundation V2 · spatial.ols_regression）。

    y ~ X（含截距）：系数表（se/t/p/VIF）、R²/adj-R²/AIC、JB 正态性、
    Koenker-BP 异方差、残差 Moran's I（固定种子 42 置换）、LM-lag /
    LM-error / 稳健双版 / SARMA（Anselin 1988；与 spreg 同式）。残差
    Moran 显著时叙事按 LM 决策树给出 SAR/SEM 建议 —— 不替用户静默换模型。
    """
    names_raw = [f.strip() for f in explanatory_fields if str(f).strip()]
    gdf, y, x_raw, names = _regression_inputs(geojson, target_field, names_raw)
    x_mat, col_names = _design_matrix(x_raw, names)
    n = len(y)
    _check_min_samples(n, x_mat.shape[1])
    if float(np.ptp(y)) == 0.0:
        raise DegenerateData(
            f"target '{target_field}' has zero variance",
            correction_hint="check the target field for constant values",
        )
    perms = _validate_permutation_count(permutations)
    wm = _regression_weights(gdf, n, weights_scheme, k, distance_band)

    ols = _ols_core(y, x_mat)
    jb = _jarque_bera(ols["residuals"])
    bp = _breusch_pagan(ols["residuals"], x_mat)
    vifs = _vif(x_mat, col_names)
    lm = _lm_spatial_diagnostics(ols["residuals"], y, x_mat, wm)
    res_moran = (_residual_morans_i(ols["residuals"], wm, perms)
                 if wm.s0 > 0 else
                 {"moran_i": None, "p_value": None, "permutations": perms})

    data_out = {
        "n_features": int(n),
        "target_field": str(target_field),
        "explanatory_fields": list(names),
        "coefficients": _coef_table(
            ols["beta"], ols["se"], ols["t"], ols["p"], col_names, vifs),
        "r_squared": round(float(ols["r2"]), 6),
        "adj_r_squared": round(float(ols["adj_r2"]), 6),
        "f_statistic": round(float(ols["f_stat"]), 6),
        "f_p_value": round(float(ols["f_p"]), 6),
        "aic": round(float(ols["aic"]), 6),
        "log_likelihood": round(float(ols["log_lik"]), 6),
        "diagnostics": {
            "jarque_bera": jb,
            "breusch_pagan": bp,
            "vif": vifs,
            "residual_morans_i": res_moran,
            **lm,
        },
        "permutations": perms,
        "weights": wm.metadata(),
        "suggested_spatial_model": _spatial_model_suggestion(lm),
        "uncertainty": [
            ValidationMetrics(
                target="ols_regression",
                method="in_sample",
                r_squared=float(ols["r2"]),
                rmse=float(np.sqrt(ols["sse"] / n)),
                sample_count=int(n),
            ).to_evidence(),
            StatisticalSignificance(
                target="residual_morans_i",
                statistic_name="Moran's I of OLS residuals",
                statistic_value=res_moran.get("moran_i"),
                p_value=res_moran.get("p_value"),
                method="permutation",
                permutations=perms,
                alternative="two-sided",
            ).to_evidence(),
        ],
    }
    sig_counts = sum(
        1 for c in data_out["coefficients"][1:]
        if c["p_value"] is not None and c["p_value"] < 0.05)
    narrative = (
        f"OLS 拟合 {n} 个观测：R²={data_out['r_squared']:.4f}（adj "
        f"{data_out['adj_r_squared']:.4f}），{sig_counts}/{len(names)} 个解释"
        f"变量 p<0.05。")
    if res_moran.get("moran_i") is not None \
            and res_moran.get("p_value") is not None:
        if res_moran["p_value"] < 0.05:
            suggestion = data_out["suggested_spatial_model"]
            narrative += (
                f" 残差 Moran's I={res_moran['moran_i']:.4f}（p="
                f"{res_moran['p_value']:.4f}）显著 —— 存在未建模的空间依赖；")
            narrative += (
                f"LM 决策树建议 {suggestion}。" if suggestion
                else "LM 检验方向不明确，请比较 SAR/SEM 的 LR。")
        else:
            narrative += " 残差 Moran's I 不显著 —— 无明显空间依赖证据。"
    narrative += f" JB p={jb['p_value']:.4f}、BP p={bp['p_value']:.4f}。"
    return GeoAnalysisResult(True, data_out, narrative)


def slx_regression_narrated(
    geojson: dict,
    target_field: str,
    explanatory_fields: Sequence[str],
    weights_scheme: str = "knn",
    k: int = 8,
    distance_band: float = 0.0,
) -> GeoAnalysisResult:
    """SLX：OLS + 空间滞后解释变量（W·X 直接进设计阵）。

    系数表包含滞后项（``WX:<field>``），其系数是"邻居溢出效应"的直接
    估计；直接/间接效应分解需 SAR/SDM 类模型的偏导推导，此处披露系数
    本身、不做编造分解。
    """
    names_raw = [f.strip() for f in explanatory_fields if str(f).strip()]
    gdf, y, x_raw, names = _regression_inputs(geojson, target_field, names_raw)
    x_base, base_names = _design_matrix(x_raw, names)
    n = len(y)
    n_params = x_base.shape[1] + len(names)  # 预留 WX 列
    _check_min_samples(n, n_params)
    if float(np.ptp(y)) == 0.0:
        raise DegenerateData(
            f"target '{target_field}' has zero variance",
            correction_hint="check the target field for constant values",
        )
    wm = _regression_weights(gdf, n, weights_scheme, k, distance_band)
    wx_raw = wm.matrix @ x_raw
    x_mat = np.column_stack([x_base, wx_raw])
    col_names = [*base_names, *[f"WX:{f}" for f in names]]
    if x_mat.shape[1] > n - 1:
        raise InsufficientSamples(
            f"n={n} 观测不足以估计 {x_mat.shape[1]} 个参数（含 WX 滞后项）",
            correction_hint="reduce explanatory fields or add observations",
        )

    ols = _ols_core(y, x_mat)
    vifs = _vif(x_mat, col_names)
    data_out = {
        "n_features": int(n),
        "target_field": str(target_field),
        "explanatory_fields": list(names),
        "coefficients": _coef_table(
            ols["beta"], ols["se"], ols["t"], ols["p"], col_names, vifs),
        "r_squared": round(float(ols["r2"]), 6),
        "adj_r_squared": round(float(ols["adj_r2"]), 6),
        "aic": round(float(ols["aic"]), 6),
        "log_likelihood": round(float(ols["log_lik"]), 6),
        "weights": wm.metadata(),
        "uncertainty": [
            ValidationMetrics(
                target="slx_regression",
                method="in_sample",
                r_squared=float(ols["r2"]),
                rmse=float(np.sqrt(ols["sse"] / n)),
                sample_count=int(n),
            ).to_evidence(),
        ],
    }
    sig_terms = [c["name"] for c in data_out["coefficients"]
                 if c["p_value"] is not None and c["p_value"] < 0.05]
    narrative = (
        f"SLX 拟合（y ~ X + W·X，{len(names)} 个解释变量 + 滞后项）："
        f"R²={data_out['r_squared']:.4f}；p<0.05 的项：{sig_terms or '无'}。"
        f"WX 系数为邻居溢出效应的直接估计；直接/间接效应分解未做 —— 需"
        f"SAR/SDM 类模型才有严格分解。")
    return GeoAnalysisResult(True, data_out, narrative)


def _ml_narrated_guard(
    geojson: dict,
    target_field: str,
    explanatory_fields: Sequence[str],
) -> Tuple["gpd.GeoDataFrame", np.ndarray, np.ndarray, np.ndarray, List[str]]:
    """SAR/SEM 共用的输入收敛 + 规模门（特征值分配前先拒绝）。"""
    names_raw = [f.strip() for f in explanatory_fields if str(f).strip()]
    gdf, y, x_raw, names = _regression_inputs(geojson, target_field, names_raw)
    x_mat, col_names = _design_matrix(x_raw, names)
    n = len(y)
    _check_min_samples(n, x_mat.shape[1])
    if float(np.ptp(y)) == 0.0:
        raise DegenerateData(
            f"target '{target_field}' has zero variance",
            correction_hint="check the target field for constant values",
        )
    if n > SAR_EIGEN_MAX_N:
        raise ResourceScaleMismatch(
            f"SAR/SEM-ML needs the eigen-decomposition of an {n}×{n} "
            f"weights matrix",
            estimated=f"O(n³) eigvalsh at n={n}",
            limit=f"n ≤ {SAR_EIGEN_MAX_N}",
            correction_hint="use ols_regression / slx_regression, or aggregate "
                            "the data below the eigen cap",
        )
    return gdf, y, x_raw, x_mat, col_names


def sar_ml_regression_narrated(
    geojson: dict,
    target_field: str,
    explanatory_fields: Sequence[str],
    weights_scheme: str = "knn",
    k: int = 8,
    distance_band: float = 0.0,
) -> GeoAnalysisResult:
    """空间滞后模型 ML 估计（spatial.sar_ml）：y = ρWy + Xβ + ε。

    Ord 1975 特征值法：log|I−ρW| = Σ ln(1−ρκᵢ)（W 相似对称 → κ 全实）；
    ρ 在平稳域 (1/κ_min, 1/κ_max) 内有界 Brent 最大化（确定性，无随机）。
    n > 4000 → ResourceScaleMismatch（特征值 O(n³) 先拒绝）；fallback 声明
    spatial.ols_regression（approximation）只供规划层参考，运行时绝不静默
    回退。β 标准误为给定 ρ̂ 的条件渐近近似（不含 ρ 估计不确定性，披露）。
    """
    gdf, y, _x_raw, x_mat, col_names = _ml_narrated_guard(
        geojson, target_field, explanatory_fields)
    n = len(y)
    wm = _regression_weights(gdf, n, weights_scheme, k, distance_band)
    if wm.s0 == 0:
        raise DegenerateData(
            "spatial weights matrix is empty (every observation is an island)",
            correction_hint="increase the distance band / k, or check geometry "
                            "connectivity",
        )
    kappa = _similar_to_symmetric_eigenvalues(wm)
    rho, ll_max, beta, sse = _ml_lag_fit(y, x_mat, wm, kappa)
    ols = _ols_core(y, x_mat)
    sigma2 = sse / n
    lr = _lr_test(ll_max, ols["log_lik"])
    pseudo_r2 = 1.0 - sse / ols["sse"] if ols["sse"] > 0 else 0.0
    # 条件渐近协方差（给定 ρ̂）：σ²(X_ρ'A'A X_ρ)⁻¹ 的对角，A = I − ρW。
    # 不含 ρ̂ 自身的估计不确定性（披露于 docstring），t/p 仅作参考量级。
    a_x = x_mat - rho * (wm.matrix @ x_mat)
    se = np.sqrt(np.maximum(
        np.diag(np.linalg.pinv(a_x.T @ a_x)) * sigma2, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        t_vals = np.where(se > 0, beta / se, np.nan)
    p_vals = 2.0 * sps.t.sf(np.abs(t_vals),
                            df=max(n - x_mat.shape[1], 1))

    data_out = {
        "n_features": int(n),
        "target_field": str(target_field),
        "rho": round(float(rho), 6),
        "rho_bounds": [round(b, 6) for b in _feasible_interval(kappa)],
        "coefficients": _coef_table(beta, se, t_vals, p_vals, col_names, None),
        "sigma2": float(sigma2),
        "log_likelihood": round(float(ll_max), 6),
        "lr_test": lr,
        "pseudo_r_squared": round(float(pseudo_r2), 6),
        "ols_comparison": {
            "log_likelihood": round(float(ols["log_lik"]), 6),
            "r_squared": round(float(ols["r2"]), 6),
        },
        "weights": wm.metadata(),
        "uncertainty": [
            ValidationMetrics(
                target="sar_ml",
                method="in_sample",
                r_squared=float(pseudo_r2),
                rmse=float(np.sqrt(sigma2)),
                sample_count=int(n),
            ).to_evidence(),
            StatisticalSignificance(
                target="sar_lr_test",
                statistic_name="LR test SAR vs OLS (df=1)",
                statistic_value=float(lr["lr"]),
                p_value=float(lr["p_value"]),
                method="analytic_normal",
                alternative="greater",
            ).to_evidence(),
        ],
    }
    narrative = (
        f"SAR-ML（y = ρWy + Xβ + ε，Ord 1975 特征值法）：ρ̂={rho:.4f}，"
        f"σ²={sigma2:.4g}，logL={ll_max:.2f}。LR 检验（vs OLS）="
        f"{lr['lr']:.3f}（p={lr['p_value']:.4g}）—— "
        + ("空间滞后依赖显著。" if lr["p_value"] < 0.05
           else "未检出显著空间滞后依赖（ρ 与 0 无统计差异）。"))
    narrative += f" 伪 R²={pseudo_r2:.4f}（1 − SSE_SAR/SSE_OLS）。"
    return GeoAnalysisResult(True, data_out, narrative)


def sem_ml_regression_narrated(
    geojson: dict,
    target_field: str,
    explanatory_fields: Sequence[str],
    weights_scheme: str = "knn",
    k: int = 8,
    distance_band: float = 0.0,
) -> GeoAnalysisResult:
    """空间误差模型 ML 估计（spatial.sem_ml）：y = Xβ + u, u = λWu + ε。

    与 SAR 同一特征值机器（Ord 1975），λ 剖面似然有界最大化；LR 检验 vs
    OLS。规模门/孤岛防御同 SAR。
    """
    gdf, y, _x_raw, x_mat, col_names = _ml_narrated_guard(
        geojson, target_field, explanatory_fields)
    n = len(y)
    wm = _regression_weights(gdf, n, weights_scheme, k, distance_band)
    if wm.s0 == 0:
        raise DegenerateData(
            "spatial weights matrix is empty (every observation is an island)",
            correction_hint="increase the distance band / k, or check geometry "
                            "connectivity",
        )
    kappa = _similar_to_symmetric_eigenvalues(wm)
    ols = _ols_core(y, x_mat)
    lam, ll_max, beta, sse = _ml_error_fit(y, x_mat, wm, kappa)
    sigma2 = sse / n
    lr = _lr_test(ll_max, ols["log_lik"])
    pseudo_r2 = 1.0 - sse / ols["sse"] if ols["sse"] > 0 else 0.0
    # 条件渐近协方差（给定 λ̂）：σ²(C'CX·的 GLS 面包) — 与 ML-β 同一变换。
    c_x = x_mat - lam * (wm.matrix @ x_mat)
    se = np.sqrt(np.maximum(
        np.diag(np.linalg.pinv(c_x.T @ c_x)) * sigma2, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        t_vals = np.where(se > 0, beta / se, np.nan)
    p_vals = 2.0 * sps.t.sf(np.abs(t_vals),
                            df=max(n - x_mat.shape[1], 1))

    data_out = {
        "n_features": int(n),
        "target_field": str(target_field),
        "lambda": round(float(lam), 6),
        "lambda_bounds": [round(b, 6) for b in _feasible_interval(kappa)],
        "coefficients": _coef_table(beta, se, t_vals, p_vals, col_names, None),
        "sigma2": float(sigma2),
        "log_likelihood": round(float(ll_max), 6),
        "lr_test": lr,
        "pseudo_r_squared": round(float(pseudo_r2), 6),
        "ols_comparison": {
            "log_likelihood": round(float(ols["log_lik"]), 6),
            "r_squared": round(float(ols["r2"]), 6),
        },
        "weights": wm.metadata(),
        "uncertainty": [
            ValidationMetrics(
                target="sem_ml",
                method="in_sample",
                r_squared=float(pseudo_r2),
                rmse=float(np.sqrt(sigma2)),
                sample_count=int(n),
            ).to_evidence(),
            StatisticalSignificance(
                target="sem_lr_test",
                statistic_name="LR test SEM vs OLS (df=1)",
                statistic_value=float(lr["lr"]),
                p_value=float(lr["p_value"]),
                method="analytic_normal",
                alternative="greater",
            ).to_evidence(),
        ],
    }
    narrative = (
        f"SEM-ML（y = Xβ + u，u = λWu + ε）：λ̂={lam:.4f}，σ²={sigma2:.4g}，"
        f"logL={ll_max:.2f}。LR 检验（vs OLS）={lr['lr']:.3f}"
        f"（p={lr['p_value']:.4g}）—— "
        + ("空间误差依赖显著。" if lr["p_value"] < 0.05
           else "未检出显著空间误差依赖（λ 与 0 无统计差异）。"))
    return GeoAnalysisResult(True, data_out, narrative)


# ── GWR（逐观测局地 WLS；golden 锚见 test_spatial_regression_v2）─────

def _bisquare(d: np.ndarray, d_max: float) -> np.ndarray:
    """自适应 bisquare 核：w = (1 − (d/d_max)²)²，d ≥ d_max 处为 0。"""
    if d_max <= 0:
        return np.zeros_like(d)
    u = d / d_max
    return np.where(u < 1.0, (1.0 - u * u) ** 2, 0.0)


def _gwr_local_fit(
    coords: np.ndarray, y: np.ndarray, x_mat: np.ndarray,
    bandwidth: int, leave_self_out: bool,
) -> Tuple[np.ndarray, np.ndarray, float, float]:
    """逐观测 bisquare-kNN 局地 WLS。

    返回 (betas[n×p], fitted[n], sse_global, tr_S)。``leave_self_out=True``
    用于带宽 CV（LOO 预测：邻域剔除自身，带宽计数不含自身）。
    局部奇异系统抛 ``IllConditionedSystem``（如实报，不静默 pinv 吞掉）。
    """
    from scipy.spatial import cKDTree

    n, p = x_mat.shape
    k = int(bandwidth)
    tree = cKDTree(coords)
    take = k if leave_self_out else max(k - 1, 1)
    take = min(take, n - 1)
    query_k = min(k + 1, n)
    # cKDTree.query 返回 (distances, indices) —— 顺序别反。
    nn_dist, nn_idx = tree.query(coords, k=query_k)
    if query_k == 1:
        nn_idx = nn_idx[:, None]
        nn_dist = nn_dist[:, None]
    betas = np.zeros((n, p))
    fitted = np.zeros(n)
    tr_s = 0.0
    for i in cancellable(range(n)):
        nbr = np.atleast_1d(nn_idx[i])
        dist = np.atleast_1d(nn_dist[i])
        # E-4 同源防御：重合点 tie-break 可能把 self 排错位置 —— 显式剔除。
        keep = nbr != i
        nbr, dist = nbr[keep], dist[keep]
        order = np.argsort(dist, kind="stable")[:take]
        nbr, dist = nbr[order], dist[order]
        if not leave_self_out:
            d_max = float(dist[-1]) if nbr.size else 0.0
            wts = _bisquare(dist, d_max)
            nbr = np.append(nbr, i)
            wts = np.append(wts, 1.0)  # 自身 d=0 → bisquare 权重恒 1
        else:
            d_max = float(dist[-1]) if nbr.size else 0.0
            wts = _bisquare(dist, d_max)
        if nbr.size < p:
            # 邻域点数少于参数数 —— 系统欠定，如实报（带宽过小或 n 过小）。
            raise IllConditionedSystem(
                f"neighborhood size {nbr.size} < {p} parameters at "
                f"observation {i}",
                correction_hint="increase the bandwidth or reduce explanatory "
                                "fields",
            )
        xn = x_mat[nbr]
        xtwx = xn.T @ (xn * wts[:, None])
        xtwy = xn.T @ (wts * y[nbr])
        try:
            beta_i = np.linalg.solve(xtwx, xtwy)
        except np.linalg.LinAlgError as exc:
            raise IllConditionedSystem(
                f"local WLS system is singular at observation {i}",
                correction_hint="increase the bandwidth or drop collinear "
                                "explanatory fields") from exc
        if not np.all(np.isfinite(beta_i)):
            raise IllConditionedSystem(
                f"local WLS system is ill-conditioned at observation {i}",
                correction_hint="increase the bandwidth or standardize the "
                                "explanatory fields",
            )
        betas[i] = beta_i
        fitted[i] = float(x_mat[i] @ beta_i)
        if not leave_self_out:
            # 帽矩阵迹的逐行贡献（w_ii = 1）：S_ii = x_i'(X'W_iX)⁻¹x_i。
            tr_s += float(x_mat[i] @ (np.linalg.inv(xtwx) @ x_mat[i]))
    sse = float(np.sum((y - fitted) ** 2))
    return betas, fitted, sse, float(tr_s)


def _gwr_local_r2(
    coords: np.ndarray, y: np.ndarray, x_mat: np.ndarray,
    betas: np.ndarray, bandwidth: int,
) -> np.ndarray:
    """逐观测加权局部 R²（Fotheringham 2002 §2.6：含自身的邻域加权）。"""
    from scipy.spatial import cKDTree

    n, p = x_mat.shape
    k = int(bandwidth)
    tree = cKDTree(coords)
    take = min(max(k - 1, 1), n - 1)
    nn_dist, nn_idx = tree.query(coords, k=min(k + 1, n))  # (dist, idx) 序
    nn_idx = np.atleast_2d(nn_idx)
    nn_dist = np.atleast_2d(nn_dist)
    local_r2 = np.zeros(n)
    for i in range(n):
        nbr = np.atleast_1d(nn_idx[i])
        dist = np.atleast_1d(nn_dist[i])
        keep = nbr != i
        nbr, dist = nbr[keep], dist[keep]
        order = np.argsort(dist, kind="stable")[:take]
        nbr, dist = nbr[order], dist[order]
        d_max = float(dist[-1]) if nbr.size else 0.0
        wts = _bisquare(dist, d_max)
        nbr = np.append(nbr, i)
        wts = np.append(wts, 1.0)
        pred = x_mat[nbr] @ betas[i]
        y_n = y[nbr]
        sse_i = float(np.sum(wts * (y_n - pred) ** 2))
        ybar_w = float(np.sum(wts * y_n) / np.sum(wts))
        tss_i = float(np.sum(wts * (y_n - ybar_w) ** 2))
        local_r2[i] = 1.0 - sse_i / tss_i if tss_i > 0 else 0.0
    return local_r2


def _gwr_summarize(values: np.ndarray) -> Dict:
    v = np.asarray(values, dtype=float)
    return {"min": round(float(v.min()), 6),
            "median": round(float(np.median(v)), 6),
            "max": round(float(v.max()), 6)}


def gwr_regression_narrated(
    geojson: dict,
    target_field: str,
    explanatory_fields: Sequence[str],
    bandwidth: int = 30,
    bandwidth_selection: str = "fixed",
) -> GeoAnalysisResult:
    """地理加权回归（spatial.gwr；Brunsdon 1996 / Fotheringham 2002）。

    自适应 bisquare 核，带宽 = 最近邻数 k（契约默认 30，运行时钳制到
    [5, n//2]）；``bandwidth_selection="cv"`` 在有界候选网格上做留一 CV
    （确定性穷举）。输出全局 R² / AIC / AICc、逐观测局部 R² 摘要、逐系数
    空间变异摘要（min/median/max/IQR）；n ≤ 2000 时附逐观测系数面（列
    表），超过则只回摘要 + ``full_coefficient_surfaces=False`` 披露。
    AIC/AICc 用帽矩阵迹的有效参数 q = tr(S)+1 的高斯形式（Fotheringham
    2002 的常用近似；GWR 的 AICc 没有唯一公认式，披露于 descriptor）。
    """
    names_raw = [f.strip() for f in explanatory_fields if str(f).strip()]
    gdf, y, x_raw, names = _regression_inputs(geojson, target_field, names_raw)
    x_mat, col_names = _design_matrix(x_raw, names)
    n, p = x_mat.shape
    _check_min_samples(n, p)
    if float(np.ptp(y)) == 0.0:
        raise DegenerateData(
            f"target '{target_field}' has zero variance",
            correction_hint="check the target field for constant values",
        )
    selection = str(bandwidth_selection or "fixed").lower()
    if selection not in ("fixed", "cv"):
        raise UnsupportedMethod(
            f"bandwidth_selection must be 'fixed' or 'cv' (got {selection!r})",
            correction_hint="use bandwidth_selection='cv' for data-driven "
                            "bandwidth",
        )
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    k_cap = max(5, n // 2)
    k_fixed = int(min(max(int(bandwidth), 5), k_cap))

    cv_scores: List[Dict] = []
    envelope: Optional[Dict] = None
    if selection == "cv":
        for cand in _GWR_BW_CANDIDATES:
            if cand > k_cap:
                break
            _, _, sse_cv, _ = _gwr_local_fit(
                coords, y, x_mat, cand, leave_self_out=True)
            cv_scores.append({"bandwidth": int(cand),
                              "cv_mse": round(sse_cv / n, 6)})
        best = min(cv_scores, key=lambda e: e["cv_mse"])
        k_used = int(best["bandwidth"])
        envelope = {
            "scheme": f"bandwidth LOO-CV over bounded grid "
                      f"{list(_GWR_BW_CANDIDATES)} (clipped to ≤ {k_cap})",
            "selected": k_used,
        }
    else:
        k_used = k_fixed

    betas, fitted, sse, tr_s = _gwr_local_fit(
        coords, y, x_mat, k_used, leave_self_out=False)
    local_r2 = _gwr_local_r2(coords, y, x_mat, betas, k_used)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - sse / ss_tot if ss_tot > 0 else 0.0
    q_eff = tr_s + 1.0  # +1 计 σ²
    aic = n * (np.log(2.0 * np.pi) + np.log(sse / n) + 1.0) + 2.0 * q_eff
    aicc = aic + (2.0 * q_eff * (q_eff + 1.0) / (n - q_eff - 1.0)
                  if n - q_eff - 1.0 > 0 else 0.0)
    rmse = float(np.sqrt(sse / n))

    variation: Dict[str, Dict] = {}
    for j, name in enumerate(col_names):
        col = betas[:, j]
        variation[name] = {
            **_gwr_summarize(col),
            "iqr": round(float(np.percentile(col, 75)
                               - np.percentile(col, 25)), 6),
        }

    data_out: Dict = {
        "n_features": int(n),
        "target_field": str(target_field),
        "explanatory_fields": list(names),
        "r_squared": round(float(r2), 6),
        "aic": round(float(aic), 6),
        "aicc": round(float(aicc), 6),
        "effective_params": round(float(q_eff), 6),
        "rmse": round(rmse, 6),
        "bandwidth": {
            "requested": int(bandwidth),
            "selected": int(k_used),
            "clamped_cap": int(k_cap),
            "selection": selection,
            "note": "自适应 bisquare 核的最近邻计数（含自身）",
        },
        "local_r2": _gwr_summarize(local_r2),
        "coefficient_variation": variation,
        "full_coefficient_surfaces": bool(n <= GWR_FULL_SURFACE_MAX_N),
        "uncertainty": [
            ValidationMetrics(
                target="gwr_regression",
                method="in_sample",
                r_squared=float(r2),
                rmse=rmse,
                sample_count=int(n),
            ).to_evidence(),
            FieldUncertainty(
                target="gwr_local_coefficients",
                field_name=", ".join(col_names[:8]),
                summary=[
                    UncertaintyMeasure(
                        measure="value",
                        value=variation[name]["iqr"],
                        method=f"IQR of local coefficient '{name}'",
                    )
                    for name in col_names[:8]
                ],
                sample_count=int(n),
            ).to_evidence(),
        ],
    }
    if envelope is not None:
        data_out["uncertainty"].append(
            SensitivityEnvelope(
                target="gwr_bandwidth",
                perturbation_scheme=envelope["scheme"],
                tipping_points=[f"selected bandwidth={envelope['selected']}"],
                notes="各候选带宽的 CV MSE 见 bandwidth_cv（确定性网格穷举）",
            ).to_evidence())
        data_out["bandwidth_cv"] = cv_scores
    if n <= GWR_FULL_SURFACE_MAX_N:
        surfaces: Dict[str, List[float]] = {
            name: [round(float(v), 6) for v in betas[:, j]]
            for j, name in enumerate(col_names)
        }
        surfaces["local_r2"] = [round(float(v), 6) for v in local_r2]
        data_out["surfaces"] = surfaces
    narrative = (
        f"GWR（bisquare 自适应核，带宽={k_used} 最近邻"
        f"{'，CV 选择' if selection == 'cv' else ''}）："
        f"R²={r2:.4f}，AICc={aicc:.1f}。局部 R² ∈ "
        f"[{data_out['local_r2']['min']:.4f}, "
        f"{data_out['local_r2']['max']:.4f}]；截距 IQR="
        f"{variation['intercept']['iqr']:.4g} —— "
        + ("系数空间变异明显，全局模型掩盖了局部过程。"
           if variation["intercept"]["iqr"] > 1e-9 else
           "系数空间变异可忽略，接近全局 OLS。"))
    if n > GWR_FULL_SURFACE_MAX_N:
        narrative += (
            f" n={n} 超过全系数面上限 {GWR_FULL_SURFACE_MAX_N} —— 仅回摘要。")
    return GeoAnalysisResult(True, data_out, narrative)
