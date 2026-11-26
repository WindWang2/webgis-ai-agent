"""科学评价原语工具面（method-experiment WP3）—— 三个缺口原语的真实实现。

Phase 0 证实这三个能力在 master 不存在（cv.py 有 splitter 与 kriging
block-CV、但无任意方法的分块 CV 工具；KDE 无守恒/边界诊断；聚类无
稳定性量度）：

1. ``interpolation_block_cv``   任意插值方法的空间分块 CV（cv.py 编排 +
                                既有驱动复用；全部候选共享同一确定性折分配）
2. ``density_edge_diagnostic``  KDE 质量守恒/边界带诊断（density.py 内核
                                复用；视觉热力不能替代定量结论的机器证据）
3. ``cluster_stability``        聚类稳定性/轮廓/噪声量度（多 seed / bootstrap
                                子采样 ARI；silhouette 仅欧氏适用情形）

红线：

- 执行真相 = 本工具注册后经 ToolDispatchService 被调用（方法实验引擎的
  trial dispatch 目标）；工具内部复用既有 lib 驱动（零算子复制）；
- 诚实退化：样本不足/秩亏/不可行 → typed 失败或 skipped_reason（不是
  0 分、不是静默吞）；未评估的指标键直接缺席（不填 0）；
- 有界：下采样确定性（等距 stride + 披露）；网格 ≤128×128；评价不改
  地图、不产新图层。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import numpy as np

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

#: block-CV 支持的方法词表（v1；与 cards.interpolation 候选对齐）。
BLOCK_CV_METHODS = (
    "idw", "nearest_neighbor", "trend_surface", "rbf", "ordinary_kriging",
)

#: CV 评估样本上限（与 protocol.MAX_CV_SAMPLES 一致；工具侧独立兜底）。
_CV_MAX_SAMPLES = 20_000

#: 密度诊断网格每轴上限（≤128×128 单元）。
_DENSITY_GRID_MAX = 128

#: 轮廓系数评估样本上限（确定性 stride 下采样）。
_SILHOUETTE_MAX = 2_000


def _stride_subsample(n: int, cap: int) -> Tuple[np.ndarray, bool]:
    """确定性等距下采样索引（超限时返回 (idx, True)——调用方披露）。"""
    if n <= cap:
        return np.arange(n), False
    idx = np.linspace(0, n - 1, cap).astype(int)
    return np.unique(idx), True


def register_science_eval_primitive_tools(registry: ToolRegistry) -> None:
    """注册三个评价原语（独立模块防并发冲突；调用方 app/tools/__init__）。"""

    # ── 1. 插值分块 CV ──────────────────────────────────────────────────
    @tool(
        registry, name="interpolation_block_cv",
        description=(
            "插值方法空间分块交叉验证：对同一份点要素 + 指定方法/参数，"
            "用确定性空间分块折（防空间泄漏，cv.py splitter）产出 RMSE/MAE/"
            "偏差与逐折证据。方法实验引擎的候选评价真相；也可单独使用。"
            "\n何时用：比较不同插值方法/参数（同一折分配才公平）或需要比 "
            "LOOCV 更贴近空间抽样设计的验证证据。"
            "\n何时不用：样本 <2；或只要插值表面（用对应插值工具）。"
        ),
        tier=2, domains=["statistics"], cost="heavy",
        param_descriptions={
            "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)（Point 几何）",
            "value_field": "用于验证的数值字段名",
            "method": "插值方法: idw / nearest_neighbor / trend_surface / rbf / ordinary_kriging",
            "folds": "折数（2-10，默认 5；自动收缩到样本允许）",
            "scheme": "折分配: spatial_block(默认,防泄漏) / index",
            "power": "IDW 距离幂（仅 idw，默认 2.0）",
            "model": "变异函数模型（仅 ordinary_kriging，默认 auto）",
            "kernel": "RBF 核（仅 rbf，默认 thin_plate_spline）",
            "max_samples": "CV 评估样本上限（超限确定性下采样并披露，默认 20000）",
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="slow",
        memory_class="heavy",
        scale_class="medium",
        tags=("插值", "交叉验证", "空间分块", "rmse", "方法比较"),
        output_semantic_type="stats",
        result_size_policy="bounded",
        crs_semantics="auto_project",
        failure_modes=("invalid_args", "missing_data", "memory"),
    )
    def interpolation_block_cv(
        geojson: Any,
        value_field: str,
        method: str = "idw",
        folds: int = 5,
        scheme: str = "spatial_block",
        power: float = 2.0,
        model: str = "auto",
        kernel: str = "thin_plate_spline",
        max_samples: int = _CV_MAX_SAMPLES,
        max_features: int = 100_000,
    ) -> dict:
        method = str(method)
        if method not in BLOCK_CV_METHODS:
            return {
                "success": False,
                "error": f"unknown_method:{method}",
                "message": f"方法越界（词表: {BLOCK_CV_METHODS}）",
                "skipped_reason": f"unknown_method:{method}",
            }
        if scheme not in ("spatial_block", "index"):
            return {
                "success": False,
                "error": f"unknown_scheme:{scheme}",
                "message": "折分配词表: spatial_block / index",
                "skipped_reason": f"unknown_scheme:{scheme}",
            }
        folds = max(2, min(int(folds), 10))
        try:
            cv_cap = max(10, min(int(max_samples), _CV_MAX_SAMPLES))
        except (TypeError, ValueError):
            cv_cap = _CV_MAX_SAMPLES
        try:
            # max_features 语义真实化（review C-R2-P3）：作为样本上限参与
            # cv_cap（行数预算），与 protocol.max_samples 取更紧者。
            cv_cap = max(10, min(cv_cap, int(max_features)))
        except (TypeError, ValueError):
            pass
        if method == "idw":
            # 生产 IDW 契约对齐（_validate_power 同界）——无效幂 typed 拒绝，
            # 不静默按无效值计算（review C-P2）。
            from app.lib.geo_analysis.interpolation import _validate_power

            try:
                power = float(_validate_power(power))
            except Exception as exc:  # noqa: BLE001 — typed 诚实失败
                return {
                    "success": False,
                    "error": f"invalid_power:{type(exc).__name__}",
                    "message": str(exc)[:240],
                    "skipped_reason": "invalid_power",
                }
        from app.lib.geo_analysis.interpolation import (
            _parse_point_values,
            _pick_metric_crs,
        )

        lonlat, values = _parse_point_values(
            geojson, value_field, purpose="插值分块CV", log_prefix="block_cv")
        n_raw = len(values)
        if n_raw < 2:
            return {
                "success": False,
                "error": "insufficient_samples",
                "message": f"分块 CV 至少需要 2 个样本，got {n_raw}",
                "skipped_reason": f"insufficient_samples:{n_raw}",
            }

        from pyproj import Transformer

        metric_crs = _pick_metric_crs(lonlat)
        transformer = Transformer.from_crs(
            "EPSG:4326", metric_crs, always_xy=True)
        xs, ys = transformer.transform(lonlat[:, 0], lonlat[:, 1])
        pts_metric = np.column_stack(
            [np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)])

        idx, subsampled = _stride_subsample(n_raw, cv_cap)
        pts_metric = pts_metric[idx]
        values = values[idx]
        n = len(values)

        params_applied: Dict[str, Any] = {"method": method}

        if method == "ordinary_kriging":
            from app.lib.geo_analysis.kriging import cross_validate_kriging

            params_applied["model"] = str(model)
            try:
                report = cross_validate_kriging(
                    pts_metric, values, model=str(model), folds=folds,
                    cv_scheme="spatial_block" if scheme == "spatial_block" else "index",
                )
            except Exception as exc:  # noqa: BLE001 — typed 诚实失败
                return {
                    "success": False,
                    "error": f"kriging_cv_failed:{type(exc).__name__}",
                    "message": str(exc)[:240],
                    "skipped_reason": "kriging_cv_failed",
                }
            if report.rmse is None:
                return {
                    "success": True,
                    "method": method,
                    "skipped_reason": "no_metrics",
                    "note": report.note or "克里金交叉验证未产出指标",
                    "n_samples": report.n_samples,
                }
            return _block_cv_payload(
                method=method, params_applied=params_applied,
                rmse=report.rmse, mae=report.mae, bias=report.bias,
                n_samples=report.n_samples, folds_used=report.folds,
                scheme=report.scheme, per_fold=report.per_fold[:8],
                metric_crs=metric_crs, subsampled=subsampled,
                note=report.note, unit=value_field,
            )

        # ── 通用方法：cv.py 方法无关编排（fit/predict 适配器）────────────
        from app.lib.geo_analysis.cv import run_cross_validation

        trend_order = None
        if method == "trend_surface":
            trend_order = _feasible_trend_order(n)
            if trend_order is None:
                return {
                    "success": True,
                    "method": method,
                    "skipped_reason": "no_feasible_trend_order",
                    "note": "1-3 阶项增长守卫均不通过——样本量不足以支撑趋势面",
                    "n_samples": n,
                }
        fit_predict = _cv_adapters(method, power=power, model=model,
                                   kernel=kernel, values_all=values,
                                   coords=pts_metric, trend_order=trend_order)
        if fit_predict is None:
            return {
                "success": False,
                "error": f"no_adapter:{method}",
                "message": "该方法暂无分块 CV 适配器",
                "skipped_reason": f"no_adapter:{method}",
            }
        fit_fn, predict_fn = fit_predict
        if method == "trend_surface" and trend_order is not None:
            params_applied["order"] = trend_order

        try:
            report = run_cross_validation(
                pts_metric, values, fit_fn, predict_fn,
                scheme=scheme, folds=folds,
                min_samples=2,
            )
        except Exception as exc:  # noqa: BLE001 — typed 诚实失败
            return {
                "success": False,
                "error": f"cv_failed:{type(exc).__name__}",
                "message": str(exc)[:240],
                "skipped_reason": "cv_failed",
            }
        if report.rmse is None:
            return {
                "success": True,
                "method": method,
                "skipped_reason": "no_metrics",
                "note": report.note or "交叉验证未产出指标",
                "n_samples": n,
            }
        return _block_cv_payload(
            method=method, params_applied=params_applied,
            rmse=report.rmse, mae=report.mae, bias=report.bias,
            n_samples=report.n_samples, folds_used=report.folds_used,
            scheme=report.scheme, per_fold=report.per_fold[:8],
            metric_crs=metric_crs, subsampled=subsampled,
            note=report.note, unit=value_field,
            leakage_check=report.leakage_check,
            fold_failures=report.fold_failures,
            duplicate_crossfold_pairs=report.duplicate_crossfold_pairs,
            calibration=report.calibration,
        )

    # ── 2. 密度守恒/边界诊断 ────────────────────────────────────────────
    @tool(
        registry, name="density_edge_diagnostic",
        description=(
            "定量密度诊断：对点要素集拟合 KDE（fixed/adaptive 带宽，米），"
            "在包围盒+带宽边距网格上积分密度质量，对照观测点数给出质量守恒"
            "误差与边界带质量占比——边界截断效应的规模证据。"
            "\n何时用：比较密度带宽/方法候选（守恒口径同一网格才可比）；"
            "解释为什么视觉热力不能替代定量密度结论。"
            "\n何时不用：只需要可视化热力（用 heatmap/热力工具——本工具不产图）。"
        ),
        tier=2, domains=["analysis"], cost="medium",
        param_descriptions={
            "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)",
            "method": "kde_fixed(默认) / kde_adaptive",
            "bandwidth_m": "分析带宽（米；fixed=核标准差，adaptive=种子带宽）",
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=("密度", "kde", "守恒", "边界效应", "诊断"),
        output_semantic_type="stats",
        result_size_policy="bounded",
        crs_semantics="auto_project",
        failure_modes=("invalid_args", "missing_data"),
    )
    def density_edge_diagnostic(
        geojson: Any,
        method: str = "kde_fixed",
        bandwidth_m: float = 1000.0,
        max_features: int = 100_000,
    ) -> dict:
        method = str(method)
        if method not in ("kde_fixed", "kde_adaptive"):
            return {
                "success": False,
                "error": f"unknown_method:{method}",
                "message": "方法词表: kde_fixed / kde_adaptive",
            }
        from app.lib.geo_analysis.context import validate_spatial_input
        from app.lib.geo_analysis._vector import extract_centroids

        try:
            vsi = validate_spatial_input(geojson, purpose="density_edge_diagnostic")
        except Exception as exc:  # noqa: BLE001 — typed 解析失败透传
            return {
                "success": False,
                "error": f"invalid_input:{type(exc).__name__}",
                "message": str(exc)[:240],
            }
        gdf = vsi.gdf[vsi.gdf.geometry.notna()]
        n_raw = len(gdf)
        if n_raw < 3:
            return {
                "success": False,
                "error": "insufficient_samples",
                "message": f"密度诊断至少需要 3 个点，got {n_raw}",
            }
        pts = extract_centroids(gdf)
        try:
            input_cap = max(100, min(int(max_features), 10_000))
        except (TypeError, ValueError):
            input_cap = 10_000
        idx, subsampled = _stride_subsample(n_raw, input_cap)
        pts = pts[idx]

        from app.lib.geo_analysis.density import (
            _cap_kde_points,
            _evaluate_adaptive_kde,
        )

        kde_data, weights, n_total, n_used = _cap_kde_points(pts.T, None)
        bw = float(bandwidth_m)
        if bw <= 0:
            return {
                "success": False,
                "error": "invalid_bandwidth",
                "message": f"bandwidth_m 必须为正（米），got {bandwidth_m}",
            }

        # ── 评估网格：bbox + bw 边距（先建网格，再评估）──────────────────
        minx, miny, maxx, maxy = (
            float(np.min(kde_data[0])), float(np.min(kde_data[1])),
            float(np.max(kde_data[0])), float(np.max(kde_data[1])))
        margin = bw
        span_x = (maxx - minx) + 2 * margin
        span_y = (maxy - miny) + 2 * margin
        # 每轴单元数 ≤ GRID_MAX，且 cell ≥ bw/2（分辨率与带宽同尺度）
        nx = int(np.clip(span_x / max(bw / 2.0, span_x / _DENSITY_GRID_MAX), 4, _DENSITY_GRID_MAX))
        ny = int(np.clip(span_y / max(bw / 2.0, span_y / _DENSITY_GRID_MAX), 4, _DENSITY_GRID_MAX))
        gx = np.linspace(minx - margin, maxx + margin, nx)
        gy = np.linspace(miny - margin, maxy + margin, ny)
        cx, cy = np.meshgrid(gx, gy)
        grid_pts = np.vstack([cx.ravel(), cy.ravel()])
        cell_area = float((gx[1] - gx[0]) * (gy[1] - gy[0]))

        # 带宽语义对齐 density.py #384 契约（isotropic kernel std = bw 米），
        # 但评估走 `_evaluate_adaptive_kde` 的显式逐点带宽路径（恒向量 = 固定
        # 核）——scipy 1.18 的 gaussian_kde.evaluate 会在评估期重算 cho_cov，
        # `_fit_kde` 的 cho_cov 覆盖不再生效（master 既有潜在缺陷，PR 披露；
        # 本工具不依赖该内部状态，显式带宽真实生效）。
        n_fit = kde_data.shape[1]
        w_vec = (np.ones(n_fit, dtype=float) if weights is None
                 else np.asarray(weights, dtype=float))
        w_norm = w_vec / max(float(w_vec.sum()), np.finfo(float).tiny)

        def _direct_eval(h_is: np.ndarray, points: np.ndarray) -> np.ndarray:
            return _evaluate_adaptive_kde(kde_data, h_is, w_norm, points)

        try:
            if method == "kde_adaptive":
                # Abramson 平方根律（density.py `_adaptive_bandwidths` 同规格）：
                # pilot = 固定带宽密度在样本点的值（显式评估，不依赖 scipy 内部状态）
                pilot = _direct_eval(
                    np.full(n_fit, bw), kde_data)
                positive = pilot[pilot > 0]
                floor = (float(positive.min()) if positive.size else 1.0) * 1e-16
                pilot = np.maximum(pilot, floor)
                log_gm = float(np.mean(np.log(pilot)))
                lam = np.power(pilot / float(np.exp(log_gm)), -0.5)
                h_is = bw * lam
                density = _direct_eval(h_is, grid_pts)
                adaptive_facts = {
                    "lambda_min": round(float(lam.min()), 4),
                    "lambda_max": round(float(lam.max()), 4),
                    "scheme": "abramson_sqrt_pilot_fixed_bw",
                }
            else:
                density = _direct_eval(
                    np.full(n_fit, bw), grid_pts)
                adaptive_facts = {}
        except Exception as exc:  # noqa: BLE001 — 数值失败 typed 透传
            return {
                "success": False,
                "error": f"kde_eval_failed:{type(exc).__name__}",
                "message": str(exc)[:240],
            }

        # 物理口径：gaussian_kde 归一化为概率密度（全域积分=1）；恢复的
        # 「点数质量」= n_used × Σd×cell_area（网格离散 + 远尾截断的
        # 数值守恒检查）。边界效应由外溢占比单独披露（见下）。
        total_mass_unit = float(np.sum(density) * cell_area)
        integral_mass = total_mass_unit * n_used
        mass_error_ratio = abs(integral_mass - n_used) / max(n_used, 1)

        # 边界外溢：质量落在原始 bbox 之外的份额——消费方只在 bbox 内积分
        # 时会低估的量（截断效应的直接规模）。纯密度份额比（分子分母同
        # 量纲，cell_area 相消）。
        density_total = float(np.sum(density))
        outside_mask = (
            (grid_pts[0] < minx - 1e-9) | (grid_pts[0] > maxx + 1e-9)
            | (grid_pts[1] < miny - 1e-9) | (grid_pts[1] > maxy + 1e-9))
        edge_mass_share = (
            float(np.sum(density[outside_mask]) / density_total)
            if density_total > 0 else None)
        # 内边界带（bbox 内距边 < bw）：边界影响的空间尺度披露
        inner_edge = (
            ((grid_pts[0] >= minx - 1e-9) & (grid_pts[0] < minx + bw))
            | ((grid_pts[0] > maxx - bw) & (grid_pts[0] <= maxx + 1e-9))
            | ((grid_pts[1] >= miny - 1e-9) & (grid_pts[1] < miny + bw))
            | ((grid_pts[1] > maxy - bw) & (grid_pts[1] <= maxy + 1e-9)))
        inner_band_share = (
            float(np.sum(density[inner_edge]) / density_total)
            if density_total > 0 else None)

        summary = (
            f"密度诊断（{method}, bw={bw:.0f}m）：积分质量 {integral_mass:.2f} "
            f"vs 点数 {n_used}（守恒误差 {mass_error_ratio:.1%}）；"
            f"边界带质量占比 {_fmt_ratio(edge_mass_share)}。"
            "视觉热力不能替代本定量结论。")

        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.scientific_evidence import build_evidence

        descriptor = get_algorithm_registry().get("spatial.kde.surface")
        payload: Dict[str, Any] = {
            "success": True,
            "method": method,
            "bandwidth_effective": round(bw, 2),
            "n_points": int(n_used),
            "n_input": int(n_raw),
            "integral_mass": round(integral_mass, 4),
            "mass_error_ratio": round(mass_error_ratio, 6),
            "edge_mass_share": (
                round(edge_mass_share, 6) if edge_mass_share is not None else None),
            "grid_facts": {"nx": nx, "ny": ny, "cell_m": round(float(gx[1] - gx[0]), 2),
                           "cell_area_m2": round(cell_area, 2)},
            "metric_crs": vsi.metric_crs,
            "subsampled": subsampled,
            "adaptive": adaptive_facts,
            "edge_band_mass_share": (
                round(inner_band_share, 6) if inner_band_share is not None else None),
            "edge_note": (
                "边界带密度被截断低估——占比高时边界修正（reflected KDE）"
                "或扩大研究区是先决条件，而不是换更好看的渲染"),
            "summary": summary,
        }
        if descriptor is not None:
            payload["scientific_evidence"] = build_evidence(
                descriptor, tool="density_edge_diagnostic",
                parameters_applied={
                    "method": method, "bandwidth_m": float(bandwidth_m)},
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": int(n_used),
                    "crs": vsi.metric_crs,
                },
                transformations=[
                    f"metric projection {vsi.metric_crs}",
                    "gaussian KDE (isotropic bw meters), grid integral",
                ],
                validation=None,
            )
        return payload

    # ── 3. 聚类稳定性 ───────────────────────────────────────────────────
    @tool(
        registry, name="cluster_stability",
        description=(
            "聚类稳定性量度：K-Means 跨 seed / DBSCAN 跨 bootstrap 子采样的"
            "调整兰德指数（ARI）一致性 + 非噪声样本轮廓系数 + 噪声占比。"
            "方法实验引擎的聚类候选评价真相。"
            "\n何时用：比较不同 k/eps 参数的聚类质量与稳健性（同一数据、"
            "同一次数协议才可比）。"
            "\n何时不用：只要分簇标签图层（用 spatial_cluster——本工具不产图层）。"
        ),
        tier=2, domains=["analysis"], cost="medium",
        param_descriptions={
            "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)",
            "method": "kmeans(默认) / dbscan",
            "k": "K-Means 簇数（默认 5）",
            "eps_m": "DBSCAN 邻域半径（米，默认 1000）",
            "min_samples": "DBSCAN 核心点阈值（默认 5）",
            "runs": "重复次数（3-10，默认 5）",
            "seed": "基础随机种子（默认 42；逐 run 递增）",
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=("聚类", "稳定性", "ari", "轮廓系数", "参数比较"),
        output_semantic_type="stats",
        result_size_policy="bounded",
        crs_semantics="auto_project",
        failure_modes=("invalid_args", "missing_data"),
    )
    def cluster_stability(
        geojson: Any,
        method: str = "kmeans",
        k: int = 5,
        eps_m: float = 1000.0,
        min_samples: int = 5,
        runs: int = 5,
        seed: int = 42,
        value_field: str = "",
        max_features: int = 100_000,
    ) -> dict:
        method = str(method)
        if method not in ("kmeans", "dbscan"):
            return {
                "success": False,
                "error": f"unknown_method:{method}",
                "message": "方法词表: kmeans / dbscan",
            }
        if value_field:
            return {
                "success": False,
                "error": "value_field_unsupported_v1",
                "message": (
                    "v1 稳定性协议只支持纯空间维聚类（坐标）；值感知聚类的"
                    "量纲混合语义尚无稳定性契约——诚实拒绝，不做无依据比较"),
            }
        runs = max(3, min(int(runs), 10))
        from app.lib.geo_analysis.context import validate_spatial_input
        from app.lib.geo_analysis._vector import extract_centroids

        try:
            vsi = validate_spatial_input(geojson, purpose="cluster_stability")
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "error": f"invalid_input:{type(exc).__name__}",
                "message": str(exc)[:240],
            }
        pts = extract_centroids(vsi.gdf)
        try:
            input_cap = max(100, min(int(max_features), 50_000))
        except (TypeError, ValueError):
            input_cap = 50_000
        cluster_subsampled = False
        if len(pts) > input_cap:
            sub_idx, _ = _stride_subsample(len(pts), input_cap)
            pts = pts[sub_idx]
            cluster_subsampled = True
        n = len(pts)
        if n < 6:
            return {
                "success": False,
                "error": "insufficient_samples",
                "message": f"稳定性协议至少需要 6 个样本，got {n}",
            }

        from sklearn.cluster import DBSCAN, KMeans

        params_applied: Dict[str, Any] = {"method": method}
        labels_runs: list[np.ndarray] = []
        note_parts: list[str] = []
        k_found = 0
        noise_share: Optional[float] = None

        if method == "kmeans":
            nk = max(2, min(int(k), n - 1))
            params_applied["k"] = nk
            for r in range(runs):
                model = KMeans(n_clusters=nk, random_state=int(seed) + r,
                               n_init=10)
                labels_runs.append(model.fit_predict(pts))
            base_labels = labels_runs[0]
            k_found = int(len(np.unique(base_labels)))
        else:
            if eps_m <= 0 or min_samples <= 0:
                return {
                    "success": False,
                    "error": "invalid_params",
                    "message": "DBSCAN 要求 eps_m>0 且 min_samples>0",
                }
            params_applied.update({"eps_m": float(eps_m),
                                   "min_samples": int(min_samples)})
            # 全量基线（噪声/轮廓口径）+ 逐 run bootstrap 子采样（确定性）
            full = DBSCAN(eps=float(eps_m), min_samples=int(min_samples))
            base_labels = full.fit_predict(pts)
            n_sub = max(6, int(np.floor(n * 0.8)))
            for r in range(runs):
                rng = np.random.default_rng(int(seed) + r)
                idx = np.sort(rng.choice(n, size=n_sub, replace=False))
                sub = DBSCAN(eps=float(eps_m), min_samples=int(min_samples))
                sub_labels = sub.fit_predict(pts[idx])
                # 子采样点的基线标签 vs 重聚类标签（同一批点的扰动一致性）
                labels_runs.append((idx, base_labels[idx], sub_labels))
            noise_share = float(np.mean(base_labels == -1))
            k_found = int(len(np.unique(base_labels))
                          - (1 if -1 in base_labels else 0))
            note_parts.append("DBSCAN 确定性：稳定性=bootstrap 子采样一致性")

        # ARI（kmeans: 跨 seed 全对；dbscan: 逐 run 对基线）
        from sklearn.metrics import adjusted_rand_score, silhouette_score

        aris: list[float] = []
        if method == "kmeans":
            for i in range(len(labels_runs)):
                for j in range(i + 1, len(labels_runs)):
                    aris.append(float(adjusted_rand_score(
                        labels_runs[i], labels_runs[j])))
        else:
            for idx, base_sub, sub_labels in labels_runs:
                aris.append(float(adjusted_rand_score(base_sub, sub_labels)))
        ari_mean = float(np.mean(aris)) if aris else None
        ari_std = float(np.std(aris)) if aris else None

        # 轮廓系数（非噪声、欧氏、确定性下采样 ≤2000）
        sil_mask = base_labels != -1
        silhouette_mean = None
        n_clusters_found = int(len(np.unique(base_labels)) - (1 if -1 in base_labels else 0))
        if method == "dbscan" and n_clusters_found >= 2 and int(sil_mask.sum()) >= max(n_clusters_found + 1, 3):
            sil_idx = np.nonzero(sil_mask)[0]
            sil_idx, _ = _stride_subsample(len(sil_idx), _SILHOUETTE_MAX)
            sil_pts = pts[sil_idx]
            sil_labels = base_labels[sil_idx]
            if len(np.unique(sil_labels)) >= 2:
                try:
                    silhouette_mean = float(silhouette_score(
                        sil_pts, sil_labels, metric="euclidean"))
                except Exception as exc:  # noqa: BLE001 — 退化输入诚实披露
                    note_parts.append(f"silhouette_failed:{type(exc).__name__}")
        elif method == "kmeans":
            sil_idx, _ = _stride_subsample(n, _SILHOUETTE_MAX)
            if len(np.unique(base_labels[sil_idx])) >= 2:
                try:
                    silhouette_mean = float(silhouette_score(
                        pts[sil_idx], base_labels[sil_idx], metric="euclidean"))
                except Exception as exc:  # noqa: BLE001
                    note_parts.append(f"silhouette_failed:{type(exc).__name__}")

        payload: Dict[str, Any] = {
            "success": True,
            "method": method,
            "params_applied": params_applied,
            "n_samples": int(n),
            "runs_used": runs,
            "ari_mean": round(ari_mean, 6) if ari_mean is not None else None,
            "ari_std": round(ari_std, 6) if ari_std is not None else None,
            "silhouette_mean": (
                round(silhouette_mean, 6) if silhouette_mean is not None else None),
            "noise_share": round(noise_share, 6) if noise_share is not None else None,
            "k_found": k_found,
            "metric_crs": vsi.metric_crs,
            "subsampled": cluster_subsampled,
            "note": "；".join(note_parts),
            "summary": (
                f"{method.upper()} 稳定性（{runs} 次）：ARI={_fmt(ari_mean)}±{_fmt(ari_std)}，"
                f"轮廓系数={_fmt(silhouette_mean)}，簇数={k_found}"
                + (f"，噪声占比={_fmt(noise_share)}" if noise_share is not None else "")),
        }

        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.scientific_evidence import build_evidence

        algo_id = ("spatial.cluster.kmeans" if method == "kmeans"
                   else "spatial.cluster.dbscan")
        descriptor = get_algorithm_registry().get(algo_id)
        if descriptor is not None:
            payload["scientific_evidence"] = build_evidence(
                descriptor, tool="cluster_stability",
                parameters_applied=dict(params_applied),
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": int(n),
                    "crs": vsi.metric_crs,
                },
                transformations=[f"metric projection {vsi.metric_crs}",
                                 f"stability protocol runs={runs}"],
                validation=None,
            )
        return payload


def _cv_adapters(
    method: str, *, power: float, model: str, kernel: str,
    values_all: np.ndarray, coords: np.ndarray,
    trend_order: Optional[int] = None,
):
    """方法 → (fit_fn, predict_fn)。

    cv.py 回调契约：``fit_fn(train_mask, values[train_mask])``；
    ``predict_fn(model, train_mask, test_mask)``——train/test 是**布尔
    掩码**，坐标由适配器闭包捕获（模型只携带训练子集——测试样本绝不进
    自己的条件集）。
    """
    if method == "idw":
        from scipy.spatial import cKDTree

        def fit_idw(train_mask, train_values):
            return {"xy": coords[train_mask], "vals": train_values}

        def predict_idw(model, train_mask, test_mask):
            pts = model["xy"]
            vals = model["vals"]
            targets = coords[test_mask]
            k = int(min(5, len(pts)))
            tree = cKDTree(pts)
            dist, idx = tree.query(targets, k=k)
            if k == 1:
                dist = np.atleast_2d(dist)
                idx = np.atleast_2d(idx)
            hit = dist < 1e-9
            has_exact = hit.any(axis=1)
            pred = np.empty(len(targets), dtype=float)
            if has_exact.any():
                first = np.argmax(hit, axis=1)
                rows = np.nonzero(has_exact)[0]
                pred[rows] = vals[idx[rows, first[rows]]]
            non_exact = ~has_exact
            if non_exact.any():
                w = 1.0 / np.maximum(dist[non_exact] ** float(power), 1e-30)
                pred[non_exact] = (w * vals[idx[non_exact]]).sum(axis=1) / w.sum(axis=1)
            return pred, None

        return fit_idw, predict_idw

    if method == "nearest_neighbor":
        from scipy.spatial import cKDTree

        def fit_nn(train_mask, train_values):
            return {"xy": coords[train_mask], "vals": train_values}

        def predict_nn(model, train_mask, test_mask):
            tree = cKDTree(model["xy"])
            dist, idx = tree.query(coords[test_mask], k=1)
            return np.asarray(model["vals"][idx], dtype=float), None

        return fit_nn, predict_nn

    if method == "trend_surface":
        from app.lib.geo_analysis.trend_surface import trend_predict

        # order 由工具级一次确定并经 fit 闭包写进模型（逐折同阶=同一方法
        # 语义；折内样本不足以支撑该阶时 trend_predict typed 抛错 →
        # 诚实 fold_failures——绝不静默降阶，review C-R2）。
        fixed_order = trend_order or _feasible_trend_order(len(values_all)) or 1

        def fit_trend(train_mask, train_values):
            return {"xy": coords[train_mask], "vals": train_values,
                    "order": fixed_order}

        def predict_trend(model, train_mask, test_mask):
            res = trend_predict(
                model["xy"], model["vals"], coords[test_mask],
                order=model["order"])
            return np.asarray(res["predictions"], dtype=float), None

        return fit_trend, predict_trend

    if method == "rbf":
        from app.lib.geo_analysis.rbf_interpolation import rbf_predict

        def fit_rbf(train_mask, train_values):
            return {"xy": coords[train_mask], "vals": train_values}

        def predict_rbf(model, train_mask, test_mask):
            pred = rbf_predict(
                model["xy"], model["vals"], coords[test_mask],
                kernel=str(kernel), smoothing=0.0)
            return np.asarray(pred, dtype=float), None

        return fit_rbf, predict_rbf

    return None


def _feasible_trend_order(n: int) -> Optional[int]:
    """最高可行趋势阶数（复用 trend_surface 守卫；不可行 → None）。"""
    from app.lib.geo_analysis.trend_surface import _check_order_feasible
    from app.lib.gis.scientific_errors import InsufficientSamples

    for order in (3, 2, 1):
        try:
            _check_order_feasible(order, n)
            return order
        except InsufficientSamples:
            continue
    return None


def _block_cv_payload(
    *, method: str, params_applied: Dict[str, Any], rmse: float, mae: float,
    bias: float, n_samples: int, folds_used: int, scheme: str,
    per_fold: list, metric_crs: str, subsampled: bool, note: str = "",
    unit: str = "", leakage_check: Optional[dict] = None,
    fold_failures: int = 0,
    duplicate_crossfold_pairs: int = 0,
    calibration: Optional[dict] = None,
) -> dict:
    """分块 CV 统一载荷（指标键与 runner 适配器契约一致）。"""
    payload: Dict[str, Any] = {
        "success": True,
        "method": method,
        "params_applied": params_applied,
        "rmse": round(float(rmse), 6),
        "mae": round(float(mae), 6),
        "bias": round(float(bias), 6),
        "n_samples": int(n_samples),
        "folds_used": int(folds_used),
        "scheme": str(scheme),
        "metric_crs": metric_crs,
        "value_unit": unit or "value_unit",
        "note": note,
    }
    if subsampled:
        payload["subsampled"] = True
        payload["note"] = (
            (note + "；" if note else "")
            + f"样本超上限确定性下采样（≤{_CV_MAX_SAMPLES}）——指标为下采样口径")
    if leakage_check is not None:
        payload["leakage_check"] = leakage_check
    if fold_failures:
        payload["fold_failures"] = int(fold_failures)
    if duplicate_crossfold_pairs:
        payload["duplicate_crossfold_pairs"] = int(duplicate_crossfold_pairs)
    if calibration:
        payload["uncertainty_calibration"] = calibration
    if per_fold:
        payload["per_fold_head"] = [
            {k: v for k, v in row.items() if isinstance(v, (int, float, str))}
            for row in per_fold[:6]]
    payload["summary"] = (
        f"{method} 分块CV（{scheme}, {folds_used} 折, n={n_samples}）："
        f"RMSE={payload['rmse']}, MAE={payload['mae']}, bias={payload['bias']}"
        + (f"（单位: {unit}）" if unit else ""))
    return payload


def _fmt(v: Any) -> str:
    return f"{v:.3f}" if isinstance(v, (int, float)) else "n/a"


def _fmt_ratio(v: Any) -> str:
    return f"{v:.1%}" if isinstance(v, (int, float)) else "n/a"
