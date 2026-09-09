"""Spatiotemporal Kriging（separable / product-sum）—— science-v4 W7。

时空克里金把采样建模为 (x, y, t)（空间坐标 + **秒**制时间戳），在时空
协方差下逐目标 (s₀, t₀) 求解。两种基础模型（De Iaco / Cressie–Huang
传统的确定性子集，PSD 按构造）：

- **separable**（可分离）：C(h, τ) = C₀·ρ_s(h)·ρ_t(τ) —— 时空相关是
  空间/时间相关的乘积；严格有效（ρ_s、ρ_t 为相关函数）。
- **product_sum**（积和，双时间尺度混合）：C(h, τ) = s·ρ_s(h)·[w·ρ_t(τ/r₁)
  + (1−w)·ρ_t(τ/r₂)] —— **可分离项的正组合，按构造半正定**（De Iaco
  product-sum 类）；τ=0 精确退化为空间协方差 s·ρ_s(h)。

时间单位契约：**秒**（epoch seconds / 相对秒由调用方声明），空间单位
米（投影工作帧）。`τ=0 可分离退化为纯空间协方差` 是 conformance 锚。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from scipy.spatial import cKDTree

from app.lib.cancellation import cancellable
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    ResourceScaleMismatch,
)

from app.lib.geo_analysis import cv
from app.lib.geo_analysis.kriging import (
    MAX_NEIGHBORS,
    VariogramFit,
    apply_anisotropy,
    fit_variogram,
    _gamma,
)

logger = logging.getLogger(__name__)

ST_MIN_SAMPLES = 12                     # 时空系统（≥3 时相 × ≥4 站点经验下限）
ST_MAX_SAMPLES = 300_000                # 样本硬顶
ST_DEFAULT_TIME_WINDOW_SEC = 60 * 60 * 24 * 30  # 默认时间窗 30 天

ST_MODEL_VOCABULARY = ("separable", "product_sum")


@dataclass
class STModel:
    """时空协方差模型参数（相关函数；PSD 按构造）。"""

    model: str                        # separable | product_sum
    spatial: VariogramFit             # 空间变异函数（γ(h)，含 nugget）
    temporal_range_sec: float         # 快时间尺度变程（秒；指数形状）
    sill: float                       # 场先验方差（C(0,0)）
    temporal_range_sec_slow: Optional[float] = None  # product_sum 慢尺度（缺省 3×）

    def params(self) -> dict:
        out = {
            "model": self.model,
            "temporal_range_seconds": round(float(self.temporal_range_sec), 3),
            "sill_scale": round(float(self.sill), 6),
            "variance_at_origin": round(float(
                float(st_covariance(self, np.array([0.0]), np.array([0.0]))[0])), 6),
            "spatial_variogram": self.spatial.params(),
        }
        if self.temporal_range_sec_slow is not None:
            out["temporal_range_seconds_slow"] = round(
                float(self.temporal_range_sec_slow), 3)
        return out


def _spatial_corr(g: VariogramFit, h: np.ndarray) -> np.ndarray:
    """标准化空间相关函数 ρ_s(h)：h=0 处严格为 1（相关函数定义要求
    ρ(0)=1；nugget 只进入 h>0 的截断——γ(0)=0、γ(0⁺)=nugget 的规范
    半方差语义）。否则 product-sum 的连续 PSD 有效性不成立。"""
    total = abs(float(g.sill)) + abs(float(g.nugget))
    if total <= 0:
        raise DegenerateData("空间变异函数基台为 0（退化场）")
    h = np.asarray(h, dtype=float)
    gamma_pure = np.where(h <= 0.0, 0.0,
                          _gamma(g.model, h, g.sill, g.range_m, g.nugget,
                                 nu=g.nu))
    return 1.0 - gamma_pure / total


def _temporal_corr(tau: np.ndarray, range_sec: float) -> np.ndarray:
    """指数时间相关 ρ_t(τ) = exp(−3τ/range)（秒制）。"""
    tau = np.abs(np.asarray(tau, dtype=float))
    return np.exp(-3.0 * tau / max(float(range_sec), 1e-9))


def fit_st_model(
    spatial_variogram: Optional[VariogramFit] = None,
    pts_metric: Optional[np.ndarray] = None,
    values: Optional[np.ndarray] = None,
    temporal_range_sec: float = ST_DEFAULT_TIME_WINDOW_SEC,
    model: str = "product_sum",
    sill: Optional[float] = None,
) -> STModel:
    """装配时空模型（空间变异函数现场拟合或消费调用方结果）。"""
    if model not in ST_MODEL_VOCABULARY:
        raise DegenerateData(
            f"ST 模型必须是 {ST_MODEL_VOCABULARY} 之一，got {model!r}")
    if temporal_range_sec <= 0:
        raise DegenerateData("temporal_range_sec 必须为正（秒制时间单位）")
    if spatial_variogram is None:
        if pts_metric is None or values is None:
            raise InsufficientSamples("需要空间样本或预拟合变异函数")
        spatial_variogram = fit_variogram(pts_metric, values, model="auto")
    sill_est = float(sill) if sill is not None else (
        abs(float(spatial_variogram.sill)) + abs(float(spatial_variogram.nugget)))
    if sill_est <= 0:
        raise DegenerateData("场先验方差非正（退化）")
    slow = float(temporal_range_sec) * 3.0 if model == "product_sum" else None
    return STModel(model=model, spatial=spatial_variogram,
                   temporal_range_sec=float(temporal_range_sec),
                   sill=sill_est, temporal_range_sec_slow=slow)


def st_covariance(st: STModel, h: np.ndarray, tau: np.ndarray) -> np.ndarray:
    """时空协方差（解析；供 oracle 与求解共用单一实现）。

    separable：C = s·ρ_s·ρ_t；product_sum：C = s·ρ_s·[0.5·ρ_t(τ/r) +
    0.5·ρ_t(τ/3r)] —— 可分离项正组合，PSD 按构造；τ=0 两模型都精确
    退化为 s·ρ_s(h)。
    """
    rho_s = _spatial_corr(st.spatial, np.asarray(h, dtype=float))
    if st.model == "separable":
        return st.sill * rho_s * _temporal_corr(tau, st.temporal_range_sec)
    r_fast = st.temporal_range_sec
    r_slow = st.temporal_range_sec_slow or (3.0 * r_fast)
    rho_t_mix = 0.5 * _temporal_corr(tau, r_fast) + 0.5 * _temporal_corr(tau, r_slow)
    return st.sill * rho_s * rho_t_mix


def st_kriging(
    pts_metric: np.ndarray,
    values: np.ndarray,
    times_sec: np.ndarray,
    targets_metric: np.ndarray,
    target_times_sec: np.ndarray,
    st: STModel,
    k: int = 16,
    time_window_sec: Optional[float] = ST_DEFAULT_TIME_WINDOW_SEC,
    anisotropy_angle: float = 0.0,
    anisotropy_ratio: float = 1.0,
) -> dict:
    """时空普通克里金：逐目标 (s₀,t₀) 的 k 近邻（空间 cKDTree × 时间窗）。

    系统 [C 1][w] = [c₀]（ST 协方差 + 无偏约束 Σw=1）；方差 = C(0,0) −
    wᵗc₀（钳 ≥0，负值计数）。时间窗外的样本不进入邻域（诚实缺省，不
    用远处时相硬凑）。返回 ``{"predictions", "variances", "n_neighbors",
    "degraded_cells", "model"}``。

    science-v5 W4 实现语义（数值与 V4 逐目标路径一致，differential 钉死）：
    邻域定长 k 填充批量求解——窗口内候选 ≥2 取窗口内前 k（不足 k 哨兵
    填充，掩膜行 w=0 精确置零：整行/列清零含约束行列）；窗口内 <2 放宽
    为纯空间 k 近邻 + degraded 计数（V4 relax 语义）；solve 失败/非有限
    → 邻域均值回退（counted）。
    """
    pts_metric = np.asarray(pts_metric, dtype=float)
    values = np.asarray(values, dtype=float)
    times_sec = np.asarray(times_sec, dtype=float)
    targets_metric = np.asarray(targets_metric, dtype=float)
    target_times_sec = np.asarray(target_times_sec, dtype=float)
    n = len(values)
    if n < ST_MIN_SAMPLES:
        raise InsufficientSamples(
            f"时空克里金至少需要 {ST_MIN_SAMPLES} 个样本（got {n}）")
    if n > ST_MAX_SAMPLES:
        raise ResourceScaleMismatch(
            f"时空样本 {n:,} 超过上限 {ST_MAX_SAMPLES:,}",
            estimated=f"{n} samples", limit=f"≤{ST_MAX_SAMPLES}",
            correction_hint="按时间窗切片或空间分层抽稀")
    if len(times_sec) != n or len(target_times_sec) != len(targets_metric):
        raise DegenerateData("时间数组与样本/目标数量不一致")
    if not (np.isfinite(times_sec).all() and np.isfinite(target_times_sec).all()):
        raise DegenerateData("时间戳含非有限值（须为 epoch/相对秒）")
    if float(np.ptp(times_sec)) == 0.0:
        raise DegenerateData("全部样本同一时刻——时空维退化（改用空间克里金）")

    k = int(max(2, min(k, MAX_NEIGHBORS, n)))
    pts_t = apply_anisotropy(pts_metric, anisotropy_angle, anisotropy_ratio)
    targets_t = apply_anisotropy(targets_metric, anisotropy_angle, anisotropy_ratio)

    # 时空邻域：空间 k* 候选 × 时间窗过滤（k 不足时逐步放宽空间候选）。
    # Review R2-#6：邻域解析按 512-chunk 进行（避免 (n_t, k_query) 全量
    # tau/argsort 中间量常驻）；距离数组不消费不落盘。
    tree = cKDTree(pts_t)
    k_query = min(MAX_NEIGHBORS * 4, n)
    _d_all, i_all = tree.query(targets_t, k=k_query)
    i_all = np.asarray(i_all, int).reshape(len(targets_t), k_query)

    C00 = float(st_covariance(st, np.array([0.0]), np.array([0.0]))[0])
    n_t = len(targets_t)

    # science-v5 W4：逐目标循环 → 定长 k 填充批量系统。
    # 邻域按逐目标 relaxed 列表定长化（架构挑战 C1）：窗口内候选 ≥2 →
    # 取窗口内前 k 个（V4 语义：relax 后仍解克里金系统）；<2 → 放宽为
    # 纯空间前 k 个 + degraded 计数（V4 同义）。不足 k 的槽位哨兵填充。
    nb_idx = np.empty((n_t, k), dtype=int)
    valid = np.empty((n_t, k), dtype=bool)
    relaxed_total = 0
    m = k + 1

    preds = np.empty(n_t, dtype=float)
    varis = np.empty(n_t, dtype=float)
    degraded = 0
    n_used = np.empty(n_t, dtype=int)

    for start in cancellable(range(0, n_t, 512), every=1):
        end = min(start + 512, n_t)
        c = end - start
        # ── 邻域解析（chunk 局部；窗口过滤 + 稳定保序）────────────────
        i_c = i_all[start:end]
        tau_c = times_sec[i_c] - target_times_sec[start:end, None]
        in_window = (
            np.abs(tau_c) <= (time_window_sec if time_window_sec is not None
                              else np.inf))
        win_counts = in_window.sum(axis=1)
        relaxed = win_counts < 2
        relaxed_total += int(relaxed.sum())
        if relaxed.any():
            r_idx = np.nonzero(relaxed)[0]
            nb_idx[start:end][r_idx] = i_c[r_idx][:, :k]
            valid[start:end][r_idx] = True
        if (~relaxed).any():
            w_idx = np.nonzero(~relaxed)[0]
            w_order = np.argsort(~in_window[w_idx], axis=1, kind="stable")
            w_sorted = i_c[w_idx][np.arange(len(w_idx))[:, None],
                                  w_order][:, :k]
            win_k = np.minimum(in_window[w_idx].sum(axis=1), k)
            v = np.arange(k)[None, :] < win_k[:, None]
            nb_idx[start:end][w_idx] = np.where(v, w_sorted, w_sorted[:, :1])
            valid[start:end][w_idx] = v

        idx = nb_idx[start:end]                   # (c, k)
        vmask = valid[start:end]                  # (c, k)
        nb_xy = pts_t[idx]                        # (c, k, 2)
        t_xy = targets_t[start:end]               # (c, 2)
        tau = times_sec[idx] - target_times_sec[start:end, None]

        C = np.empty((c, m, m), dtype=float)
        diff = nb_xy[:, :, None, :] - nb_xy[:, None, :, :]
        h_ss = np.sqrt((diff ** 2).sum(-1))       # (c, k, k)
        tau_ss = times_sec[idx][:, :, None] - times_sec[idx][:, None, :]
        C[:, :k, :k] = st_covariance(st, h_ss, tau_ss)
        diag = np.arange(k)
        C[:, diag, diag] = C00
        rhs = np.empty((c, m), dtype=float)
        h0 = np.sqrt(((nb_xy - t_xy[:, None, :]) ** 2).sum(-1))   # (c, k)
        rhs[:, :k] = st_covariance(st, h0, tau)
        rhs[:, k] = 1.0
        C[:, :k, k] = vmask.astype(float)         # 约束只统计有效槽位
        C[:, k, :k] = vmask.astype(float)
        C[:, k, k] = 0.0

        # 哨兵槽位：整行/整列清零（含约束行/列条目——否则 w_pad = −μ 把
        # 偏差拉进全部有效权重）→ 单位阵 + rhs=0 ⇒ w_pad 恰为 0。
        pad_rows, pad_cols = np.nonzero(~vmask)
        if pad_rows.size:
            C[pad_rows, pad_cols, :] = 0.0
            C[pad_rows, :, pad_cols] = 0.0
            C[pad_rows, pad_cols, pad_cols] = 1.0
            rhs[pad_rows, pad_cols] = 0.0

        # 批量求解（隔离条件 = LinAlgError ∨ 非有限——与 LMC 同款：
        # 恰奇异整栈 raise → 逐行重解；非有限 → 邻域均值回退，counted）
        try:
            sol_all = np.linalg.solve(C, rhs[:, :, None])[:, :, 0]
        except np.linalg.LinAlgError:
            sol_all = None
        ok_mask = (np.isfinite(sol_all).all(axis=1)
                   if sol_all is not None else None)
        for r_i in range(c):
            sol = None
            if ok_mask is not None and ok_mask[r_i]:
                sol = sol_all[r_i]
            else:
                try:
                    cand = np.linalg.solve(C[r_i], rhs[r_i])
                    if not np.isfinite(cand).all():
                        raise np.linalg.LinAlgError("non-finite")
                    sol = cand
                except np.linalg.LinAlgError:
                    sol = None
            gi = start + r_i
            vm = vmask[r_i]
            if sol is None:
                preds[gi] = float(np.mean(values[idx[r_i][vm]]))
                varis[gi] = float(np.var(values[idx[r_i][vm]]))
                degraded += 1
                continue
            w = sol[:k]
            nb_vals = values[idx[r_i]]
            preds[gi] = float(w[vm] @ nb_vals[vm])
            var = C00 - float(w @ rhs[r_i, :k]) - float(sol[k])
            var = max(var, 0.0)
            if var <= 0:                          # V4 语义：钳后 ≤0 计 degraded
                degraded += 1
            varis[gi] = var

    n_used = valid.sum(axis=1).astype(int)

    return {
        "predictions": preds,
        "variances": varis,
        "n_neighbors": n_used,
        "degraded_cells": int(degraded + relaxed_total),
        "model": st,
    }


def st_cross_validate(
    pts_metric: np.ndarray,
    values: np.ndarray,
    times_sec: np.ndarray,
    *,
    scheme: str = "temporal_forward",
    folds: int = 4,
    k: int = 12,
    time_window_sec: Optional[float] = None,
    model: str = "product_sum",
    temporal_range_sec: float = ST_DEFAULT_TIME_WINDOW_SEC,
) -> "cv.CVReport":
    """时空克里金交叉验证（science-v5 W4；CV 框架唯一事实源）。

    scheme="temporal_forward"（默认）：时间前向链——fold k 的训练 = 严格
    早于测试块首时刻的样本（expanding window，零 future leakage）；
    ``scheme="spatial_block"``：空间块（同一时刻样本可跨训练/测试，
    评的是空间外推诚实误差）。逐折**完整重拟合**时空模型（空间变异函数
    + 模型装配），无跨折泄漏。

    样本 < ST_MIN_SAMPLES → 诚实退化报告（不产指标）；个别折因训练子集
    时间维退化（单时刻）失败 → fold_failures 计数，不静默。
    """
    pts_metric = np.asarray(pts_metric, dtype=float)
    values = np.asarray(values, dtype=float)
    times_sec = np.asarray(times_sec, dtype=float)
    n = len(values)
    if n < ST_MIN_SAMPLES:
        return cv.CVReport(
            n_samples=n, folds=0, folds_used=0, scheme=scheme,
            note=(
                f"样本量 {n} < {ST_MIN_SAMPLES}，无法进行可靠的时空交叉"
                "验证；不确定性仅由模型方差表达。"),
        )
    from app.lib.geo_analysis.kriging import fit_variogram

    def fit_fn(train, train_vals):
        vfit = fit_variogram(pts_metric[train], train_vals, model="auto")
        return fit_st_model(
            spatial_variogram=vfit, temporal_range_sec=temporal_range_sec,
            model=model)

    def predict_fn(st_m, train, test):
        # 条件集 = **仅训练子集**（Review R1-B1：全量条件会让测试样本
        # 自条件——τ=0 邻居权重 ≈1，CV 指标无意义且时间守卫不可见）。
        res = st_kriging(
            pts_metric[train], values[train], times_sec[train],
            pts_metric[test], times_sec[test], st_m,
            k=k, time_window_sec=time_window_sec,
        )
        return res["predictions"], res["variances"]

    return cv.run_cross_validation(
        pts_metric, values, fit_fn, predict_fn,
        scheme=scheme, folds=folds, times_sec=times_sec,
        min_samples=ST_MIN_SAMPLES,
    )


def st_kriging_surface(
    points_geojson: Any,
    value_field: str,
    time_field: str,
    target_time_sec: float,
    resolution: int = 7,
    model: str = "product_sum",
    temporal_range_sec: float = ST_DEFAULT_TIME_WINDOW_SEC,
    time_window_sec: Optional[float] = ST_DEFAULT_TIME_WINDOW_SEC,
    neighbors: int = 16,
) -> dict:
    """时空克里金表面：H3 网格在 ``target_time_sec`` 时刻的预测 + 方差。

    样本须带 ``time_field``（epoch/相对**秒**）；CRS 语义与 IDW/SGS 同
    （自动米制工作帧）。
    """
    import geopandas as gpd
    import h3
    import pandas as pd

    from app.lib.geo_analysis.interpolation import (
        _pick_metric_crs,
        _target_cells_for_samples,
        _validate_resolution,
    )
    from app.lib.geo_processor.core import safe_parse, to_feature_collection

    # review R1-C3/R2-C1：时间戳必须与样本**同一循环**提取 —— preamble 的
    # _parse_point_values 会丢弃非有限值并聚合重复坐标（不保基数），第三次
    # 遍历对齐会错位/越界。此处自解析（value+time 同循环），网格仍复用
    # 共享机器（_pick_metric_crs/_target_cells_for_samples）。
    _validate_resolution(resolution)
    parsed = safe_parse(points_geojson)
    if parsed is None:
        raise ValueError("无法解析输入点要素 GeoJSON")
    features = to_feature_collection(parsed).get("features", [])
    lons: list[float] = []
    lats: list[float] = []
    vals: list[Any] = []
    tms: list[Any] = []
    for f in features:
        if not isinstance(f, dict):
            continue
        geom = f.get("geometry")
        if not isinstance(geom, dict) or geom.get("type") != "Point":
            continue
        props = f.get("properties") or {}
        if value_field not in props or time_field not in props:
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lons.append(float(coords[0]))
        lats.append(float(coords[1]))
        vals.append(props[value_field])
        tms.append(props[time_field])
    if not lons:
        raise ValueError(
            f"没有可用于时空克里金的点要素（需 Point 几何且含字段 "
            f"'{value_field}'/'{time_field}'）")
    v_arr = pd.to_numeric(pd.Series(vals), errors="coerce").to_numpy(dtype=float)
    t_arr = pd.to_numeric(pd.Series(tms), errors="coerce").to_numpy(dtype=float)
    ok = np.isfinite(v_arr) & np.isfinite(t_arr)
    lonlat = np.column_stack([np.asarray(lons, float), np.asarray(lats, float)])[ok]
    values = v_arr[ok]
    times = t_arr[ok]
    if len(values) < ST_MIN_SAMPLES or float(np.ptp(times)) == 0.0:
        raise DegenerateData(
            "时空样本时间维不足（需 ≥12 且跨多时相，秒制时间戳）",
            correction_hint="检查 time_field 是否为 epoch/相对秒，且覆盖多个时刻")

    working_crs = _pick_metric_crs(lonlat)
    pts_gdf = gpd.GeoDataFrame(
        {"v": values},
        geometry=gpd.points_from_xy(lonlat[:, 0], lonlat[:, 1]),
        crs="EPSG:4326",
    ).to_crs(working_crs)
    pts_metric = np.column_stack(
        (pts_gdf.geometry.x.values, pts_gdf.geometry.y.values)
    )
    target_cells, bbox = _target_cells_for_samples(
        lonlat, resolution, label="时空克里金")
    if target_cells:
        cell_latlng = np.array([h3.cell_to_latlng(c) for c in target_cells])
        cell_gdf = gpd.GeoDataFrame(
            geometry=gpd.points_from_xy(cell_latlng[:, 1], cell_latlng[:, 0]),
            crs="EPSG:4326",
        ).to_crs(working_crs)
        cell_metric = np.column_stack(
            (cell_gdf.geometry.x.values, cell_gdf.geometry.y.values)
        )
    else:
        cell_metric = np.empty((0, 2), dtype=float)
    metadata: dict[str, Any] = {
        "algorithm": "interpolation.st_kriging",
        "value_field": value_field,
        "time_field": time_field,
        "target_time_sec": float(target_time_sec),
        "resolution": int(resolution),
        "working_crs": working_crs,
        "n_samples": int(len(values)),
        "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
    }
    if not target_cells:
        metadata["cell_count"] = 0
        return {"records": [], "metadata": metadata}

    st = fit_st_model(
        pts_metric=pts_metric, values=values,
        temporal_range_sec=temporal_range_sec, model=model)
    result = st_kriging(
        pts_metric, values, times, cell_metric,
        np.full(len(cell_metric), float(target_time_sec)), st,
        k=neighbors, time_window_sec=time_window_sec,
    )
    metadata["st_model"] = st.params()
    metadata["effective_time_window_sec"] = (
        float(time_window_sec) if time_window_sec is not None else None)
    metadata["degraded_cells"] = result["degraded_cells"]
    metadata["variance_range"] = [
        round(float(result["variances"].min()), 6),
        round(float(result["variances"].max()), 6),
    ]
    # science-v5 W6/W7：uncertainty artifact + 执行方式规划（纯函数证据）
    from app.lib.geo_analysis.uncertainty import (
        data_quality_summary,
        from_variance as _artifact_from_variance,
    )

    artifact = _artifact_from_variance(
        "st_kriging_variance", result["predictions"], result["variances"],
        provenance={"st_model": st.params(),
                    "target_time_sec": float(target_time_sec)},
        data_quality=data_quality_summary(
            n_samples=int(len(values)), n_targets=len(target_cells),
            value_field=value_field, working_crs=working_crs),
    )
    metadata["uncertainty"] = artifact.to_dict()
    metadata["renderer"] = artifact.to_renderer_metadata()
    from app.lib.gis.backend_selection import ScaleProfile, plan_execution

    metadata["execution_plan"] = plan_execution(
        "interpolation.st_kriging",
        ScaleProfile(raster_cells=len(target_cells))).to_dict()
    records = [
        {
            "h3_index": cell,
            "value": float(pred),
            "st_variance": float(var),
            "st_stddev": float(np.sqrt(max(var, 0.0))),
            "n_neighbors": int(n_used),
        }
        for cell, pred, var, n_used in zip(
            target_cells, result["predictions"], result["variances"],
            result["n_neighbors"])
    ]
    return {"records": records, "metadata": metadata}
