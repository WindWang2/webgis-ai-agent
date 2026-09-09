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

    # 时空邻域：空间 k* 候选 × 时间窗过滤（k 不足时逐步放宽空间候选）
    tree = cKDTree(pts_t)
    k_query = min(MAX_NEIGHBORS * 4, n)
    d_all, i_all = tree.query(targets_t, k=k_query)
    d_all = np.asarray(d_all, float).reshape(len(targets_t), k_query)
    i_all = np.asarray(i_all, int).reshape(len(targets_t), k_query)

    C00 = float(st_covariance(st, np.array([0.0]), np.array([0.0]))[0])
    preds = np.empty(len(targets_t), dtype=float)
    varis = np.empty(len(targets_t), dtype=float)
    degraded = 0
    n_used = np.empty(len(targets_t), dtype=int)

    for start in cancellable(range(0, len(targets_t), 512), every=1):
        end = min(start + 512, len(targets_t))
        for r_i in range(start, end):
            tau = times_sec[i_all[r_i]] - target_times_sec[r_i]
            in_window = (
                np.abs(tau) <= (time_window_sec if time_window_sec is not None
                                else np.inf))
            idx = i_all[r_i][in_window][:k]
            if len(idx) < 2:
                # 时间窗内样本不足：放宽为纯空间 k 近邻（诚实计数披露）
                idx = i_all[r_i][:k]
                degraded += 1
            h = np.sqrt(((pts_t[idx] - targets_t[r_i]) ** 2).sum(axis=1))
            nb_tau = times_sec[idx] - target_times_sec[r_i]
            m = len(idx) + 1
            C = np.empty((m, m), dtype=float)
            diff = pts_t[idx][:, None, :] - pts_t[idx][None, :, :]
            h_ss = np.sqrt((diff ** 2).sum(-1))
            tau_ss = times_sec[idx][:, None] - times_sec[idx][None, :]
            C[:m - 1, :m - 1] = st_covariance(st, h_ss, tau_ss)
            np.fill_diagonal(C[:m - 1, :m - 1], C00)
            C[:m - 1, m - 1] = 1.0
            C[m - 1, :m - 1] = 1.0
            C[m - 1, m - 1] = 0.0
            rhs = np.empty(m, dtype=float)
            rhs[:m - 1] = st_covariance(st, h, nb_tau)
            rhs[m - 1] = 1.0
            try:
                sol = np.linalg.solve(C, rhs)
                if not np.isfinite(sol).all():
                    raise np.linalg.LinAlgError("non-finite")
                w = sol[:m - 1]
                preds[r_i] = float(w @ values[idx])
                var = max(C00 - float(sol[:m - 1] @ rhs[:m - 1]) - float(sol[m - 1]), 0.0)
                if var <= 0:
                    degraded += 1
                varis[r_i] = var
            except np.linalg.LinAlgError:
                preds[r_i] = float(np.mean(values[idx]))
                varis[r_i] = float(np.var(values[idx]))
                degraded += 1
            n_used[r_i] = len(idx)

    return {
        "predictions": preds,
        "variances": varis,
        "n_neighbors": n_used,
        "degraded_cells": int(degraded),
        "model": st,
    }


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
