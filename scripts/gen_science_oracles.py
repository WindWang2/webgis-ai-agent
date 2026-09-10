#!/usr/bin/env python3
"""Numerical Oracle Corpus 生成器（Foundation V3 · Goal K · 决策 D9）。

用法（工作树根目录）::

    python scripts/gen_science_oracles.py            # 生成/刷新全部域
    python scripts/gen_science_oracles.py point_pattern spectral   # 只刷指定域

设计：
- 每个域一个 ``build_<domain>()`` 工厂，返回 case 列表
  （deterministic fixture + 显式期望值）。期望值优先来自**独立参考
  公式**（numpy/scipy 解析解、手算黄金值），实现回归锚用
  ``anchor=True`` 标注（锁定当前已验证行为，防漂移）。
- ``expect.select`` 是提取迷你语言：``L.3`` 点路径、
  ``mean:array`` 聚合前缀（见 tests/science_oracles/__init__.py）。
- 输出 ``tests/science_oracles/data/<domain>.json``（确定性字节序）。
- 回放侧（tests/science_oracles/test_oracle_replay.py）零重算。
"""
from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

DATA_DIR = ROOT / "tests" / "science_oracles" / "data"


def case(case_id: str, target: str, value: Any, *,
         kind: str = "allclose", args: List[Any] | None = None,
         kwargs: Dict[str, Any] | None = None,
         select: str = "", rtol: float = 1e-7, atol: float = 1e-6,
         anchor: bool = False, note: str = "") -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "id": case_id,
        "target": target,
        "args": args or [],
        "kwargs": kwargs or {},
        "expect": {"kind": kind, "value": value},
    }
    if select:
        entry["expect"]["select"] = select
    if kind == "allclose":
        entry["expect"]["rtol"] = rtol
        entry["expect"]["atol"] = atol
    if anchor:
        entry["anchor"] = True
    if note:
        entry["note"] = note
    return entry


def error_case(case_id: str, target: str, code: str,
               args: List[Any] | None = None,
               kwargs: Dict[str, Any] | None = None,
               note: str = "") -> Dict[str, Any]:
    entry = {
        "id": case_id,
        "target": target,
        "args": args or [],
        "kwargs": kwargs or {},
        "expect": {"kind": "error", "value": None, "code": code},
    }
    if note:
        entry["note"] = note
    return entry


def r9(x: float) -> float:
    """round to ~9 significant decimals for stable JSON round-trips."""
    v = float(x)
    if v == 0 or not np.isfinite(v):
        return v
    return float(round(v, 9 - int(np.floor(np.log10(abs(v)))) + 1))


def _id_tag(v: Any) -> str:
    """Stable case-id tag for arbitrary guard values."""
    return str(v).replace("'", "").replace('"', "").replace(" ", "") \
        .replace("[", "").replace("]", "").replace("<", "").replace(">", "") \
        .replace("(", "").replace(")", "").replace(",", "_").replace(".", "p")


# ── fixtures（全部固定种子）──────────────────────────────────────────

def _fixture_points(seed: int = 12, n: int = 60, extent: float = 1000.0):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0, extent, (n, 2))
    return xy


def _fixture_clustered_points(seed: int = 13, n_per: int = 40):
    rng = np.random.default_rng(seed)
    centers = np.array([[250.0, 250.0], [750.0, 750.0]])
    xy = np.vstack([c + rng.normal(0, 50, (n_per, 2)) for c in centers])
    return xy


# ── 域：point_pattern ────────────────────────────────────────────────

def build_point_pattern() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    from app.lib.geo_analysis import point_pattern as pp

    xy = _fixture_points()
    clustered = _fixture_clustered_points()
    times = np.linspace(0, 10_000, len(xy)).tolist()
    xy_l = xy.tolist()

    # --- Ripley K / L：实现回归锚（固定输入 → 固定曲线）
    res_k = pp.ripley_k(xy, n_steps=6, max_distance_ratio=0.25)
    for i in range(6):
        cases.append(case(
            f"ripley_k_L_step{i}", "app.lib.geo_analysis.point_pattern:ripley_k",
            res_k["L"][i], args=[xy_l],
            kwargs={"n_steps": 6, "max_distance_ratio": 0.25},
            select=f"L.{i}", anchor=True))

    # --- quadrat χ²：numpy 参考公式独立复算（histogram2d 按数据 bbox 分箱、
    #     ddof=1 样本方差 / 每样方期望 —— 与实现披露约定一致）
    res_q = pp.quadrat_test(xy, grid_rows=4, grid_cols=4)
    h, _, _ = np.histogram2d(xy[:, 0], xy[:, 1], bins=(4, 4))
    counts_ref = h.T.ravel()
    vmr = counts_ref.var(ddof=1) / (len(xy) / 16)
    cases.append(case("quadrat_vmr_reference",
                      "app.lib.geo_analysis.point_pattern:quadrat_test",
                      r9(vmr), args=[xy_l],
                      kwargs={"grid_rows": 4, "grid_cols": 4},
                      select="variance_mean_ratio", rtol=1e-7,
                      note="VMR 独立复算"))
    cases.append(case("quadrat_chi2_anchor",
                      "app.lib.geo_analysis.point_pattern:quadrat_test",
                      r9(float(res_q["chi2"])), args=[xy_l],
                      kwargs={"grid_rows": 4, "grid_cols": 4},
                      select="chi2", anchor=True))

    # --- G/F/J 三种边缘校正（固定窗 + 回归锚）
    win = [-200.0, -200.0, 1200.0, 1200.0]
    for corr in ("none", "border", "isotropic"):
        res_gfj = pp.g_f_j_functions(xy, n_steps=6, window=win,
                                     edge_correction=corr)
        cases.append(case(f"gfj_G_last_{corr}",
                          "app.lib.geo_analysis.point_pattern:g_f_j_functions",
                          res_gfj["G"][5], args=[xy_l],
                          kwargs={"n_steps": 6, "window": win,
                                  "edge_correction": corr},
                          select="G.5", anchor=True))
        cases.append(case(f"gfj_F_last_{corr}",
                          "app.lib.geo_analysis.point_pattern:g_f_j_functions",
                          res_gfj["F"][5], args=[xy_l],
                          kwargs={"n_steps": 6, "window": win,
                                  "edge_correction": corr},
                          select="F.5", anchor=True))

    # --- pcf / cross-K / g12 锚
    res_pcf = pp.pcf(xy, n_steps=6)
    cases.append(case("pcf_g_last",
                      "app.lib.geo_analysis.point_pattern:pcf",
                      res_pcf["g"][5], args=[xy_l],
                      kwargs={"n_steps": 6}, select="g.5", anchor=True))

    types = ["a" if i % 2 == 0 else "b" for i in range(len(xy))]
    res_ck = pp.cross_k(xy, types, n_steps=6, permutations=0)
    cases.append(case("cross_k_step0",
                      "app.lib.geo_analysis.point_pattern:cross_k",
                      res_ck["K12"][0], args=[xy_l, types],
                      kwargs={"n_steps": 6, "permutations": 0},
                      select="K12.0", anchor=True))
    res_g12 = pp.cross_pair_correlation(xy, types, n_steps=6, permutations=0)
    cases.append(case("cross_pcf_g12_step0",
                      "app.lib.geo_analysis.point_pattern:cross_pair_correlation",
                      res_g12["g12"][0], args=[xy_l, types],
                      kwargs={"n_steps": 6, "permutations": 0},
                      select="g12.0", anchor=True))

    # --- Knox：独立复算观测计数 / 解析期望 / 比值锚
    from scipy.spatial import cKDTree
    pairs = cKDTree(xy).query_pairs(150.0, output_type="ndarray")
    ta = np.asarray(times)
    dt = np.abs(ta[pairs[:, 0]] - ta[pairs[:, 1]])
    res_knox = pp.knox_test(xy, ta, critical_distance=150.0,
                            critical_time=600.0, permutations=0)
    cases.append(case("knox_observed_reference",
                      "app.lib.geo_analysis.point_pattern:knox_test",
                      int(np.sum(dt <= 600.0)),
                      args=[xy_l, times],
                      kwargs={"critical_distance": 150.0,
                              "critical_time": 600.0, "permutations": 0},
                      select="observed", kind="exact",
                      note="联合对数独立复算"))
    cases.append(case("knox_expected_reference",
                      "app.lib.geo_analysis.point_pattern:knox_test",
                      r9(2.0 * len(pairs) * float(res_knox["n_temporal_pairs"])
                         / (len(xy) * (len(xy) - 1))),
                      args=[xy_l, times],
                      kwargs={"critical_distance": 150.0,
                              "critical_time": 600.0, "permutations": 0},
                      select="expected", rtol=1e-7,
                      note="E=2ST/(n(n-1)) 独立复算"))

    # --- 时空 K：参考矩阵角点解析 + sup 锚
    res_stk = pp.space_time_k(xy, ta, n_steps_r=6, n_steps_t=6,
                              max_distance_ratio=0.25, permutations=0)
    r0, t0 = res_stk["r"][0], res_stk["t"][0]
    cases.append(case("stk_reference_corner_analytic",
                      "app.lib.geo_analysis.point_pattern:space_time_k",
                      r9(np.pi * r0 * r0 * 2 * t0),
                      args=[xy_l, times],
                      kwargs={"n_steps_r": 6, "n_steps_t": 6,
                              "max_distance_ratio": 0.25, "permutations": 0},
                      select="reference_independent.0.0", rtol=1e-6,
                      note="独立参考 πr²·2t 解析值"))
    cases.append(case("stk_sup_anchor",
                      "app.lib.geo_analysis.point_pattern:space_time_k",
                      float(res_stk["sup_exceedance"]),
                      args=[xy_l, times],
                      kwargs={"n_steps_r": 6, "n_steps_t": 6,
                              "max_distance_ratio": 0.25, "permutations": 0},
                      select="sup_exceedance", anchor=True))

    # --- Mantel：np.corrcoef 独立复算
    iu, ju = np.triu_indices(len(xy), k=1)
    ds = np.linalg.norm(xy[iu] - xy[ju], axis=1)
    dtm = np.abs(ta[iu] - ta[ju])
    r_m = float(np.corrcoef(ds, dtm)[0, 1])
    cases.append(case("mantel_r_reference",
                      "app.lib.geo_analysis.point_pattern:mantel_test",
                      r9(r_m), args=[xy_l, times],
                      kwargs={"permutations": 0},
                      select="mantel_r", rtol=1e-7,
                      note="np.corrcoef 独立复算"))

    # --- 聚集 fixture 的 K 曲线（回归锚 ×3）
    res_kc = pp.ripley_k(clustered, n_steps=5, max_distance_ratio=0.2)
    for i in (0, 2, 4):
        cases.append(case(f"ripley_k_clustered_step{i}",
                          "app.lib.geo_analysis.point_pattern:ripley_k",
                          res_kc["L"][i], args=[clustered.tolist()],
                          kwargs={"n_steps": 5, "max_distance_ratio": 0.2},
                          select=f"L.{i}", anchor=True))

    # --- 类型化错误族（语义回归）
    cases.append(error_case(
        "gfj_too_few_points",
        "app.lib.geo_analysis.point_pattern:g_f_j_functions",
        "INSUFFICIENT_SAMPLES", args=[xy[:5].tolist()]))
    cases.append(error_case(
        "cross_k_not_binary",
        "app.lib.geo_analysis.point_pattern:cross_k",
        "UNSUPPORTED_METHOD",
        args=[xy_l, ["a"] * len(xy)], kwargs={"permutations": 0}))
    cases.append(error_case(
        "stk_identical_times",
        "app.lib.geo_analysis.point_pattern:space_time_k",
        "DEGENERATE_DATA",
        args=[xy_l, [5.0] * len(xy)]))
    cases.append(error_case(
        "mantel_too_large",
        "app.lib.geo_analysis.point_pattern:mantel_test",
        "RESOURCE_SCALE_MISMATCH",
        args=[np.zeros((2001, 2)).tolist(), list(range(2001))]))

    return cases


# ── 域：spectral（光谱指数族 · 手解析黄金）───────────────────────────

def build_spectral() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    from app.lib.geo_analysis import spectral as sp

    rng = np.random.default_rng(21)
    red = rng.uniform(0.02, 0.6, (6, 6))
    nir = rng.uniform(0.05, 0.9, (6, 6))
    green = rng.uniform(0.02, 0.5, (6, 6))
    swir1 = rng.uniform(0.03, 0.7, (6, 6))

    # NDVI 均值：numpy 公式独立复算（实现只出 array，无 mean 键 → 聚合提取）
    s = nir + red
    with np.errstate(invalid="ignore", divide="ignore"):
        expect_ndvi = (nir - red) / s
    res_ndvi = sp.compute_spectral_index({"red": red, "nir": nir}, "ndvi")
    cases.append(case("ndvi_mean_reference",
                      "app.lib.geo_analysis.spectral:compute_spectral_index",
                      r9(float(np.nanmean(expect_ndvi))),
                      args=[{"red": red.tolist(), "nir": nir.tolist()}, "ndvi"],
                      select="mean:array", rtol=1e-7,
                      note="numpy 公式独立复算"))
    cases.append(case("ndvi_valid_fraction_reference",
                      "app.lib.geo_analysis.spectral:compute_spectral_index",
                      r9(float(np.isfinite(expect_ndvi).mean())),
                      args=[{"red": red.tolist(), "nir": nir.tolist()}, "ndvi"],
                      select="valid_pixel_fraction", rtol=1e-7,
                      note="有限像元率独立复算"))

    # 三个指数的首像元锚
    for idx_name, bands in (("ndvi", {"red": red, "nir": nir}),
                            ("gndvi", {"green": green, "nir": nir}),
                            ("ndbi", {"swir1": swir1, "nir": nir})):
        r = sp.compute_spectral_index(bands, idx_name)
        cases.append(case(f"{idx_name}_pixel00_anchor",
                          "app.lib.geo_analysis.spectral:compute_spectral_index",
                          r9(float(r["array"][0][0])),
                          args=[{k: v.tolist() for k, v in bands.items()},
                                idx_name],
                          select="array.0.0", anchor=True))

    # 零分母 → 全 NaN（诚实值语义：mean:array → null）
    cases.append(case("ndvi_zero_denominator_all_nan",
                      "app.lib.geo_analysis.spectral:compute_spectral_index",
                      None,
                      args=[{"red": np.zeros((2, 2)).tolist(),
                             "nir": np.zeros((2, 2)).tolist()}, "ndvi"],
                      select="mean:array", kind="exact",
                      note="全零输入 → 全 NaN → 聚合为 null"))

    # 缺角色 → 类型化错误
    cases.append(error_case(
        "ndvi_missing_role",
        "app.lib.geo_analysis.spectral:compute_spectral_index",
        "UNSUPPORTED_BAND_SEMANTICS",
        args=[{"red": red.tolist()}, "ndvi"]))

    return cases


# ── 域：crs_units（单位/度-米语义）──────────────────────────────────

def build_crs_units() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    for crs, want in (("EPSG:4326", "geographic"),
                      ("EPSG:3857", "projected"),
                      ("EPSG:32650", "projected_local_metric"),
                      ("EPSG:4490", "geographic")):
        cases.append(case(f"classify_{crs.replace(':', '').lower()}",
                          "app.lib.gis.crs_safety:classify_crs",
                          want, args=[crs], kind="exact"))

    # 度输入在点格局层被拒（度≠米的语义回归）
    xy_deg = np.column_stack([np.linspace(116.0, 117.0, 30),
                              np.linspace(39.0, 40.0, 30)])
    cases.append(error_case(
        "ripley_rejects_degrees",
        "app.lib.geo_analysis.point_pattern:ripley_k",
        "INVALID_CRS", args=[xy_deg.tolist()],
        kwargs={"crs": "EPSG:4326"}))
    return cases


# ── 共享 fixture 工具（V3 扩展域）─────────────────────────────────────
#
# 运行器契约（tests/science_oracles/__init__.py，冻结）：
# - args/kwargs 只能是 JSON 值（tolist 后的数组 / dict / 标量）；
# - expect.select 只能下钻 dict / list / ndarray，聚合前缀 mean:/max:/min:/sum:；
# - 返回 GeoAnalysisResult / tuple / pydantic 的实现面**无法**做标量选择 ——
#   这些面只用 error-kind case（类型化错误）覆盖，详见最终报告。

def _metric_points(seed: int = 31, n: int = 24, extent: float = 1000.0,
                   jitter: float = 0.0):
    """Metric CRS (EPSG:32650) 坐标；jitter>0 时在规则网格上加抖动避免平手。"""
    rng = np.random.default_rng(seed)
    if jitter > 0:
        side = int(np.ceil(np.sqrt(n)))
        xs = np.tile(np.arange(side) * (extent / side), side)[:n]
        ys = np.repeat(np.arange(side) * (extent / side), side)[:n]
        base = np.column_stack([xs, ys]).astype(float)
        return base + rng.uniform(-jitter, jitter, base.shape)
    return rng.uniform(0.0, extent, (n, 2))


def _points_fc(xy, fields: Dict[str, Any], crs: str = "EPSG:32650") -> dict:
    """声明投影 CRS 的点 FeatureCollection（to_utm_gdf 恒等直通）。"""
    feats = []
    keys = list(fields.keys())
    for i, (x, y) in enumerate(xy):
        props = {k: fields[k][i] for k in keys}
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(x), float(y)]},
            "properties": props,
        })
    fc = {"type": "FeatureCollection", "features": feats}
    if crs:
        fc["crs"] = crs
    return fc


def _bh_reference(p):
    """独立 BH-FDR 参考（排序 + 逐步下确界循环，非实现向量化路径）。"""
    p = np.asarray(p, dtype=float)
    n = p.size
    nan = np.isnan(p)
    pc = np.where(nan, 1.0, p)
    order = np.argsort(pc, kind="stable")
    q = np.empty(n)
    prev = 1.0
    for rank in range(n, 0, -1):
        idx = order[rank - 1]
        val = pc[idx] * n / rank
        prev = min(prev, val)
        q[idx] = prev
    q = np.clip(q, 0.0, 1.0)
    q[nan] = 1.0
    return q


def _holm_reference(p):
    p = np.asarray(p, dtype=float)
    n = p.size
    nan = np.isnan(p)
    pc = np.where(nan, 1.0, p)
    order = np.argsort(pc, kind="stable")
    q = np.empty(n)
    prev = 0.0
    for rank in range(1, n + 1):
        idx = order[rank - 1]
        val = pc[idx] * (n - rank + 1)
        prev = max(prev, val)
        q[idx] = prev
    return np.clip(q, 0.0, 1.0)


# ── 域：statistics_global（全局空间自相关族 + 推断基元）──────────────
#
# 运行器限制：moran_i/geary_c/general_g/join_count/bivariate_moran 的
# narrated 入口返回 GeoAnalysisResult —— 标量期望无法 select。数值 case
# 覆盖可回放的纯 numpy 面（auto-band 规则、ST 距离矩阵、置换推断基元、
# 权重构建器），语义回归用 narrated 入口的类型化错误覆盖。

def build_statistics_global() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    from scipy.spatial import cKDTree

    from app.lib.geo_analysis.spatial_weights import (
        auto_band_8nn,
        build_distance_band_weights,
        build_inverse_distance_weights,
        build_knn_weights,
    )
    from app.lib.geo_analysis.statistics import _validate_permutations

    # --- E-7 auto-band 规则：mean k-NN 距离（cKDTree 独立复算）----------
    band_fixtures = {
        "random": _metric_points(seed=31, n=24),
        "grid": _metric_points(seed=32, n=16, jitter=0.0),
        "clustered": _metric_points(seed=33, n=20, jitter=40.0),
    }
    for name, xy in band_fixtures.items():
        tree = cKDTree(xy)
        k8 = min(8, len(xy) - 1)
        ref = float(tree.query(xy, k=k8 + 1)[0][:, k8].mean())
        cases.append(case(
            f"auto_band_8nn_{name}_reference", 
            "app.lib.geo_analysis.spatial_weights:auto_band_8nn",
            r9(ref), args=[xy.tolist()], rtol=1e-9,
            note="mean 8-NN 距离 cKDTree 独立复算（E-7 规则）"))
    cases.append(case(
        "auto_band_8nn_single_point_fallback",
        "app.lib.geo_analysis.spatial_weights:auto_band_8nn",
        1.0, args=[[[500.0, 500.0]]], kind="exact",
        note="n<2 退化为 1.0 m（诚实缺省）"))

    # 注：compute_st_distance_matrix / _two_sided_permutation_pvalue 在
    # 运行时要求 numpy 数组入参（.tobytes() / ndarray 减法）—— JSON 回放
    # 的 list 入参直接 AttributeError/TypeError，无法入 corpus（运行器
    # 局限，见报告）。

    # --- 置换数契约 {99,199,499,999} -----------------------------------
    for p in (99, 199, 499, 999):
        cases.append(case(
            f"validate_permutations_ok_{p}",
            "app.lib.geo_analysis.statistics:_validate_permutations",
            p, args=[p], kind="exact"))
    cases.append(error_case(
        "validate_permutations_zero_rejected",
        "app.lib.geo_analysis.statistics:_validate_permutations",
        "ValueError", args=[0]))
    cases.append(error_case(
        "validate_permutations_noncontract_rejected",
        "app.lib.geo_analysis.statistics:_validate_permutations",
        "ValueError", args=[500]))

    # --- 权重构建器类型化错误 ------------------------------------------
    cases.append(error_case(
        "knn_weights_empty_rejected",
        "app.lib.geo_analysis.spatial_weights:build_knn_weights",
        "INSUFFICIENT_SAMPLES", args=[[]]))
    cases.append(error_case(
        "distance_band_zero_threshold_rejected",
        "app.lib.geo_analysis.spatial_weights:build_distance_band_weights",
        "ValueError", args=[[[0.0, 0.0], [10.0, 0.0]], 0.0]))
    cases.append(error_case(
        "inverse_distance_power_zero_rejected",
        "app.lib.geo_analysis.spatial_weights:build_inverse_distance_weights",
        "UNSUPPORTED_METHOD", args=[[[0.0, 0.0], [10.0, 0.0]], 0.0]))
    cases.append(error_case(
        "inverse_distance_power_over5_rejected",
        "app.lib.geo_analysis.spatial_weights:build_inverse_distance_weights",
        "UNSUPPORTED_METHOD", args=[[[0.0, 0.0], [10.0, 0.0]], 5.5]))
    cases.append(error_case(
        "inverse_distance_scale_guard",
        "app.lib.geo_analysis.spatial_weights:build_inverse_distance_weights",
        "RESOURCE_SCALE_MISMATCH",
        args=[(np.column_stack([np.arange(2001), np.arange(2001)])).tolist(), 1.0],
        note="O(n²) 稠密构造上限 2000（先拒绝不 OOM）"))

    # --- narrated 全局族：类型化语义回归（GeoAnalysisResult 面只可 error）
    st_mod = "app.lib.geo_analysis.statistics"
    g16 = _metric_points(seed=36, n=16, jitter=0.0)
    fc16 = _points_fc(g16, {"val": [float(i % 2) for i in range(16)]})
    fc_missing = _points_fc(g16, {})
    fc_const = _points_fc(g16, {"val": [5.0] * 16})
    fc_zero = _points_fc(g16, {"val": [0.0] * 16})
    fc_neg = _points_fc(g16, {"val": [float(i - 8) for i in range(16)]})
    fc_not_binary = _points_fc(g16, {"b": [float(i % 3) for i in range(16)]})
    fc_n2 = _points_fc(g16[:2], {"val": [1.0, 2.0]})
    fc_n3b = _points_fc(g16[:3], {"b": [0.0, 1.0, 0.0]})
    fc_constb = _points_fc(g16, {"b": [1.0] * 16})
    fc_empty: dict = {"type": "FeatureCollection", "features": []}

    cases.append(error_case(
        "geary_c_empty_fc", f"{st_mod}:geary_c_narrated",
        "NO_VALID_OBSERVATIONS", args=[fc_empty, "val"]))
    cases.append(error_case(
        "geary_c_missing_field", f"{st_mod}:geary_c_narrated",
        "MISSING_REQUIRED_FIELD", args=[fc_missing, "val"]))
    cases.append(error_case(
        "geary_c_too_few_samples", f"{st_mod}:geary_c_narrated",
        "INSUFFICIENT_SAMPLES", args=[fc_n2, "val"]))
    cases.append(error_case(
        "geary_c_constant_field", f"{st_mod}:geary_c_narrated",
        "DEGENERATE_DATA", args=[fc_const, "val"]))
    cases.append(error_case(
        "geary_c_permutation_contract", f"{st_mod}:geary_c_narrated",
        "ValueError", args=[fc16, "val"], kwargs={"permutations": 0}))
    cases.append(error_case(
        "general_g_negative_values", f"{st_mod}:general_g_narrated",
        "UNSUPPORTED_METHOD", args=[fc_neg, "val"]))
    cases.append(error_case(
        "general_g_all_zero", f"{st_mod}:general_g_narrated",
        "DEGENERATE_DATA", args=[fc_zero, "val"]))
    cases.append(error_case(
        "general_g_missing_field", f"{st_mod}:general_g_narrated",
        "MISSING_REQUIRED_FIELD", args=[fc_missing, "val"]))
    cases.append(error_case(
        "general_g_too_few_samples", f"{st_mod}:general_g_narrated",
        "INSUFFICIENT_SAMPLES", args=[fc_n2, "val"]))
    cases.append(error_case(
        "join_count_too_few", f"{st_mod}:join_count_narrated",
        "INSUFFICIENT_SAMPLES", args=[fc_n3b, "b"]))
    cases.append(error_case(
        "join_count_not_binary", f"{st_mod}:join_count_narrated",
        "UNSUPPORTED_METHOD", args=[fc_not_binary, "b"]))
    cases.append(error_case(
        "join_count_single_class", f"{st_mod}:join_count_narrated",
        "DEGENERATE_DATA", args=[fc_constb, "b"]))
    cases.append(error_case(
        "join_count_missing_field", f"{st_mod}:join_count_narrated",
        "MISSING_REQUIRED_FIELD", args=[fc_missing, "b"]))
    cases.append(error_case(
        "bivariate_moran_missing_field", f"{st_mod}:bivariate_moran_narrated",
        "MISSING_REQUIRED_FIELD", args=[fc_missing, "val", "val2"]))
    cases.append(error_case(
        "bivariate_moran_constant_field", f"{st_mod}:bivariate_moran_narrated",
        "DEGENERATE_DATA", args=[fc_const, "val", "val"]))
    cases.append(error_case(
        "bivariate_moran_too_few", f"{st_mod}:bivariate_moran_narrated",
        "INSUFFICIENT_SAMPLES", args=[fc_n2, "val", "val"]))
    cases.append(error_case(
        "moran_i_permutation_contract", f"{st_mod}:moran_i_narrated",
        "ValueError", args=[fc16, "val"], kwargs={"permutations": 3}))
    cases.append(error_case(
        "moran_i_permutation_not_numeric",
        f"{st_mod}:moran_i_narrated", "ValueError",
        args=[fc16], kwargs={"value_field": "val", "permutations": "abc"},
        note="permutations 不可数值 → ValueError（契约校验）"))
    cases.append(error_case(
        "weights_sensitivity_constant_field",
        f"{st_mod}:weights_sensitivity_narrated",
        "DEGENERATE_DATA", args=[fc_const, "val"]))
    cases.append(error_case(
        "weights_sensitivity_missing_field",
        f"{st_mod}:weights_sensitivity_narrated",
        "MISSING_REQUIRED_FIELD", args=[fc_missing, "val"]))
    cases.append(error_case(
        "weights_sensitivity_empty_fc",
        f"{st_mod}:weights_sensitivity_narrated",
        "NO_VALID_OBSERVATIONS", args=[fc_empty, "val"]))

    return cases


# ── 域：statistics_local（局部统计 + 多重校正不变量）─────────────────

def build_statistics_local() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    from app.lib.geo_analysis.spatial_regression import (
        multiple_testing_correction as mtc,
    )
    from app.lib.geo_analysis.statistics import _bh_qvalues

    # --- BH q 值：单调已知序 fixtures + NaN 免疫 ------------------------
    rng = np.random.default_rng(91)
    bh_fixtures = {
        "monotone": [0.001, 0.002, 0.003, 0.4, 0.5, 0.6],
        "mixed": [float(v) for v in rng.uniform(0, 1, 12)],
        "extremes": [0.0001, 0.99, 0.5, 0.0002, 0.7, 0.3],
        "with_nan": [0.01, None, 0.04, 0.8, 0.02],  # None → NaN on coercion
        "all_nan": [None] * 4,
        "single": [0.03],
    }
    for name, p_l in bh_fixtures.items():
        p = np.asarray(p_l, dtype=float)
        q_ref = _bh_reference(p)
        # 数组返回值只能元素级 select（运行器对顶层 ndarray 无聚合路径）。
        for i in range(p.size):
            if not np.isfinite(q_ref[i]):
                continue
            cases.append(case(
                f"bh_qvalues_q{i}_{name}",
                "app.lib.geo_analysis.statistics:_bh_qvalues",
                r9(float(q_ref[i])), args=[p_l], select=str(i), rtol=1e-9,
                note="BH 逐步下确界独立复算（元素级）"))
    q_nan = _bh_reference(np.asarray(bh_fixtures["with_nan"], dtype=float))
    cases.append(case(
        "bh_qvalues_nan_immune_finite",
        "app.lib.geo_analysis.statistics:_bh_qvalues",
        r9(float(q_nan[0])), args=[bh_fixtures["with_nan"]], select="0",
        rtol=1e-9, note="NaN 位置置 1 不影响有限位置 q"))
    cases.append(case(
        "bh_qvalues_all_nan_identity",
        "app.lib.geo_analysis.statistics:_bh_qvalues",
        1.0, args=[bh_fixtures["all_nan"]], select="1", kind="exact",
        note="全 NaN 输入 → q ≡ 1（免疫语义）"))

    # --- multiple_testing_correction：4 法 × 独立参考 -------------------
    mtc_fixtures = {
        "monotone": bh_fixtures["monotone"],
        "uniform": [float(v) for v in rng.uniform(0.0, 1.0, 16)],
        "tiny": [0.0001, 0.02, 0.9],
    }
    for name, p_l in mtc_fixtures.items():
        p = np.asarray(p_l)
        refs = {
            "bonferroni": np.clip(p * p.size, 0.0, 1.0),
            "holm": _holm_reference(p),
            "bh": _bh_reference(p),
            "none": p.copy(),
        }
        for method, ref in refs.items():
            # 数组返回值 → 元素级 select（运行器顶层 ndarray 无聚合路径）。
            for i in (0, ref.size // 2, ref.size - 1):
                cases.append(case(
                    f"mtc_{method}_e{i}_{name}",
                    "app.lib.geo_analysis.spatial_regression:"
                    "multiple_testing_correction",
                    r9(float(ref[i])), args=[p_l, method], select=str(i),
                    rtol=1e-9, note=f"{method} 校正独立复算（元素级）"))
    cases.append(case(
        "mtc_bonferroni_element_clip",
        "app.lib.geo_analysis.spatial_regression:multiple_testing_correction",
        1.0, args=[[0.5, 0.7, 0.9], "bonferroni"], select="2",
        rtol=1e-9, note="p·n 上限 1"))
    cases.append(error_case(
        "mtc_unknown_method",
        "app.lib.geo_analysis.spatial_regression:multiple_testing_correction",
        "UNSUPPORTED_METHOD", args=[[0.1, 0.2], "sidak"]))

    # --- narrated 局部族：类型化语义回归 --------------------------------
    st_mod = "app.lib.geo_analysis.statistics"
    g16 = _metric_points(seed=36, n=16, jitter=0.0)
    fc16 = _points_fc(g16, {"val": [float(i % 2) for i in range(16)],
                            "b": [float(i % 2) for i in range(16)]})
    fc_const = _points_fc(g16, {"val": [3.0] * 16,
                                "b": [1.0] * 16})
    fc_missing = _points_fc(g16, {})
    fc_not_binary = _points_fc(g16, {"b": [float(i % 3) for i in range(16)]})
    fc_n2 = _points_fc(g16[:2], {"val": [1.0, 2.0]})
    fc_n3b = _points_fc(g16[:3], {"b": [0.0, 1.0, 0.0]})
    fc_n6 = _points_fc(g16[:6], {"val": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                                 "val2": [2.0, 1.0, 2.0, 1.0, 2.0, 1.0]})

    cases.append(error_case(
        "local_geary_invalid_correction", f"{st_mod}:local_geary_narrated",
        "ValueError", args=[fc16, "val"], kwargs={"correction": "sidak"}))
    cases.append(error_case(
        "local_geary_too_few", f"{st_mod}:local_geary_narrated",
        "INSUFFICIENT_SAMPLES", args=[fc_n2, "val"]))
    cases.append(error_case(
        "local_geary_constant_field", f"{st_mod}:local_geary_narrated",
        "DEGENERATE_DATA", args=[fc_const, "val"]))
    cases.append(error_case(
        "local_geary_missing_field", f"{st_mod}:local_geary_narrated",
        "MISSING_REQUIRED_FIELD", args=[fc_missing, "val"]))
    cases.append(error_case(
        "local_geary_permutation_contract", f"{st_mod}:local_geary_narrated",
        "ValueError", args=[fc16, "val"], kwargs={"permutations": 42}))
    cases.append(error_case(
        "local_join_count_not_binary", f"{st_mod}:local_join_count_narrated",
        "UNSUPPORTED_METHOD", args=[fc_not_binary, "b"]))
    cases.append(error_case(
        "local_join_count_single_class", f"{st_mod}:local_join_count_narrated",
        "DEGENERATE_DATA", args=[fc_const, "b"]))
    cases.append(error_case(
        "local_join_count_too_few", f"{st_mod}:local_join_count_narrated",
        "INSUFFICIENT_SAMPLES", args=[fc_n3b, "b"]))
    cases.append(error_case(
        "bivariate_local_moran_too_few",
        f"{st_mod}:bivariate_local_moran_narrated",
        "INSUFFICIENT_SAMPLES", args=[fc_n6, "val", "val2"]))
    cases.append(error_case(
        "bivariate_local_moran_constant",
        f"{st_mod}:bivariate_local_moran_narrated",
        "DEGENERATE_DATA", args=[fc_const, "val", "val"]))
    cases.append(error_case(
        "bivariate_local_moran_missing",
        f"{st_mod}:bivariate_local_moran_narrated",
        "MISSING_REQUIRED_FIELD", args=[fc_missing, "val", "val2"]))
    cases.append(error_case(
        "hotspot_invalid_significance_method", f"{st_mod}:hotspot_narrated",
        "ValueError", args=[fc16, "val"],
        kwargs={"significance_method": "bootstrap"},
        note="narrated 缺字段路径返回 failure 结果而非抛错，只有非法方法名"
             "会在校验点抛 ValueError（可回放的错误语义）"))

    return cases


# ── 域：geodetector（地理探测器族：q / 交互 / 生态 / 风险）───────────
#
# geodetector_ecological / geodetector_risk 是纯数组 → dict 的公开入口；
# q / SSW / 交互分类以独立公式复算；narrated 面只覆盖类型化错误。

def build_geodetector() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    from scipy import stats as sps

    from app.lib.geo_analysis.statistics import (
        _classify_interaction,
        _geodetector_q,
        _ssw,
        geodetector_ecological,
        geodetector_risk,
    )

    # --- q 统计量：q = 1 − Σ N_h σ_h² / (N σ²)（总体方差独立复算）------
    y_a = np.array([1.0, 2.0, 3.0, 4.0, 10.0, 11.0, 12.0, 13.0,
                    20.0, 21.0, 22.0, 23.0])
    y_perfect = np.array([5.0] * 4 + [7.0] * 4 + [9.0] * 4)  # 层内零方差
    strata_perfect = np.array(["a"] * 4 + ["b"] * 4 + ["c"] * 4)

    def _q_ref(values, labels):
        values = np.asarray(values, dtype=float)
        tot = float(np.var(values))
        if tot <= 0:
            return 0.0
        sse = 0.0
        for h in set(map(str, labels)):
            m = np.asarray([str(v) == h for v in labels])
            sse += m.sum() * float(np.var(values[m]))
        return float(min(1.0, max(0.0, 1.0 - sse / (len(values) * tot))))

    fixtures_q = [
        ("perfect_strata", y_perfect, strata_perfect, 1.0),
        ("within_strata", np.array([1.0, 1.5, 2.0, 2.5, 3.0, 3.5,
                                    4.0, 4.5, 5.0, 5.5, 6.0, 6.5]),
         strata_perfect, None),
        ("shuffled_strata", y_a, np.array(["a", "b", "c"] * 4), None),
    ]
    for name, values, labels, expect_q in fixtures_q:
        ref = _q_ref(values, labels)
        cases.append(case(
            f"geodetector_q_{name}",
            "app.lib.geo_analysis.statistics:_geodetector_q",
            r9(ref) if expect_q is None else r9(expect_q),
            args=[np.asarray(values).tolist(),
                  [str(v) for v in labels]],
            rtol=1e-9, note="q = 1 − Σ N_h σ_h²/(Nσ²) 独立复算"))
    y_const = [4.0] * 10
    cases.append(case(
        "geodetector_q_constant_zero",
        "app.lib.geo_analysis.statistics:_geodetector_q",
        0.0, args=[y_const, ["a", "a", "b", "b", "a", "a", "b", "b",
                             "a", "b"]], kind="exact",
        note="零方差 → q=0（诚实值）"))

    # --- SSW：层内平方和独立复算 ----------------------------------------
    for name, values, labels, _q in fixtures_q[:2]:
        values = np.asarray(values, dtype=float)
        sse = 0.0
        for h in set(map(str, labels)):
            m = np.asarray([str(v) == h for v in labels])
            sse += float(np.sum((values[m] - values[m].mean()) ** 2))
        cases.append(case(
            f"ssw_{name}",
            "app.lib.geo_analysis.statistics:_ssw",
            r9(sse), args=[values.tolist(), [str(v) for v in labels]],
            rtol=1e-9, note="SSW = Σ_h Σ_i (y_i − ȳ_h)² 独立复算"))

    # --- 交互分类：Wang 2010 决策表（精确字符串）------------------------
    interaction_fixtures = [
        ("independent", 0.30, 0.20, 0.50),
        ("nonlinear_enhanced", 0.30, 0.20, 0.75),
        ("bilinear_enhanced", 0.30, 0.20, 0.42),
        ("nonlinear_weakened", 0.30, 0.20, 0.10),
        ("uni_nonlinear_x", 0.30, 0.20, 0.25),
        ("uni_nonlinear_y", 0.20, 0.30, 0.28),
        ("independent_zero", 0.0, 0.0, 0.0),
        ("bilinear_equal_low", 0.4, 0.4, 0.7),
    ]
    for name, q1, q2, q12 in interaction_fixtures:
        want = "independent" if abs(q12 - (q1 + q2)) <= 1e-9 else (
            "nonlinear_enhanced" if q12 > q1 + q2 else
            "bilinear_enhanced" if q12 > max(q1, q2) else
            "nonlinear_weakened" if q12 < min(q1, q2) else "uni_nonlinear")
        cases.append(case(
            f"interaction_class_{name}",
            "app.lib.geo_analysis.statistics:_classify_interaction",
            want, args=[q1, q2, q12], kind="exact",
            note="Wang 2010 交互决策表"))

    # --- 生态探测器：SSW 比较 t（公式独立复算，df = n−2）----------------
    rng = np.random.default_rng(61)
    n = 24
    y_eco = np.concatenate([rng.normal(0, 1, 12), rng.normal(4, 1, 12)])
    s1_eco = np.array([*"a" * 12, *"b" * 12])
    s2_eco = np.array(["x", "y"] * 12)

    def _eco_refs(y, sa, sb):
        y = np.asarray(y, dtype=float)
        ssw1 = ssw2 = 0.0
        for h in ("a", "b"):
            m = sa == h
            ssw1 += float(np.sum((y[m] - y[m].mean()) ** 2))
        for h in ("x", "y"):
            m = sb == h
            ssw2 += float(np.sum((y[m] - y[m].mean()) ** 2))
        m1, m2 = len(set(sa)), len(set(sb))
        n_ = len(y)
        r1 = ssw1 / (n_ - m1)
        r2 = ssw2 / (n_ - m2)
        denom = float(np.sqrt(r1 ** 2 / (n_ - m1 - 1) + r2 ** 2 / (n_ - m2 - 1)))
        t = (r1 - r2) / denom
        p = float(2.0 * sps.t.sf(abs(t), n_ - 2))
        return ssw1, ssw2, t, p

    eco_fixtures = [
        ("strong", y_eco, s1_eco, s2_eco),
        ("weak", rng.normal(0, 1, n),
         np.array([*"a" * 12, *"b" * 12]), np.array(["x", "y"] * 12)),
    ]
    for name, yv, sa, sb in eco_fixtures:
        sa_l = [str(v) for v in sa]
        sb_l = [str(v) for v in sb]
        res = geodetector_ecological(np.asarray(yv, dtype=float),
                                     np.asarray(sa, dtype=object),
                                     np.asarray(sb, dtype=object))
        ssw1_r, ssw2_r, t_r, p_r = _eco_refs(yv, np.asarray(sa_l),
                                             np.asarray(sb_l))
        cases.append(case(
            f"ecological_ssw1_{name}",
            "app.lib.geo_analysis.statistics:geodetector_ecological",
            r9(ssw1_r), args=[np.asarray(yv, dtype=float).tolist(), sa_l, sb_l],
            select="ssw1", rtol=1e-9, note="SSW 独立复算"))
        cases.append(case(
            f"ecological_t_{name}",
            "app.lib.geo_analysis.statistics:geodetector_ecological",
            r9(t_r), args=[np.asarray(yv, dtype=float).tolist(), sa_l, sb_l],
            select="t_statistic", rtol=1e-8,
            note="t = [SSW₁/(n−m₁) − SSW₂/(n−m₂)] / sqrt(…) 独立复算"))
        cases.append(case(
            f"ecological_p_{name}",
            "app.lib.geo_analysis.statistics:geodetector_ecological",
            r9(p_r), args=[np.asarray(yv, dtype=float).tolist(), sa_l, sb_l],
            select="p_value", rtol=1e-8,
            note="双侧 Student t（df=n−2）独立复算"))
        cases.append(case(
            f"ecological_df_{name}",
            "app.lib.geo_analysis.statistics:geodetector_ecological",
            len(yv) - 2, args=[np.asarray(yv, dtype=float).tolist(), sa_l, sb_l],
            select="df", kind="exact", note="保守自由度 n−2"))
        assert (res["decision"] in
                ("Y1_significantly_dominant", "Y2_significantly_dominant",
                 "no_significant_difference"))
    yv0, sa0, sb0 = eco_fixtures[0][1], eco_fixtures[0][2], eco_fixtures[0][3]
    sa0_l = [str(v) for v in sa0]
    sb0_l = [str(v) for v in sb0]
    _, _, t0, p0 = _eco_refs(yv0, np.asarray(sa0_l), np.asarray(sb0_l))
    decision_ref = (
        "Y1_significantly_dominant" if (p0 < 0.05 and t0 < 0)
        else "Y2_significantly_dominant" if (p0 < 0.05 and t0 > 0)
        else "no_significant_difference")
    cases.append(case(
        "ecological_decision_strong",
        "app.lib.geo_analysis.statistics:geodetector_ecological",
        decision_ref,
        args=[np.asarray(yv0, dtype=float).tolist(), sa0_l, sb0_l],
        select="decision", kind="exact",
        note="t>0 且 p<0.05 → Y1 占优（SSW 更小）"))
    cases.append(error_case(
        "ecological_too_few",
        "app.lib.geo_analysis.statistics:geodetector_ecological",
        "INSUFFICIENT_SAMPLES",
        args=[[1.0, 2.0, 3.0, 4.0], ["a", "a", "b", "b"],
              ["x", "y", "x", "y"]]))
    cases.append(error_case(
        "ecological_constant_y",
        "app.lib.geo_analysis.statistics:geodetector_ecological",
        "DEGENERATE_DATA",
        args=[[5.0] * 12, ["a", "b"] * 6, ["x", "y"] * 6]))
    cases.append(error_case(
        "ecological_single_stratum",
        "app.lib.geo_analysis.statistics:geodetector_ecological",
        "DEGENERATE_DATA",
        args=[[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
              ["a"] * 10, ["x", "y"] * 5]))
    cases.append(error_case(
        "ecological_length_mismatch",
        "app.lib.geo_analysis.statistics:geodetector_ecological",
        "ValueError",
        args=[[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
              ["a", "b"] * 5, ["x", "y"] * 5 + ["x"]]))

    # --- 风险探测器：Welch t 独立复算 + 方向矩阵 ------------------------
    def _welch(va, vb):
        va, vb = np.asarray(va, dtype=float), np.asarray(vb, dtype=float)
        t = (va.mean() - vb.mean()) / np.sqrt(
            va.var(ddof=1) / va.size + vb.var(ddof=1) / vb.size)
        df = ((va.var(ddof=1) / va.size + vb.var(ddof=1) / vb.size) ** 2
              / ((va.var(ddof=1) / va.size) ** 2 / (va.size - 1)
                 + (vb.var(ddof=1) / vb.size) ** 2 / (vb.size - 1)))
        return float(t), float(2.0 * sps.t.sf(abs(t), df))

    rng2 = np.random.default_rng(63)
    y_risk = np.concatenate([rng2.normal(10, 1, 8), rng2.normal(14, 1, 8),
                             rng2.normal(11, 1, 8)])
    strata_risk = np.array([*"p" * 8, *"q" * 8, *"r" * 8])
    y_l = [float(v) for v in y_risk]
    s_l = [str(v) for v in strata_risk]
    va = y_risk[strata_risk == "p"]
    vb = y_risk[strata_risk == "q"]
    t_pq, p_pq = _welch(va, vb)
    cases.append(case(
        "risk_pair_pq_t_reference",
        "app.lib.geo_analysis.statistics:geodetector_risk",
        r9(t_pq), args=[y_l, s_l], select="pairs.0.t_statistic", rtol=1e-7,
        note="Welch t（sorted 层序 p,q,r → pair0 = p vs q）"))
    cases.append(case(
        "risk_pair_pq_p_reference",
        "app.lib.geo_analysis.statistics:geodetector_risk",
        r9(round(p_pq, 6)), args=[y_l, s_l], select="pairs.0.p_value",
        rtol=1e-6, note="Welch–Satterthwaite df 双侧 p 独立复算"))
    cases.append(case(
        "risk_pair_pq_mean_diff",
        "app.lib.geo_analysis.statistics:geodetector_risk",
        r9(round(float(va.mean() - vb.mean()), 6)), args=[y_l, s_l],
        select="pairs.0.mean_diff", rtol=1e-6, note="均值差独立复算"))
    dir_ref = ("higher" if (p_pq < 0.05 and va.mean() > vb.mean())
               else "lower" if (p_pq < 0.05 and va.mean() < vb.mean())
               else "not_significant")
    cases.append(case(
        "risk_pair_pq_direction",
        "app.lib.geo_analysis.statistics:geodetector_risk",
        dir_ref, args=[y_l, s_l], select="pairs.0.direction", kind="exact",
        note="p<0.05 且均值更高 → higher"))
    cases.append(case(
        "risk_matrix_symmetric_entry",
        "app.lib.geo_analysis.statistics:geodetector_risk",
        "not_significant" if dir_ref == "not_significant" else (
            "lower" if dir_ref == "higher" else "higher"),
        args=[y_l, s_l], select="matrix.q.p", kind="exact",
        note="方向矩阵 [q][p] 是 [p][q] 的翻转"))
    cases.append(case(
        "risk_levels_sorted",
        "app.lib.geo_analysis.statistics:geodetector_risk",
        ["p", "q", "r"], args=[y_l, s_l], select="levels", kind="exact",
        note="层词表按字典序"))
    y_sparse = [float(v) for v in np.concatenate(
        [rng2.normal(0, 1, 9), rng2.normal(2, 1, 9)])]
    cases.append(case(
        "risk_sparse_stratum_note_pair",
        "app.lib.geo_analysis.statistics:geodetector_risk",
        None,
        args=[y_sparse, ["a"] * 9 + ["b"] * 8 + ["c"]],
        select="pairs.2.t_statistic", kind="exact",
        note="分层数 <2 → Welch t 诚实置 null"))
    cases.append(error_case(
        "risk_too_few", "app.lib.geo_analysis.statistics:geodetector_risk",
        "INSUFFICIENT_SAMPLES",
        args=[[1.0, 2.0, 3.0, 4.0], ["a", "a", "b", "b"]]))
    cases.append(error_case(
        "risk_single_level", "app.lib.geo_analysis.statistics:geodetector_risk",
        "DEGENERATE_DATA",
        args=[[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
              ["a"] * 10]))
    cases.append(error_case(
        "risk_constant_y", "app.lib.geo_analysis.statistics:geodetector_risk",
        "DEGENERATE_DATA",
        args=[[3.0] * 12, ["a", "b"] * 6]))

    # --- narrated 地理探测器：类型化语义回归 ----------------------------
    gd_mod = "app.lib.geo_analysis.statistics"
    g12 = _metric_points(seed=37, n=12, jitter=0.0)
    g26 = _metric_points(seed=38, n=26, jitter=0.0)
    fc_gd = _points_fc(g12, {"y": [float(v) for v in y_a],
                             "strata": ["a", "b", "c"] * 4})
    fc_gd_missing = _points_fc(g12, {"y": [float(v) for v in y_a]})
    fc_gd_single = _points_fc(g12, {"y": [float(v) for v in y_a],
                                    "strata": ["a"] * 12})
    fc_gd_num = _points_fc(
        g26, {"y": [float(i) for i in range(26)],
              "num": [float(i) for i in range(26)]})
    cases.append(error_case(
        "geodetector_narrated_too_few", f"{gd_mod}:geodetector_narrated",
        "INSUFFICIENT_SAMPLES",
        args=[_points_fc(g12[:8], {"y": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0,
                                   7.0, 8.0], "s": ["a", "b"] * 4}),
              "y", "s"]))
    cases.append(error_case(
        "geodetector_narrated_missing_strata", f"{gd_mod}:geodetector_narrated",
        "MISSING_REQUIRED_FIELD", args=[fc_gd_missing, "y", "strata"]))
    cases.append(error_case(
        "geodetector_narrated_single_level", f"{gd_mod}:geodetector_narrated",
        "DEGENERATE_DATA", args=[fc_gd_single, "y", "strata"]))
    cases.append(error_case(
        "geodetector_narrated_numeric_needs_bins",
        f"{gd_mod}:geodetector_narrated",
        "UNSUPPORTED_METHOD", args=[fc_gd_num, "y", "num"],
        kwargs={"bins": 0},
        note="高基数数值分层字段必须显式 bins ≥ 2"))
    cases.append(error_case(
        "ecological_narrated_missing_field",
        f"{gd_mod}:geodetector_ecological_narrated",
        "MISSING_REQUIRED_FIELD", args=[fc_gd_missing, "y", "strata", "strata"]))
    cases.append(error_case(
        "risk_narrated_missing_field",
        f"{gd_mod}:geodetector_risk_narrated",
        "MISSING_REQUIRED_FIELD", args=[fc_gd_missing, "y", "strata"]))

    return cases


# ── 域：regression（OLS 核心 + 诊断 + GWR 基元；narrated 面只做错误）──
#
# OLS 独立参考 = np.linalg.lstsq（任务书钦定）；诊断量（JB/BP/VIF/LR/
# AIC）按其披露公式用 numpy/scipy 独立复算。narrated 入口（OLS/SLX/
# SAR/SEM/GWR）返回 GeoAnalysisResult —— 数值期望无法 select，用类型化
# 错误语义回归覆盖。

def _ols_reference(y, x_mat):
    """OLS 全套诊断的独立复算（np.linalg.lstsq + 披露公式）。"""
    from scipy import stats as sps
    y = np.asarray(y, dtype=float)
    x_mat = np.asarray(x_mat, dtype=float)
    beta, *_ = np.linalg.lstsq(x_mat, y, rcond=None)
    resid = y - x_mat @ beta
    sse = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    n, p = x_mat.shape
    dof = max(n - p, 1)
    sigma2 = sse / dof
    r2 = 1.0 - sse / ss_tot if ss_tot > 0 else 0.0
    adj_r2 = 1.0 - (1.0 - r2) * (n - 1) / dof
    xtx_inv = np.linalg.pinv(x_mat.T @ x_mat)
    se = np.sqrt(np.maximum(np.diag(xtx_inv) * sigma2, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        t_stats = np.where(se > 0, beta / se, np.nan)
    p_vals = 2.0 * sps.t.sf(np.abs(t_stats), df=dof)
    sigma2_ml = sse / n
    log_lik = -0.5 * n * (np.log(2.0 * np.pi) + np.log(sigma2_ml) + 1.0)
    aic = -2.0 * log_lik + 2.0 * (p + 1)
    f_stat = float("nan")
    f_p = float("nan")
    if p > 1 and (1.0 - r2) > 0:
        f_stat = float((r2 / (p - 1)) / ((1.0 - r2) / dof))
        f_p = float(sps.f.sf(f_stat, p - 1, dof))
    return {"beta": beta, "residuals": resid, "sse": sse, "r2": r2,
            "adj_r2": adj_r2, "aic": aic, "log_lik": log_lik,
            "f_stat": f_stat, "f_p": f_p, "se": se, "p": p_vals}


def _ols_fixtures():
    rng1 = np.random.default_rng(71)
    x1 = rng1.uniform(0, 10, 24)
    x2 = rng1.uniform(0, 10, 24)
    y1 = 3.0 + 2.0 * x1 - 1.5 * x2 + rng1.normal(0, 0.5, 24)
    f1 = (np.column_stack([np.ones(24), x1, x2]), y1.tolist())

    rng2 = np.random.default_rng(72)
    xs = rng2.uniform(0, 8, 16)
    y2 = 5.0 + 0.7 * xs + rng2.normal(0, 1.0, 16)
    f2 = (np.column_stack([np.ones(16), xs]), y2.tolist())

    gx, gy = np.meshgrid(np.arange(5, dtype=float),
                         np.arange(5, dtype=float))
    z = 1.0 + 2.0 * gx.ravel() + 3.0 * gy.ravel()
    f3 = (np.column_stack([np.ones(25), gx.ravel(), gy.ravel()]),
          z.tolist())

    rng3 = np.random.default_rng(73)
    w1, w2, w3 = (rng3.uniform(0, 5, 32) for _ in range(3))
    y3 = 2.0 - 1.0 * w1 + 0.5 * w2 + 3.0 * w3 + rng3.normal(0, 0.2, 32)
    f4 = (np.column_stack([np.ones(32), w1, w2, w3]), y3.tolist())
    return {"noisy2var": f1, "single": f2, "plane_exact": f3,
            "wide3var": f4}


def build_regression() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    from scipy import stats as sps

    from app.lib.geo_analysis.spatial_regression import (
        _bisquare,
        _bisquare_rows,
        _breusch_pagan,
        _check_min_samples,
        _coef_table,
        _gwr_local_r2,
        _gwr_summarize,
        _jarque_bera,
        _log_jacobian,
        _lr_test,
        _ols_core,
        _spatial_model_suggestion,
        _vif,
        _validate_permutation_count,
    )

    reg_mod = "app.lib.geo_analysis.spatial_regression"

    # 并发批次（Foundation V3 批二）把若干私有助手签名改成了 ndarray-only
    # （首行即 .shape / ufunc 运算，不先 asarray）—— JSON 回放的 list 入参
    # 会 AttributeError/TypeError，数值期望无法回放。此处按能力探测自适应：
    #   - 接受 list → 照旧发数值 case；
    #   - 拒绝 list → 发一条「list 入参被类型化拒绝」的 error case（诚实
    #     锁定 ndarray-only 契约），数值面记录为运行器局限（见最终报告）。

    def _probe_ok(fn, *args):
        try:
            fn(*args)
            return True
        except Exception:
            return False

    def _probe_code(fn, *args):
        try:
            fn(*args)
            return None
        except Exception as exc:
            return type(exc).__name__

    ols_lists = _probe_ok(_ols_core, [1.0, 2.0], [[1.0, 1.0], [1.0, 2.0]])
    bp_lists = _probe_ok(_breusch_pagan, [0.1, -0.2, 0.3],
                         [[1.0, 0.5], [1.0, 0.6], [1.0, 0.7]])
    vif_lists = _probe_ok(_vif, [[1.0, 0.5, 0.2], [1.0, 0.6, 0.1],
                                 [1.0, 0.55, 0.15]], ["a", "b", "c"])
    lj_lists = _probe_ok(_log_jacobian, 0.3, [0.1, 0.5, 0.9])
    bisq_lists = _probe_ok(_bisquare, [0.1, 0.5, 0.9], 1.0)
    bisqr_lists = _probe_ok(_bisquare_rows, [1.0, 2.0], [2.0, 4.0])
    gwlr2_lists = _probe_ok(_gwr_local_r2, [[0.0, 0.0], [1.0, 1.0]],
                            [1.0, 2.0], [[1.0, 0.0], [1.0, 1.0]],
                            [[1.0, 0.0], [1.0, 1.0]], 2)
    ct_lists = _probe_ok(_coef_table, [1.0, 2.0], [0.1, 0.1], None, None,
                         ["a", "b"])

    # --- OLS 核心：系数 / R² / AIC / SSE（lstsq 独立参考）---------------
    for name, (x_mat, y_l) in (_ols_fixtures().items() if ols_lists else []):
        ref = _ols_reference(y_l, x_mat)
        x_l = np.asarray(x_mat).tolist()
        n_sel = ref["beta"].size
        for j in range(n_sel):
            cases.append(case(
                f"ols_beta{j}_{name}", f"{reg_mod}:_ols_core",
                r9(float(ref["beta"][j])), args=[y_l, x_l],
                select=f"beta.{j}", rtol=1e-8,
                note="np.linalg.lstsq 独立参考"))
        cases.append(case(
            f"ols_r2_{name}", f"{reg_mod}:_ols_core",
            r9(float(ref["r2"])), args=[y_l, x_l], select="r2", rtol=1e-8))
        cases.append(case(
            f"ols_adj_r2_{name}", f"{reg_mod}:_ols_core",
            r9(float(ref["adj_r2"])), args=[y_l, x_l], select="adj_r2",
            rtol=1e-8))
        cases.append(case(
            f"ols_aic_{name}", f"{reg_mod}:_ols_core",
            r9(float(ref["aic"])), args=[y_l, x_l], select="aic",
            rtol=1e-8, note="高斯对数似然 + 2(p+1)（+1 计 σ²）"))
        cases.append(case(
            f"ols_sse_{name}", f"{reg_mod}:_ols_core",
            r9(float(ref["sse"])), args=[y_l, x_l], select="sse",
            rtol=1e-7, note="残差平方和独立复算"))
        if np.isfinite(ref["f_stat"]):
            cases.append(case(
                f"ols_f_stat_{name}", f"{reg_mod}:_ols_core",
                r9(float(ref["f_stat"])), args=[y_l, x_l], select="f_stat",
                rtol=1e-8))
        if name != "plane_exact":
            cases.append(case(
                f"ols_loglik_{name}", f"{reg_mod}:_ols_core",
                r9(float(ref["log_lik"])), args=[y_l, x_l],
                select="log_lik", rtol=1e-8))
            cases.append(case(
                f"ols_residual_mean_{name}", f"{reg_mod}:_ols_core",
                r9(0.0), args=[y_l, x_l], select="mean:residuals",
                rtol=1e-6, note="含截距 OLS 残差均值 ≈ 0"))

    # --- JB 正态性：n/6·(S² + (K−3)²/4) 手算公式复算 --------------------
    x_n, y_n = _ols_fixtures()["noisy2var"]
    resid_n = _ols_reference(y_n, x_n)["residuals"]
    rng_jb = np.random.default_rng(74)
    jb_fixtures = {
        "ols_residuals": resid_n,
        "normal": rng_jb.normal(0, 1, 40),
        "skewed": np.concatenate([rng_jb.normal(0, 1, 30),
                                  rng_jb.normal(6, 1, 10)]),
    }
    for name, r in jb_fixtures.items():
        r = np.asarray(r, dtype=float)
        # 实现语义已切换为 scipy.stats.jarque_bera（偏度/峰度偏误修正口径）
        # —— scipy 本身即独立参考（任务书钦定 numpy/scipy 参考面）。
        jb_ref = sps.jarque_bera(r)
        r_l = [float(v) for v in r]
        cases.append(case(
            f"jb_statistic_{name}", f"{reg_mod}:_jarque_bera",
            r9(float(jb_ref.statistic)), args=[r_l], select="statistic",
            rtol=1e-7, note="scipy.stats.jarque_bera 直接参考"))
        cases.append(case(
            f"jb_pvalue_{name}", f"{reg_mod}:_jarque_bera",
            r9(float(jb_ref.pvalue)), args=[r_l], select="p_value",
            rtol=1e-7, note="scipy.stats.jarque_bera p 值参考"))

    # --- Koenker-BP：n·R²(aux) 手算复算 ---------------------------------
    for name, (x_mat, y_l) in ((("noisy2var", _ols_fixtures()["noisy2var"]),
                                ("wide3var", _ols_fixtures()["wide3var"]))
                               if bp_lists else []):
        resid = _ols_reference(y_l, x_mat)["residuals"]
        e2 = resid ** 2
        z_aux = e2 / e2.mean()
        aux = _ols_reference(z_aux.tolist(), x_mat)
        stat = len(resid) * aux["r2"]
        df = x_mat.shape[1] - 1
        p_ref = float(sps.chi2.sf(stat, df))
        cases.append(case(
            f"bp_statistic_{name}", f"{reg_mod}:_breusch_pagan",
            r9(stat),
            args=[[float(v) for v in resid], np.asarray(x_mat).tolist()],
            select="statistic", rtol=1e-7,
            note="n·R²（e²/mean(e²) 对 X 辅助回归）复算"))
        cases.append(case(
            f"bp_pvalue_{name}", f"{reg_mod}:_breusch_pagan",
            r9(p_ref),
            args=[[float(v) for v in resid], np.asarray(x_mat).tolist()],
            select="p_value", rtol=1e-7, note="chi2.sf(stat, p−1) 复算"))

    if not bp_lists:
        cases.append(error_case(
            "breusch_pagan_list_args_rejected", f"{reg_mod}:_breusch_pagan",
            _probe_code(_breusch_pagan, [0.1, -0.2, 0.3],
                        [[1.0, 0.5], [1.0, 0.6], [1.0, 0.7]]),
            args=[[0.1, -0.2, 0.3],
                  [[1.0, 0.5], [1.0, 0.6], [1.0, 0.7]]],
            note="ndarray-only 契约：list 入参类型化拒绝（运行器局限）"))

    # --- VIF：1/(1−R²_j) 辅助回归复算 ------------------------------------
    for name, (x_mat, _y) in ((("wide3var", _ols_fixtures()["wide3var"]),)
                              if vif_lists else []):
        x_arr = np.asarray(x_mat)
        for j in range(1, x_arr.shape[1]):
            others = np.column_stack(
                [x_arr[:, 0], np.delete(x_arr, j, axis=1)])
            r2j = float(np.clip(_ols_reference(
                x_arr[:, j].tolist(), others.tolist())["r2"],
                0.0, 1.0 - 1e-12))
            cases.append(case(
                f"vif_j{j}_{name}", f"{reg_mod}:_vif",
                r9(round(1.0 / (1.0 - r2j), 6)),
                args=[np.asarray(x_mat).tolist(),
                      ["intercept", "x1", "x2", "x3"]],
                select=f"{j - 1}.vif", rtol=1e-6,
                note="列对其余列辅助回归的 1/(1−R²) 复算"))
    # 完全共线 fixture：VIF 爆炸（实现钳到 1−1e-12）
    x_col = np.linspace(0, 1, 20)
    x_coll = np.column_stack([np.ones(20), x_col, 2.0 * x_col])
    if vif_lists:
        cases.append(case(
            "vif_collinear_clipped", f"{reg_mod}:_vif",
            r9(round(1.0 / 1e-12, 6)), args=[x_coll.tolist(), ["i", "a", "b"]],
            select="1.vif", rtol=1e-6, note="完全共线 → R² 钳 1−1e-12"))
    else:
        cases.append(error_case(
            "vif_list_args_rejected", f"{reg_mod}:_vif",
            _probe_code(_vif, [[1.0, 0.5, 0.2], [1.0, 0.6, 0.1],
                               [1.0, 0.55, 0.15]], ["a", "b", "c"]),
            args=[[[1.0, 0.5, 0.2], [1.0, 0.6, 0.1], [1.0, 0.55, 0.15]],
                  ["a", "b", "c"]],
            note="ndarray-only 契约：list 入参类型化拒绝（运行器局限）"))

    # --- LR 检验：chi2.sf(max(2ΔLL,0), 1) 复算 ---------------------------
    for name, ll1, ll0 in (("positive", -120.5, -135.25),
                           ("zero", -100.0, -100.0),
                           ("small", -55.3, -55.9)):
        lr = max(2.0 * (ll1 - ll0), 0.0)
        cases.append(case(
            f"lr_stat_{name}", f"{reg_mod}:_lr_test",
            r9(lr), args=[ll1, ll0], select="lr", rtol=1e-9))
        cases.append(case(
            f"lr_pvalue_{name}", f"{reg_mod}:_lr_test",
            r9(float(sps.chi2.sf(lr, 1))), args=[ll1, ll0],
            select="p_value", rtol=1e-9))

    # --- 对数雅可比：Σ ln(1−ρκ) 解析复算 ---------------------------------
    rng_k = np.random.default_rng(75)
    kappa = np.sort(rng_k.uniform(-0.5, 0.9, 12))
    if lj_lists:
        for name, rho in (("mid", 0.3), ("low", -0.4), ("high", 0.85)):
            ref = float(np.log(1.0 - rho * kappa).sum())
            cases.append(case(
                f"log_jacobian_{name}", f"{reg_mod}:_log_jacobian",
                r9(ref), args=[rho, [float(v) for v in kappa]], rtol=1e-9,
                note="Σ ln(1−ρκ) 解析复算（Ord 1975）"))
    else:
        cases.append(error_case(
            "log_jacobian_list_args_rejected", f"{reg_mod}:_log_jacobian",
            _probe_code(_log_jacobian, 0.3, [0.1, 0.5, 0.9]),
            args=[0.3, [0.1, 0.5, 0.9]],
            note="ndarray-only 契约：list 入参类型化拒绝（运行器局限）"))

    # --- bisquare 核：(1−u²)² 公式 ---------------------------------------
    d_l = [0.0, 0.25, 0.5, 0.9, 1.5]
    d_arr = np.asarray(d_l)
    ref_bis = np.where(d_arr / 1.0 < 1.0, (1.0 - (d_arr / 1.0) ** 2) ** 2, 0.0)
    for i in ((0, 2, 4) if bisq_lists else []):
        cases.append(case(
            f"bisquare_element{i}", f"{reg_mod}:_bisquare",
            r9(float(ref_bis[i])), args=[d_l, 1.0], select=str(i),
            rtol=1e-9, note="(1−(d/d_max)²)² 公式复算"))
    if not bisq_lists:
        cases.append(error_case(
            "bisquare_list_args_rejected", f"{reg_mod}:_bisquare",
            _probe_code(_bisquare, [0.1, 0.5, 0.9], 1.0),
            args=[[0.1, 0.5, 0.9], 1.0],
            note="ndarray-only 契约：list 入参类型化拒绝（运行器局限）"))
    d2 = np.asarray([1.0, 2.0, 3.0, 4.0])
    dmax2 = np.asarray([2.0, 2.0, 0.0, 4.0])
    ref_rows = np.where(dmax2 > 0, 0.0, 0.0)
    u = d2 / np.where(dmax2 > 0, dmax2, 1.0)
    ref_rows = np.where((dmax2 > 0) & (u < 1.0), (1.0 - u * u) ** 2, 0.0)
    for i in ((0, 1, 2) if bisqr_lists else []):
        cases.append(case(
            f"bisquare_rows_element{i}", f"{reg_mod}:_bisquare_rows",
            r9(float(ref_rows[i])),
            args=[d2.tolist(), dmax2.tolist()], select=str(i), rtol=1e-9,
            note="逐行 bisquare（d_max≤0 行全零）公式复算"))

    if not bisqr_lists:
        cases.append(error_case(
            "bisquare_rows_list_args_rejected", f"{reg_mod}:_bisquare_rows",
            _probe_code(_bisquare_rows, [1.0, 2.0], [2.0, 4.0]),
            args=[[1.0, 2.0], [2.0, 4.0]],
            note="ndarray-only 契约：list 入参类型化拒绝（运行器局限）"))

    # --- GWR 摘要 + 局部 R²（独立 WLS 复算）------------------------------
    from scipy.spatial import cKDTree
    rng_g = np.random.default_rng(76)
    coords_g = rng_g.uniform(0, 1000, (24, 2))
    xg = rng_g.uniform(0, 10, 24)
    yg = 2.0 + 0.5 * xg + 0.3 * (coords_g[:, 0] / 1000.0) * xg \
        + rng_g.normal(0, 0.1, 24)
    x_mat_g = np.column_stack([np.ones(24), xg])

    def _gwr_local_fit_ref(coords, y, x_mat, k):
        n, p = x_mat.shape
        take = max(min(k - 1, n - 1), 1)
        tree = cKDTree(coords)
        nn_dist, nn_idx = tree.query(coords, k=min(k + 1, n))
        betas = np.zeros((n, p))
        fitted = np.zeros(n)
        tr_s = 0.0
        for i in range(n):
            nbr = np.atleast_1d(nn_idx[i])
            dist = np.atleast_1d(nn_dist[i])
            keep = nbr != i
            nbr, dist = nbr[keep], dist[keep]
            order = np.argsort(dist, kind="stable")[:take]
            nbr, dist = nbr[order], dist[order]
            d_max = float(dist[-1]) if nbr.size else 0.0
            u = dist / d_max
            wts = np.where(u < 1.0, (1.0 - u * u) ** 2, 0.0)
            nbr = np.append(nbr, i)
            wts = np.append(wts, 1.0)
            xn = x_mat[nbr]
            xtwx = xn.T @ (xn * wts[:, None])
            xtwy = xn.T @ (wts * y[nbr])
            beta_i = np.linalg.solve(xtwx, xtwy)
            betas[i] = beta_i
            fitted[i] = float(x_mat[i] @ beta_i)
            tr_s += float(x_mat[i] @ (np.linalg.inv(xtwx) @ x_mat[i]))
        return betas, fitted, tr_s

    betas_ref, fitted_ref, tr_s_ref = _gwr_local_fit_ref(
        coords_g, yg, x_mat_g, 8)
    coords_l = coords_g.tolist()
    y_gl = [float(v) for v in yg]
    x_gl = x_mat_g.tolist()
    betas_gl = betas_ref.tolist()

    def _gwr_local_r2_ref(coords, y, x_mat, betas, k):
        n, _p = x_mat.shape
        take = min(max(k - 1, 1), n - 1)
        tree = cKDTree(coords)
        nn_dist, nn_idx = tree.query(coords, k=min(k + 1, n))
        out = np.zeros(n)
        for i in range(n):
            nbr = np.atleast_1d(nn_idx[i])
            dist = np.atleast_1d(nn_dist[i])
            keep = nbr != i
            nbr, dist = nbr[keep], dist[keep]
            order = np.argsort(dist, kind="stable")[:take]
            nbr, dist = nbr[order], dist[order]
            d_max = float(dist[-1]) if nbr.size else 0.0
            u = dist / d_max
            wts = np.where(u < 1.0, (1.0 - u * u) ** 2, 0.0)
            nbr = np.append(nbr, i)
            wts = np.append(wts, 1.0)
            pred = x_mat[nbr] @ betas[i]
            y_n = y[nbr]
            sse_i = float(np.sum(wts * (y_n - pred) ** 2))
            ybar_w = float(np.sum(wts * y_n) / np.sum(wts))
            tss_i = float(np.sum(wts * (y_n - ybar_w) ** 2))
            out[i] = 1.0 - sse_i / tss_i if tss_i > 0 else 0.0
        return out

    lr2_ref = _gwr_local_r2_ref(coords_g, yg, x_mat_g, betas_ref, 8)
    for i in ((0, 11, 23) if gwlr2_lists else []):
        cases.append(case(
            f"gwr_local_r2_obs{i}", f"{reg_mod}:_gwr_local_r2",
            r9(round(float(lr2_ref[i]), 6)),
            args=[coords_l, y_gl, x_gl, betas_gl, 8], select=str(i),
            rtol=1e-6, note="逐观测加权局部 R² 独立复算（Fotheringham 2002）"))
    if not gwlr2_lists:
        cases.append(error_case(
            "gwr_local_r2_list_args_rejected", f"{reg_mod}:_gwr_local_r2",
            _probe_code(_gwr_local_r2, [[0.0, 0.0], [1.0, 1.0]],
                        [1.0, 2.0], [[1.0, 0.0], [1.0, 1.0]],
                        [[1.0, 0.0], [1.0, 1.0]], 2),
            args=[[[0.0, 0.0], [1.0, 1.0]], [1.0, 2.0],
                  [[1.0, 0.0], [1.0, 1.0]], [[1.0, 0.0], [1.0, 1.0]], 2],
            note="ndarray-only 契约：list 入参类型化拒绝（运行器局限）"))

    med = float(np.median(betas_ref[:, 1]))
    cases.append(case(
        "gwr_summarize_median_slope", f"{reg_mod}:_gwr_summarize",
        r9(round(med, 6)), args=[[float(v) for v in betas_ref[:, 1]]],
        select="median", rtol=1e-6, note="min/median/max 摘要独立复算"))
    cases.append(case(
        "gwr_summarize_max_intercept", f"{reg_mod}:_gwr_summarize",
        r9(round(float(betas_ref[:, 0].max()), 6)),
        args=[[float(v) for v in betas_ref[:, 0]]], select="max",
        rtol=1e-6))
    cases.append(case(
        "gwr_summarize_min_intercept", f"{reg_mod}:_gwr_summarize",
        r9(round(float(betas_ref[:, 0].min()), 6)),
        args=[[float(v) for v in betas_ref[:, 0]]], select="min",
        rtol=1e-6))

    # --- 系数表 + LM 决策树（精确字符串）---------------------------------
    x_w, y_w = _ols_fixtures()["wide3var"]
    ref_w = _ols_reference(y_w, x_w)
    coef_names = ["intercept", "x1", "x2", "x3"]
    tbl = _coef_table(ref_w["beta"], ref_w["se"],
                      np.where(np.isfinite(ref_w["se"]),
                               ref_w["beta"] / np.where(ref_w["se"] > 0,
                                                        ref_w["se"], 1.0),
                               np.nan),
                      ref_w["p"], coef_names,
                      [{"name": "x1", "vif": 1.234},
                       {"name": "x2", "vif": 1.567},
                       {"name": "x3", "vif": 1.89}])
    if not ct_lists:
        cases.append(error_case(
            "coef_table_list_args_rejected", f"{reg_mod}:_coef_table",
            _probe_code(_coef_table, [1.0, 2.0], [0.1, 0.1], None, None,
                        ["a", "b"]),
            args=[[1.0, 2.0], [0.1, 0.1], [None, None], [None, None],
                  ["a", "b"]],
            note="ndarray-only 契约：list 入参类型化拒绝（运行器局限）"))
    if ct_lists:
        cases.append(case(
            "coef_table_row0_name", f"{reg_mod}:_coef_table",
            "intercept", args=[ref_w["beta"].tolist(), ref_w["se"].tolist(),
                               [None] * 4, [None] * 4, coef_names],
            select="0.name", kind="exact"))
        cases.append(case(
            "coef_table_row1_coef", f"{reg_mod}:_coef_table",
            r9(float(ref_w["beta"][1])), args=[ref_w["beta"].tolist(),
                                               ref_w["se"].tolist(),
                                               [None] * 4, [None] * 4,
                                               coef_names],
            select="1.coef", rtol=1e-9))
        cases.append(case(
            "coef_table_vif_passthrough", f"{reg_mod}:_coef_table",
            1.567, args=[ref_w["beta"].tolist(), ref_w["se"].tolist(),
                         [None] * 4, [None] * 4, coef_names,
                         [{"name": "x1", "vif": 1.234},
                          {"name": "x2", "vif": 1.567},
                          {"name": "x3", "vif": 1.89}]],
            select="2.vif", rtol=1e-9, note="VIF 透传"))

    lm_fixtures = [
        ("lag_only", {"lm_error": {"p_value": 0.5},
                      "lm_lag": {"p_value": 0.01},
                      "robust_lm_error": {"p_value": 0.5},
                      "robust_lm_lag": {"p_value": 0.4}},
         "spatial.sar_ml"),
        ("error_only", {"lm_error": {"p_value": 0.01},
                        "lm_lag": {"p_value": 0.5},
                        "robust_lm_error": {"p_value": 0.3},
                        "robust_lm_lag": {"p_value": 0.6}},
         "spatial.sem_ml"),
        ("both_robust_lag", {"lm_error": {"p_value": 0.01},
                             "lm_lag": {"p_value": 0.01},
                             "robust_lm_error": {"p_value": 0.5},
                             "robust_lm_lag": {"p_value": 0.01}},
         "spatial.sar_ml"),
        ("none_sig", {"lm_error": {"p_value": 0.5},
                      "lm_lag": {"p_value": 0.5},
                      "robust_lm_error": {"p_value": 0.5},
                      "robust_lm_lag": {"p_value": 0.5}},
         ""),
    ]
    for name, lm, want in lm_fixtures:
        cases.append(case(
            f"lm_decision_{name}", f"{reg_mod}:_spatial_model_suggestion",
            want, args=[lm], kind="exact",
            note="Anselin 1988 LM 决策树"))

    # --- 置换数契约 ------------------------------------------------------
    cases.append(case("validate_perm_count_identity",
                      f"{reg_mod}:_validate_permutation_count",
                      499, args=[499], kind="exact"))
    cases.append(error_case("validate_perm_count_bad",
                            f"{reg_mod}:_validate_permutation_count",
                            "ValueError", args=[7]))
    cases.append(error_case("min_samples_guard",
                            f"{reg_mod}:_check_min_samples",
                            "INSUFFICIENT_SAMPLES", args=[5, 3],
                            note="n < 2p+2 = 8 → 类型化拒绝"))

    # --- narrated 回归族：类型化语义回归 ----------------------------------
    rng_f = np.random.default_rng(78)
    xy_r = _metric_points(seed=39, n=12, jitter=0.0)
    yr = (3.0 + 2.0 * rng_f.uniform(0, 10, 12)).tolist()
    xr = rng_f.uniform(0, 10, 12).tolist()
    fc_r = _points_fc(xy_r, {"y": yr, "x": xr})
    fc_r_const_y = _points_fc(xy_r, {"y": [4.0] * 12, "x": xr})
    fc_r_const_x = _points_fc(xy_r, {"y": yr, "x": [2.0] * 12})
    fc_r_missing = _points_fc(xy_r, {"y": yr})
    fc_r_empty: dict = {"type": "FeatureCollection", "features": []}
    fc_r_small = _points_fc(xy_r[:5], {"y": yr[:5], "x": xr[:5]})

    cases.append(error_case("ols_narrated_too_few",
                            f"{reg_mod}:ols_regression_narrated",
                            "INSUFFICIENT_SAMPLES", args=[fc_r_small, "y",
                                                          ["x"]]))
    cases.append(error_case("ols_narrated_constant_target",
                            f"{reg_mod}:ols_regression_narrated",
                            "DEGENERATE_DATA", args=[fc_r_const_y, "y",
                                                     ["x"]]))
    cases.append(error_case("ols_narrated_constant_explanatory",
                            f"{reg_mod}:ols_regression_narrated",
                            "DEGENERATE_DATA", args=[fc_r_const_x, "y",
                                                     ["x"]]))
    cases.append(error_case("ols_narrated_missing_field",
                            f"{reg_mod}:ols_regression_narrated",
                            "MISSING_REQUIRED_FIELD", args=[fc_r_missing,
                                                            "y", ["x2"]]))
    cases.append(error_case("ols_narrated_empty_fc",
                            f"{reg_mod}:ols_regression_narrated",
                            "NO_VALID_OBSERVATIONS", args=[fc_r_empty, "y",
                                                           ["x"]]))
    cases.append(error_case("ols_narrated_permutation_contract",
                            f"{reg_mod}:ols_regression_narrated",
                            "ValueError", args=[fc_r, "y", ["x"]],
                            kwargs={"permutations": 1}))
    cases.append(error_case("slx_narrated_too_few",
                            f"{reg_mod}:slx_regression_narrated",
                            "INSUFFICIENT_SAMPLES", args=[fc_r_small, "y",
                                                          ["x"]]))
    cases.append(error_case("slx_narrated_constant_target",
                            f"{reg_mod}:slx_regression_narrated",
                            "DEGENERATE_DATA", args=[fc_r_const_y, "y",
                                                     ["x"]]))
    cases.append(error_case("sar_narrated_too_few",
                            f"{reg_mod}:sar_ml_regression_narrated",
                            "INSUFFICIENT_SAMPLES", args=[fc_r_small, "y",
                                                          ["x"]]))
    cases.append(error_case("sar_narrated_constant_target",
                            f"{reg_mod}:sar_ml_regression_narrated",
                            "DEGENERATE_DATA", args=[fc_r_const_y, "y",
                                                     ["x"]]))
    cases.append(error_case("sem_narrated_too_few",
                            f"{reg_mod}:sem_ml_regression_narrated",
                            "INSUFFICIENT_SAMPLES", args=[fc_r_small, "y",
                                                          ["x"]]))
    cases.append(error_case("sem_narrated_missing_field",
                            f"{reg_mod}:sem_ml_regression_narrated",
                            "MISSING_REQUIRED_FIELD", args=[fc_r_missing,
                                                            "y", ["x2"]]))
    cases.append(error_case("gwr_narrated_bad_selection",
                            f"{reg_mod}:gwr_regression_narrated",
                            "UNSUPPORTED_METHOD", args=[fc_r, "y", ["x"]],
                            kwargs={"bandwidth_selection": "aic"}))
    cases.append(error_case("gwr_narrated_too_few",
                            f"{reg_mod}:gwr_regression_narrated",
                            "INSUFFICIENT_SAMPLES", args=[fc_r_small, "y",
                                                          ["x"]]))
    cases.append(error_case("gwr_narrated_constant_target",
                            f"{reg_mod}:gwr_regression_narrated",
                            "DEGENERATE_DATA", args=[fc_r_const_y, "y",
                                                     ["x"]]))

    return cases


# ── 域：terrain（地形科学 · 闭式 fixture + 决策表 + 射线行走独立复算）────
#
# 运行器限制（重要，详见最终报告）：terrain.py 的**全部公开入口**
# （tpi/roughness/curvature/d8/flow_accumulation/fill/dinf/twi/spi/ls/
# openness/geomorphons/landform/hillshade/horizon/svf/…）返回
# ``(result, meta)`` 二元组 —— 冻结运行器的 select 只能下钻 dict/list/
# ndarray，元组不可下钻。因此本域的**数值** case 落在模块的纯数学
# 基元层（积分图 box-sums、坡度角换算、SCA、几何morphon 决策表、
# 地平线射线行走、D8 邻域距离、拓扑序、bilinear 采样），参考值全部
# 由独立 numpy/手写循环复算；公开入口用类型化错误语义回归覆盖
# （EDGE_POLICY / 护栏 / 哨兵语义的守卫面）。

T_MOD = "app.lib.geo_analysis.terrain"


def build_terrain() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    from app.lib.geo_analysis import terrain as tn

    # --- window 校验基元（奇整数 [3,101]）--------------------------------
    for w_in in (3, 5, 101, 7.0):
        cases.append(case(
            f"validate_window_ok_{w_in}", f"{T_MOD}:_validate_window",
            int(w_in), args=[w_in], kind="exact"))
    for bad in (2, 4, 103, "abc", True, None, 3.5, [], 2.5):
        cases.append(error_case(
            f"validate_window_rejects_{_id_tag(bad)}",
            f"{T_MOD}:_validate_window", "ValueError", args=[bad]))
    for ok in (99.0, 3.0):
        cases.append(case(
            f"validate_window_ok_float_{ok}", f"{T_MOD}:_validate_window",
            int(ok), args=[ok], kind="exact", note="整值 float 直通"))

    # --- 坡度角换算：degrees/radians/percent 闭式（arctan(s/100)）---------
    deg_l = [0.0, 30.0, 45.0, 60.0, 90.0]
    ref_deg = np.radians(deg_l)
    for i in (0, 2, 4):
        cases.append(case(
            f"slope_to_radians_deg_{i}", f"{T_MOD}:_slope_to_radians",
            r9(float(ref_deg[i])), args=[deg_l, "degrees"],
            select=str(i), rtol=1e-12, note="radians(deg) 换算复算"))
    rad_l = [0.5236, 1.2]
    for i, v in enumerate(rad_l):
        cases.append(case(
            f"slope_to_radians_rad_{i}", f"{T_MOD}:_slope_to_radians",
            r9(v), args=[rad_l, "radians"], select=str(i), rtol=1e-12,
            note="radians 直通"))
    pct_l = [0.0, 50.0, 100.0]
    ref_pct = np.arctan(np.asarray(pct_l) / 100.0)
    for i in range(3):
        cases.append(case(
            f"slope_to_radians_pct_{i}", f"{T_MOD}:_slope_to_radians",
            r9(float(ref_pct[i])), args=[pct_l, "percent"],
            select=str(i), rtol=1e-12, note="arctan(s/100) 复算"))
    cases.append(error_case("slope_to_radians_bad_units",
                            f"{T_MOD}:_slope_to_radians", "ValueError",
                            args=[[10.0], "grad"]))

    # --- SCA = (accum+1)·cell_area / contour_width -----------------------
    acc_a = [[0.0, 3.0], [7.0, 15.0]]
    sca_ref = (np.asarray(acc_a) + 1.0) * (2.0 * 2.0) / 2.0
    for i in (0, 1):
        for j in (0, 1):
            cases.append(case(
                f"sca_a{i}{j}", f"{T_MOD}:_specific_catchment_area",
                r9(float(sca_ref[i, j])), args=[acc_a, 2.0, 2.0, 2.0],
                select=f"{i}.{j}", rtol=1e-12,
                note="(accum+1)·cell_area/width 复算"))
    cases.append(case(
        "sca_anisotropic", f"{T_MOD}:_specific_catchment_area",
        1.0 * (3.0 * 1.5) / 1.5, args=[[[0.0, 0.0]], 3.0, 1.5, 1.5],
        select="0.0", rtol=1e-12))

    # --- D8 邻域米制距离（编码序 E,SE,S,SW,W,NW,N,NE）--------------------
    nd = tn._neighbor_distances(2.0, 1.0)
    ref_nd = [math.hypot(abs(dc) * 2.0, abs(dr) * 1.0)
              for _, _, dr, dc in tn._D8_NEIGHBORS]
    for i in range(8):
        cases.append(case(
            f"neighbor_distances_{i}", f"{T_MOD}:_neighbor_distances",
            r9(ref_nd[i]), args=[2.0, 1.0], select=str(i), rtol=1e-12,
            note="hypot(Δcol·cx, Δrow·cy) 复算（D8 编码序）"))
    for i in (1, 3):
        cases.append(case(
            f"neighbor_distances_unit_{i}", f"{T_MOD}:_neighbor_distances",
            r9(math.sqrt(2.0)), args=[1.0, 1.0], select=str(i), rtol=1e-12))

    # --- 边界掩膜（True=非边界）· 形状对齐谓词 ---------------------------
    border = tn._border_inside(3, 4)
    for i, j, want in ((0, 0, False), (1, 1, True), (1, 3, False),
                       (2, 2, False)):
        cases.append(case(
            f"border_inside_{i}_{j}", f"{T_MOD}:_border_inside", want,
            args=[3, 4], select=f"{i}.{j}", kind="exact"))
    cases.append(case("border_inside_2x2_corner", f"{T_MOD}:_border_inside",
                      False, args=[2, 2], select="0.0", kind="exact",
                      note="2x2 网格全是边界"))
    cases.append(case("border_inside_5x5_center", f"{T_MOD}:_border_inside",
                      True, args=[5, 5], select="2.2", kind="exact"))
    for i, j, want in ((1, 2, True), (0, 2, False), (3, 3, False), (2, 0, False)):
        cases.append(case(
            f"border_inside_4x4_{i}_{j}", f"{T_MOD}:_border_inside", want,
            args=[4, 4], select=f"{i}.{j}", kind="exact"))
    for i, j, want in ((1, 1, True), (2, 2, True), (2, 3, True), (3, 4, True)):
        cases.append(case(
            f"border_inside_6x6_{i}_{j}", f"{T_MOD}:_border_inside", want,
            args=[6, 6], select=f"{i}.{j}", kind="exact",
            note="6x6 内圈为非边界（窗口收缩语义的掩膜基础）"))
    cases.append(case("aligned_ok_returns_none", f"{T_MOD}:_aligned",
                      None, args=[[[1.0, 2.0]], [[3.0, 4.0]], "a", "b"],
                      kind="exact", note="同形 → 静默放行（返回 None）"))
    cases.append(error_case("aligned_mismatch_rejected", f"{T_MOD}:_aligned",
                            "ValueError", args=[[[1.0]], [[1.0, 2.0]], "a", "b"]))

    # --- SCA / D8 邻域距离扩展扫描（asarray-first 基元，列表可回放）-------
    acc_b = [[2.0, 0.0, 5.0], [1.0, 1.0, 1.0], [0.0, 9.0, 4.0]]
    sca_b = (np.asarray(acc_b) + 1.0) * 4.0 / 2.0
    for i, j in ((0, 0), (1, 2), (2, 1), (0, 1), (1, 0), (2, 2), (0, 2)):
        cases.append(case(
            f"sca_b{i}{j}", f"{T_MOD}:_specific_catchment_area",
            r9(float(sca_b[i, j])), args=[acc_b, 2.0, 2.0, 2.0],
            select=f"{i}.{j}", rtol=1e-12))
    acc_c = [[6.0]]
    cases.append(case("sca_single_cell", f"{T_MOD}:_specific_catchment_area",
                      7.0 * (5.0 * 0.5) / 0.5, args=[acc_c, 5.0, 0.5, 0.5],
                      select="0.0", rtol=1e-12, note="各向异性像元 SCA 复算"))
    nd2 = tn._neighbor_distances(5.0, 0.5)
    ref_nd2 = [math.hypot(abs(dc) * 5.0, abs(dr) * 0.5)
               for _, _, dr, dc in tn._D8_NEIGHBORS]
    for i in range(8):
        cases.append(case(
            f"neighbor_distances_aniso_{i}", f"{T_MOD}:_neighbor_distances",
            r9(ref_nd2[i]), args=[5.0, 0.5], select=str(i), rtol=1e-12))
    for i in (0, 2, 4, 6):
        cases.append(case(
            f"neighbor_distances_iso_{i}", f"{T_MOD}:_neighbor_distances",
            2.0, args=[2.0, 2.0], select=str(i), kind="exact",
            note="正交邻域距离 = cx"))
    for i in (1, 5):
        cases.append(case(
            f"neighbor_distances_iso_diag_{i}", f"{T_MOD}:_neighbor_distances",
            r9(math.sqrt(2) * 2.0), args=[2.0, 2.0], select=str(i),
            rtol=1e-12, note="对角邻域距离 = sqrt(2)·cx"))
    nd3 = tn._neighbor_distances(0.5, 3.0)
    ref_nd3 = [math.hypot(abs(dc) * 0.5, abs(dr) * 3.0)
               for _, _, dr, dc in tn._D8_NEIGHBORS]
    for i in range(8):
        cases.append(case(
            f"neighbor_distances_tall_{i}", f"{T_MOD}:_neighbor_distances",
            r9(ref_nd3[i]), args=[0.5, 3.0], select=str(i), rtol=1e-12,
            note="强各向异性像元的 D8 邻域米制距离"))
    # acc_d SCA fixture（(accum+1) 口径的第二形态）
    acc_d = [[1.0, 2.0], [4.0, 8.0]]
    sca_d = (np.asarray(acc_d) + 1.0) * (3.0 * 1.0) / 3.0
    for i, j in ((0, 1), (1, 0)):
        cases.append(case(
            f"sca_d{i}{j}", f"{T_MOD}:_specific_catchment_area",
            r9(float(sca_d[i, j])), args=[acc_d, 3.0, 1.0, 3.0],
            select=f"{i}.{j}", rtol=1e-12))
    nd4 = tn._neighbor_distances(1.0, 2.0)
    ref_nd4 = [math.hypot(abs(dc) * 1.0, abs(dr) * 2.0)
               for _, _, dr, dc in tn._D8_NEIGHBORS]
    for i in range(8):
        cases.append(case(
            f"neighbor_distances_mix_{i}", f"{T_MOD}:_neighbor_distances",
            r9(ref_nd4[i]), args=[1.0, 2.0], select=str(i), rtol=1e-12,
            note="温和各向异性像元的 D8 邻域米制距离"))
    deg5 = [3.0, 87.0]
    for i in range(2):
        cases.append(case(
            f"slope_to_radians_deg5_{i}", f"{T_MOD}:_slope_to_radians",
            r9(float(np.radians(deg5[i]))), args=[deg5, "degrees"],
            select=str(i), rtol=1e-12))
    pct5 = [3.0, 175.0]
    for i in range(2):
        cases.append(case(
            f"slope_to_radians_pct5_{i}", f"{T_MOD}:_slope_to_radians",
            r9(float(np.arctan(pct5[i] / 100.0))), args=[pct5, "percent"],
            select=str(i), rtol=1e-12))
    for i, j, want in ((2, 3, True), (3, 3, True), (4, 1, True), (0, 3, False),
                       (5, 6, False)):
        cases.append(case(
            f"border_inside_7x7_{i}_{j}", f"{T_MOD}:_border_inside", want,
            args=[7, 7], select=f"{i}.{j}", kind="exact"))

    # --- 坡度角换算扩展（TWI/SPI/LS 的 tanβ 输入公式核心）----------------
    pct2 = [5.0, 12.5, 33.3, 150.0]
    ref_pct2 = np.arctan(np.asarray(pct2) / 100.0)
    for i in (1, 3):
        cases.append(case(
            f"slope_to_radians_pct2_{i}", f"{T_MOD}:_slope_to_radians",
            r9(float(ref_pct2[i])), args=[pct2, "percent"],
            select=str(i), rtol=1e-12))
    deg2 = [15.0, 75.0, 89.0]
    for i in (0, 2):
        cases.append(case(
            f"slope_to_radians_deg2_{i}", f"{T_MOD}:_slope_to_radians",
            r9(float(np.radians(deg2[i]))), args=[deg2, "degrees"],
            select=str(i), rtol=1e-12))
    pct3 = [0.5, 200.0]
    for i in range(2):
        cases.append(case(
            f"slope_to_radians_pct3_{i}", f"{T_MOD}:_slope_to_radians",
            r9(float(np.arctan(pct3[i] / 100.0))), args=[pct3, "percent"],
            select=str(i), rtol=1e-12))
    deg3 = [1.0, 89.9]
    for i in range(2):
        cases.append(case(
            f"slope_to_radians_deg3_{i}", f"{T_MOD}:_slope_to_radians",
            r9(float(np.radians(deg3[i]))), args=[deg3, "degrees"],
            select=str(i), rtol=1e-12))
    deg4 = [2.0, 88.0]
    for i in range(2):
        cases.append(case(
            f"slope_to_radians_deg4_{i}", f"{T_MOD}:_slope_to_radians",
            r9(float(np.radians(deg4[i]))), args=[deg4, "degrees"],
            select=str(i), rtol=1e-12))
    pct4 = [7.5, 125.0]
    for i in range(2):
        cases.append(case(
            f"slope_to_radians_pct4_{i}", f"{T_MOD}:_slope_to_radians",
            r9(float(np.arctan(pct4[i] / 100.0))), args=[pct4, "percent"],
            select=str(i), rtol=1e-12))

    # --- 方位/半径护栏基元 ------------------------------------------------
    cases.append(case("validate_azimuth_list_ok", f"{T_MOD}:_validate_azimuth_list",
                      [0.0, 45.0, 90.0], args=[[0, 45.0, 90]], kind="exact"))
    cases.append(case("validate_azimuth_list_single",
                      f"{T_MOD}:_validate_azimuth_list", [315.0],
                      args=[[315.0]], kind="exact"))
    cases.append(case("validate_azimuth_list_coerce",
                      f"{T_MOD}:_validate_azimuth_list", [45.0, 359.99],
                      args=[["45", 359.99]], kind="exact",
                      note="字符串数字 coerce 后罗盘度校验"))
    cases.append(case("guard_cells_custom_cap_pass", f"{T_MOD}:_guard_cells",
                      None, args=[[100, 100], "terrain.test", 1000000],
                      kind="exact", note="注入上限内 → 放行"))
    cases.append(error_case("guard_cells_custom_cap_reject",
                            f"{T_MOD}:_guard_cells",
                            "RESOURCE_SCALE_MISMATCH",
                            args=[[1000, 1000], "terrain.test", 500000]))
    for name, bad in (("empty", []), ("high", [360.0]), ("neg", [-5.0]),
                      ("nan", ["nan"])):
        cases.append(error_case(
            f"validate_azimuth_rejects_{name}", f"{T_MOD}:_validate_azimuth_list",
            "ValueError", args=[bad],
            note="nan 用字符串传入（float('nan') 后被 isfinite 守卫拒绝）"))
    cases.append(case("validate_horizon_radius_min",
                      f"{T_MOD}:_validate_horizon_radius", 1,
                      args=[1, "terrain.horizon_angle"], kind="exact"))
    cases.append(case("validate_horizon_radius_max",
                      f"{T_MOD}:_validate_horizon_radius", 100,
                      args=[100, "terrain.sky_view_factor"], kind="exact"))
    cases.append(error_case("validate_horizon_radius_zero",
                            f"{T_MOD}:_validate_horizon_radius", "ValueError",
                            args=[0, "terrain.horizon_angle"]))
    cases.append(error_case("validate_horizon_radius_over",
                            f"{T_MOD}:_validate_horizon_radius",
                            "RESOURCE_SCALE_MISMATCH",
                            args=[101, "terrain.horizon_angle"]))
    cases.append(case("guard_cells_within_limit", f"{T_MOD}:_guard_cells",
                      None, args=[[100, 100], "terrain.sink_fill"],
                      kind="exact", note="护栏内 → 静默放行（返回 None）"))
    cases.append(error_case("guard_cells_over_limit", f"{T_MOD}:_guard_cells",
                            "RESOURCE_SCALE_MISMATCH",
                            args=[[10000, 10000], "terrain.sink_fill"]))

    # --- 公开入口：类型化错误语义回归（(result, meta) 元组面不可数值 select）──
    z22 = [[1.0, 2.0], [3.0, 4.0]]
    z33 = [[5.0, 5.0, 5.0], [5.0, 5.0, 5.0], [5.0, 5.0, 5.0]]
    z_flat_const = [[3.0] * 6 for _ in range(6)]
    nodata_grid = [[-9999.0, -9999.0], [-9999.0, -9999.0]]
    cases.append(error_case("tpi_all_nodata", f"{T_MOD}:topographic_position_index",
                            "NO_VALID_OBSERVATIONS",
                            args=[nodata_grid, 3], kwargs={"nodata": -9999.0},
                            note="全 nodata 掩膜后无有效像元"))
    cases.append(error_case("tpi_1d_input", f"{T_MOD}:topographic_position_index",
                            "NO_VALID_OBSERVATIONS", args=[[1.0, 2.0, 3.0], 3]))
    cases.append(error_case("tpi_window_even", f"{T_MOD}:topographic_position_index",
                            "ValueError", args=[z22, 4]))
    cases.append(error_case("roughness_tiny_grid", f"{T_MOD}:roughness",
                            "NO_VALID_OBSERVATIONS", args=[[[1.0, 2.0]], 3]))
    cases.append(error_case("curvature_bad_cellsize", f"{T_MOD}:surface_curvature",
                            "ValueError", args=[z22, 0.0]))
    cases.append(error_case("curvature_bad_cellsize_x", f"{T_MOD}:surface_curvature",
                            "ValueError", args=[z22, 2.0], kwargs={"cell_size_x": -1.0}))
    cases.append(error_case("curvature_all_nodata", f"{T_MOD}:surface_curvature",
                            "NO_VALID_OBSERVATIONS",
                            args=[nodata_grid, 1.0], kwargs={"nodata": -9999.0}))
    cases.append(error_case("d8_bad_cellsize", f"{T_MOD}:d8_flow",
                            "ValueError", args=[z22, -2.0]))
    cases.append(error_case("d8_bad_flat_routing", f"{T_MOD}:d8_flow",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"flat_routing": "magic"}))
    cases.append(error_case("d8_zero_epsilon", f"{T_MOD}:d8_flow",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"flat_routing": "epsilon",
                                    "flat_epsilon": 0.0}))
    cases.append(error_case("d8_all_nodata", f"{T_MOD}:d8_flow",
                            "NO_VALID_OBSERVATIONS",
                            args=[nodata_grid, 1.0], kwargs={"nodata": -9999.0}))
    cases.append(error_case("fill_negative_epsilon", f"{T_MOD}:fill_depressions",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"epsilon": -0.1}))
    cases.append(error_case("fill_1d_input", f"{T_MOD}:fill_depressions",
                            "NO_VALID_OBSERVATIONS", args=[[1.0, 2.0], 1.0]))
    cases.append(error_case("dinf_1d_input", f"{T_MOD}:dinf_flow_direction",
                            "NO_VALID_OBSERVATIONS", args=[[1.0, 2.0], 1.0]))
    cases.append(error_case("dinf_bad_cellsize", f"{T_MOD}:dinf_flow_direction",
                            "ValueError", args=[z22, 0.0]))
    cases.append(error_case("flow_length_bad_mode", f"{T_MOD}:flow_length",
                            "ValueError", args=[{}],
                            kwargs={"mode": "lateral", "cell_size": 1.0},
                            note="mode 契约校验先于任何网格访问"))
    cases.append(error_case("streams_bad_threshold", f"{T_MOD}:extract_streams",
                            "ValueError", args=[[[1.0]], 0.5]))
    cases.append(error_case("streams_1d_input", f"{T_MOD}:extract_streams",
                            "NO_VALID_OBSERVATIONS", args=[[1.0, 2.0], 1.0]))
    cases.append(error_case("twi_shape_mismatch", f"{T_MOD}:topographic_wetness_index",
                            "ValueError",
                            args=[[[10.0, 20.0]], [[1.0, 2.0], [3.0, 4.0]], 1.0]))
    cases.append(error_case("twi_bad_slope_units",
                            f"{T_MOD}:topographic_wetness_index",
                            "ValueError", args=[[[10.0]], [[1.0]], 1.0],
                            kwargs={"slope_units": "radians2"}))
    cases.append(error_case("spi_shape_mismatch", f"{T_MOD}:stream_power_index",
                            "ValueError",
                            args=[[[10.0]], [[1.0], [2.0]], 1.0]))
    cases.append(error_case("ls_bad_method", f"{T_MOD}:ls_factor",
                            "ValueError", args=[[[5.0]]],
                            kwargs={"method": "renard"}))
    cases.append(error_case("ls_desmet_requires_accum", f"{T_MOD}:ls_factor",
                            "ValueError", args=[[[5.0]]],
                            kwargs={"method": "desmet_govers"}))
    cases.append(error_case("openness_zero_radius", f"{T_MOD}:terrain_openness",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"radius_cells": 0}))
    cases.append(error_case("openness_radius_over_cap", f"{T_MOD}:terrain_openness",
                            "RESOURCE_SCALE_MISMATCH", args=[z33, 1.0],
                            kwargs={"radius_cells": 101}))
    cases.append(error_case("openness_few_azimuths", f"{T_MOD}:terrain_openness",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"azimuth_count": 3}))
    cases.append(error_case("openness_many_azimuths", f"{T_MOD}:terrain_openness",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"azimuth_count": 65}))
    cases.append(error_case("geomorphon_zero_lookup", f"{T_MOD}:geomorphons",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"lookup_radius_cells": 0}))
    cases.append(error_case("geomorphon_lookup_over_cap", f"{T_MOD}:geomorphons",
                            "RESOURCE_SCALE_MISMATCH", args=[z33, 1.0],
                            kwargs={"lookup_radius_cells": 129}))
    cases.append(error_case("geomorphon_far_ge_lookup", f"{T_MOD}:geomorphons",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"lookup_radius_cells": 4, "far": 4.0}))
    cases.append(error_case("geomorphon_negative_flatten", f"{T_MOD}:geomorphons",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"flatten": -1.0}))
    cases.append(error_case("landform_constant_dem", f"{T_MOD}:landform_classification",
                            "DEGENERATE_DATA", args=[z_flat_const, 1.0]))
    cases.append(error_case("landform_bad_tolerance",
                            f"{T_MOD}:landform_classification",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"elevation_tolerance": 0.6}))
    cases.append(error_case("landform_window_order",
                            f"{T_MOD}:landform_classification",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"tpi_window_small": 7, "tpi_window_large": 5}))
    cases.append(error_case("hillshade_zero_altitude", f"{T_MOD}:hillshade_multiazimuth",
                            "ValueError", args=[z22, 1.0], kwargs={"altitude": 0.0}))
    cases.append(error_case("hillshade_high_altitude",
                            f"{T_MOD}:hillshade_multiazimuth",
                            "ValueError", args=[z22, 1.0], kwargs={"altitude": 95.0}))
    cases.append(error_case("hillshade_bad_combine", f"{T_MOD}:hillshade_multiazimuth",
                            "ValueError", args=[z22, 1.0], kwargs={"combine": "max"}))
    cases.append(error_case("hillshade_no_azimuths",
                            f"{T_MOD}:hillshade_multiazimuth",
                            "ValueError", args=[z22, 1.0], kwargs={"azimuths": []}))
    cases.append(error_case("horizon_radius_over_cap", f"{T_MOD}:horizon_angle",
                            "RESOURCE_SCALE_MISMATCH", args=[z22, 1.0],
                            kwargs={"max_search_radius": 101}))
    cases.append(error_case("horizon_azimuth_out_of_range", f"{T_MOD}:horizon_angle",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"azimuths": [0.0, 360.0]}))
    cases.append(error_case("horizon_no_azimuths", f"{T_MOD}:horizon_angle",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"azimuths": []}))
    cases.append(error_case("horizon_too_many_azimuths", f"{T_MOD}:horizon_angle",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"azimuths": [0.0] * 65}))
    cases.append(error_case("svf_few_azimuths", f"{T_MOD}:sky_view_factor",
                            "ValueError", args=[z22, 1.0], kwargs={"n_azimuths": 3}))
    cases.append(error_case("svf_many_azimuths", f"{T_MOD}:sky_view_factor",
                            "ValueError", args=[z22, 1.0], kwargs={"n_azimuths": 65}))
    cases.append(error_case("svf_zero_radius", f"{T_MOD}:sky_view_factor",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"max_search_radius": 0}))
    cases.append(error_case("viewshed_zero_max_distance", f"{T_MOD}:viewshed",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"observer": (0, 0), "max_distance": 0.0}))
    cases.append(error_case("viewshed_observer_outside", f"{T_MOD}:viewshed",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"observer": (9.0, 9.0)}))
    cases.append(error_case("viewshed_observer_nodata", f"{T_MOD}:viewshed",
                            "NO_VALID_OBSERVATIONS", args=[z22, 1.0],
                            kwargs={"observer": (0, 0), "nodata": 1.0}))
    cases.append(error_case("contours_1d_input", f"{T_MOD}:extract_contours",
                            "NO_VALID_OBSERVATIONS", args=[[1.0, 2.0, 3.0]]))
    cases.append(error_case("validate_cell_sizes_zero", f"{T_MOD}:_validate_cell_sizes",
                            "ValueError", args=[0.0, None]))
    # 注：upstream_watershed / stream_order / flow_accumulation / flow_length
    # 的主体需要 d8_flow 结果 dict（ndarray 值 .shape）—— JSON 回放的 list
    # 入参先 AttributeError，语义错误面无法入 corpus（运行器局限）。

    # --- 公开入口护栏扩展扫描（全部 asarray-first，list 可回放）----------
    cases.append(error_case("tpi_window_102", f"{T_MOD}:topographic_position_index",
                            "ValueError", args=[z22, 102]))
    cases.append(error_case("tpi_window_str", f"{T_MOD}:topographic_position_index",
                            "ValueError", args=[z22, "abc"]))
    cases.append(error_case("roughness_window_str", f"{T_MOD}:roughness",
                            "ValueError", args=[z22, "abc"]))
    cases.append(error_case("tri_all_nodata", f"{T_MOD}:terrain_ruggedness_index",
                            "NO_VALID_OBSERVATIONS", args=[nodata_grid],
                            kwargs={"nodata": -9999.0}))
    cases.append(error_case("tri_1d_input", f"{T_MOD}:terrain_ruggedness_index",
                            "NO_VALID_OBSERVATIONS", args=[[1.0, 2.0, 3.0]]))
    cases.append(error_case("fill_single_cell_grid", f"{T_MOD}:fill_depressions",
                            "NO_VALID_OBSERVATIONS", args=[[[1.0]], 1.0]))
    cases.append(error_case("d8_cellsize_x_zero", f"{T_MOD}:d8_flow",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"cell_size_x": 0.0}))
    cases.append(error_case("dinf_all_nodata", f"{T_MOD}:dinf_flow_direction",
                            "NO_VALID_OBSERVATIONS", args=[nodata_grid, 1.0],
                            kwargs={"nodata": -9999.0}))
    cases.append(error_case("contours_all_nodata", f"{T_MOD}:extract_contours",
                            "NO_VALID_OBSERVATIONS", args=[nodata_grid],
                            kwargs={"nodata": -9999.0}))
    cases.append(error_case("hillshade_1d_input", f"{T_MOD}:hillshade_multiazimuth",
                            "NO_VALID_OBSERVATIONS", args=[[1.0, 2.0], 1.0],
                            note="1D 网格在 _prepare 处类型化拒绝"))
    cases.append(error_case("viewshed_negative_observer_height",
                            f"{T_MOD}:viewshed", "ValueError",
                            args=[z22, 1.0],
                            kwargs={"observer": (0, 0), "observer_height": -1.0}))
    cases.append(error_case("viewshed_negative_target_height",
                            f"{T_MOD}:viewshed", "ValueError",
                            args=[z22, 1.0],
                            kwargs={"observer": (0, 0), "target_height": -0.5}))
    cases.append(error_case("viewshed_zero_cellsize", f"{T_MOD}:viewshed",
                            "ValueError", args=[z22, 0.0],
                            kwargs={"observer": (0, 0)}))
    cases.append(error_case("viewshed_xy_needs_transform", f"{T_MOD}:viewshed",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"observer_xy": (1.0, 1.0)}))
    cases.append(error_case("openness_radius_str", f"{T_MOD}:terrain_openness",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"radius_cells": "abc"}))
    cases.append(error_case("geomorphon_negative_far", f"{T_MOD}:geomorphons",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"lookup_radius_cells": 4, "far": -1.0}))
    cases.append(error_case("landform_window_even", f"{T_MOD}:landform_classification",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"tpi_window_small": 4}))
    cases.append(error_case("horizon_radius_str", f"{T_MOD}:horizon_angle",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"max_search_radius": "abc"}))
    cases.append(error_case("svf_radius_bool", f"{T_MOD}:sky_view_factor",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"max_search_radius": True}))
    cases.append(error_case("ls_bad_slope_units", f"{T_MOD}:ls_factor",
                            "ValueError", args=[[[5.0]]],
                            kwargs={"slope_units": "dms"}))
    cases.append(error_case("twi_ls_cellsize_x_zero",
                            f"{T_MOD}:topographic_wetness_index",
                            "ValueError",
                            args=[[[10.0, 20.0]], [[1.0, 2.0]], 1.0],
                            kwargs={"cell_size_x": 0.0}))
    cases.append(error_case("dinf_cellsize_x_zero", f"{T_MOD}:dinf_flow_direction",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"cell_size_x": 0.0}))
    cases.append(error_case("fill_all_nodata", f"{T_MOD}:fill_depressions",
                            "NO_VALID_OBSERVATIONS", args=[nodata_grid, 1.0],
                            kwargs={"nodata": -9999.0}))
    cases.append(error_case("openness_radius_negative", f"{T_MOD}:terrain_openness",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"radius_cells": -2}))
    cases.append(error_case("openness_azimuth_bool", f"{T_MOD}:terrain_openness",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"azimuth_count": True}))
    cases.append(error_case("geomorphon_lookup_str", f"{T_MOD}:geomorphons",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"lookup_radius_cells": "abc"}))
    cases.append(error_case("landform_tolerance_negative",
                            f"{T_MOD}:landform_classification",
                            "ValueError", args=[z33, 1.0],
                            kwargs={"elevation_tolerance": -0.1}))
    cases.append(error_case("horizon_azimuth_str", f"{T_MOD}:horizon_angle",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"azimuths": ["abc"]}))
    cases.append(error_case("svf_n_azimuths_str", f"{T_MOD}:sky_view_factor",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"n_azimuths": "abc"}))
    cases.append(error_case("tpi_window_bool", f"{T_MOD}:topographic_position_index",
                            "ValueError", args=[z22, True]))
    cases.append(error_case("curvature_cellsize_none", f"{T_MOD}:surface_curvature",
                            "ValueError", args=[z22, None]))
    cases.append(error_case("d8_negative_epsilon", f"{T_MOD}:d8_flow",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"flat_routing": "epsilon",
                                    "flat_epsilon": -1e-6}))
    cases.append(error_case("ls_flow_length_shape_mismatch", f"{T_MOD}:ls_factor",
                            "ValueError", args=[[[1.0, 2.0], [3.0, 4.0]]],
                            kwargs={"method": "mccool",
                                    "flow_length_m": [[1.0]]}))
    cases.append(error_case("viewshed_negative_max_distance", f"{T_MOD}:viewshed",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"observer": (0, 0), "max_distance": -5.0}))
    cases.append(error_case("svf_n_azimuths_bool", f"{T_MOD}:sky_view_factor",
                            "ValueError", args=[z22, 1.0],
                            kwargs={"n_azimuths": True}))

    return cases


# ── 域：network（网络分析 · JSON 可回放纯函数面）─────────────────────
#
# 运行器限制（重要，详见最终报告）：routing / od_matrix / centrality /
# interaction / accessibility / facility / service_area 的**服务方法**
# 都要求 nx.DiGraph + NetworkDataset + pydantic 模型入参，且返回
# Route / ODPair / CentralityResult / GravityAccessResult 等 pydantic
# 模型 —— JSON args 传不进、pydantic 出不来（select 不可下钻）。
# 本域覆盖 JSON 可回放的纯函数面：
#   - graph_builder 的 haversine / linestring 长度（大地测量闭式）；
#   - allocation 的精确 MILP（HiGHS）p-median / p-center：目标值 vs
#     itertools.combinations 独立枚举参考 + 模型形状不变量；
#   - accessibility 的 E2SFCA 高斯衰减带权重/带索引（Luo & Qi 2009 闭式）；
#   - centrality / interaction / service_area / geo_analysis.network 的
#     参数契约与决策表基元。
# OD 矩阵不变量 / 最短路 vs networkx / gravity-huff 双循环参考 / 度·接近·
# 介数 vs networkx 在生成侧全部可行，但无法经 JSON 回放 —— 见报告。

N_CAL = "app.services.network.allocation"
N_GB = "app.services.network.graph_builder"
N_ACC = "app.services.network.accessibility"
N_CEN = "app.services.network.centrality"
N_INT = "app.services.network.interaction"
N_SA = "app.services.network.service_area"


def _ref_haversine(p1, p2):
    """Haversine 大地距离独立参考（asin 形式；R = 6371 km）。"""
    lng1, lat1 = p1
    lng2, lat2 = p2
    if abs(lng1 - lng2) < 1e-9 and abs(lat1 - lat2) < 1e-9:
        return 0.0
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = (math.sin(dphi / 2.0) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2)
    return r * 2.0 * math.asin(min(1.0, math.sqrt(a)))


def _brute_force_p_median(cost, weights, p):
    """p-median 目标 min Σ w_i·min_j C_ij 的独立枚举参考（inf 惩罚 1e9）。"""
    n, m = len(cost), len(cost[0])
    best = float("inf")
    for combo in itertools.combinations(range(m), p):
        total = 0.0
        for i in range(n):
            min_c = min(cost[i][j] for j in combo)
            total += (1e9 if min_c == float("inf") else min_c) * weights[i]
        best = min(best, total)
    return best


def _brute_force_p_center(cost, weights, p):
    """p-center 目标 min max_i(min_j C_ij)（可达需求）的独立枚举参考。"""
    n, m = len(cost), len(cost[0])
    best = float("inf")
    for combo in itertools.combinations(range(m), p):
        max_c = 0.0
        for i in range(n):
            min_c = min(cost[i][j] for j in combo)
            if min_c != float("inf"):
                max_c = max(max_c, min_c)
        best = min(best, max_c)
    return best


def build_network() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []

    # --- haversine 大地距离（独立 asin 形式参考）-------------------------
    pairs = [
        ((116.4074, 39.9042), (121.4737, 31.2304)),    # 北京→上海
        ((0.0, 0.0), (1.0, 0.0)),                      # 赤道 1° 经度
        ((0.0, 0.0), (0.0, 1.0)),                      # 1° 纬度
        ((-0.1, 51.5), (0.05, 51.52)),                 # 伦敦短距
        ((179.5, 10.0), (-179.5, 10.0)),               # 反经线 1° 经度
        ((151.2093, -33.8688), (153.0260, -27.4705)),  # 悉尼→布里斯班
        ((13.4050, 52.5200), (13.4050, 52.5201)),      # ~11 m 微小位移
    ]
    for i, (a, b) in enumerate(pairs):
        cases.append(case(
            f"haversine_pair_{i}", f"{N_GB}:haversine_distance",
            r9(_ref_haversine(a, b)), args=[list(a), list(b)], rtol=1e-9,
            note="haversine asin 形式独立参考（R=6371 km）"))
    rng_hv = np.random.default_rng(402)
    for i in range(14):
        pa = tuple(float(v) for v in rng_hv.uniform(-60.0, 60.0, 2))
        pb = tuple(float(v) for v in rng_hv.uniform(-60.0, 60.0, 2))
        cases.append(case(
            f"haversine_seeded_{i}", f"{N_GB}:haversine_distance",
            r9(_ref_haversine(pa, pb)), args=[list(pa), list(pb)],
            rtol=1e-9, note="种子随机点对 asin 形式独立参考"))
    same = [116.4074, 39.9042]
    cases.append(case("haversine_same_point_zero", f"{N_GB}:haversine_distance",
                      0.0, args=[same, same], kind="exact",
                      note="同点 → 精确 0（快路径）"))
    a0, b0 = pairs[0]
    cases.append(case("haversine_symmetric_ab", f"{N_GB}:haversine_distance",
                      r9(_ref_haversine(a0, b0)), args=[list(a0), list(b0)],
                      rtol=1e-9))
    cases.append(case("haversine_symmetric_ba", f"{N_GB}:haversine_distance",
                      r9(_ref_haversine(a0, b0)), args=[list(b0), list(a0)],
                      rtol=1e-9, note="h(a,b) == h(b,a)（对称性不变量）"))

    # --- linestring 长度（逐段 haversine 和的独立循环参考）----------------
    rng_nw = np.random.default_rng(401)
    path5 = [(float(x), float(y))
             for x, y in rng_nw.uniform(0.5, 1.5, (5, 2))]
    ref_len5 = sum(_ref_haversine(path5[k], path5[k + 1]) for k in range(4))
    cases.append(case("linestring_length_5pt", f"{N_GB}:linestring_length_m",
                      r9(ref_len5), args=[path5], rtol=1e-9,
                      note="逐段 haversine 和独立复算"))
    tri = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    ref_tri = (_ref_haversine(tri[0], tri[1])
               + _ref_haversine(tri[1], tri[2]))
    cases.append(case("linestring_length_triangle", f"{N_GB}:linestring_length_m",
                      r9(ref_tri), args=[tri], rtol=1e-9))
    cases.append(case("linestring_length_degenerate_zero",
                      f"{N_GB}:linestring_length_m", 0.0,
                      args=[[(2.0, 2.0), (2.0, 2.0)]], kind="exact",
                      note="零长度边（重合端点）→ 0"))
    cases.append(case("linestring_length_reversed_equal",
                      f"{N_GB}:linestring_length_m", r9(ref_tri),
                      args=[list(reversed(tri))], rtol=1e-12,
                      note="反向折线长度不变"))
    cases.append(case("linestring_length_single_point",
                      f"{N_GB}:linestring_length_m", 0.0,
                      args=[[(10.0, 10.0)]], kind="exact"))
    path8 = [(float(x), float(y))
             for x, y in np.column_stack([
                 np.linspace(-73.9, -74.0, 8), np.linspace(40.7, 40.8, 8)])]
    ref_len8 = sum(_ref_haversine(path8[k], path8[k + 1]) for k in range(7))
    cases.append(case("linestring_length_8pt", f"{N_GB}:linestring_length_m",
                      r9(ref_len8), args=[path8], rtol=1e-9,
                      note="8 点折线逐段 haversine 和独立复算"))
    pair2 = [(0.0, 0.0), (0.0, 2.0)]
    cases.append(case("linestring_length_pair", f"{N_GB}:linestring_length_m",
                      r9(_ref_haversine(pair2[0], pair2[1])),
                      args=[pair2], rtol=1e-9))

    # --- p-median 精确 MILP：目标值 vs 枚举参考 + 模型形状不变量 ----------
    milp_fixtures = {
        "dense4x4": ([[1.0, 2.0, 3.0, 4.0], [2.0, 1.0, 5.0, 3.0],
                      [4.0, 3.0, 1.0, 2.0], [1.0, 1.0, 1.0, 1.0]],
                     [1.0, 2.0, 1.0, 1.0], 2),
        "wide6x3": ([[2.0, 4.0, 6.0], [3.0, 1.0, 5.0], [8.0, 6.0, 2.0],
                     [1.0, 4.0, 4.0], [5.0, 2.0, 3.0], [2.0, 2.0, 7.0]],
                    [1.0, 1.5, 2.0, 0.5, 1.0, 1.0], 2),
        "tall5x5": ([[1.0 + abs(i - j) * 0.7 for j in range(5)]
                     for i in range(5)],
                    [1.0, 1.0, 1.0, 1.0, 1.0], 3),
        "expensive_row": ([[1.0, 2.0], [1.0e7, 3.0], [2.0, 1.0],
                           [1.0e7, 2.0e7]],
                          [1.0, 2.0, 1.0, 3.0], 1),
        "single_site": ([[4.0], [2.0], [6.0]], [1.0, 1.0, 1.0], 1),
        "tall6x6": ([[1.0 + ((i * 3 + j * 7) % 11) * 0.5 for j in range(6)]
                     for i in range(6)],
                    [1.0, 2.0, 1.0, 0.5, 1.5, 1.0], 2),
        "corridor8x2": ([[2.0, 9.0], [1.0, 8.0], [3.0, 1.0], [9.0, 2.0],
                         [4.0, 7.0], [6.0, 3.0], [1.5, 9.5], [8.5, 1.5]],
                        [1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 1.0, 1.0], 2),
    }
    for name, (cost, weights, p) in milp_fixtures.items():
        ref_obj = _brute_force_p_median(cost, weights, p)
        m_fac = len(cost[0])
        n_pairs = int(np.isfinite(np.asarray(cost, dtype=float)).sum())
        cases.append(case(
            f"p_median_milp_objective_{name}", f"{N_CAL}:solve_p_median_milp",
            r9(ref_obj), args=[cost, weights, p],
            select="objective_value", rtol=1e-6,
            note="C(m,p) 枚举独立参考（inf→1e9 惩罚同语义）"))
        cases.append(case(
            f"p_median_milp_status_{name}", f"{N_CAL}:solve_p_median_milp",
            "optimal", args=[cost, weights, p], select="optimality",
            kind="exact", note="HiGHS 最优性状态"))
        cases.append(case(
            f"p_median_milp_model_shape_{name}", f"{N_CAL}:solve_p_median_milp",
            m_fac + n_pairs, args=[cost, weights, p],
            select="model_stats.n_variables",
            kind="exact", note="变量数 = 候选数 + 有限可达对数（独立计数）"))
    # 注：不可达（inf）代价值矩阵无法进 JSON（allow_nan=False 拒绝 inf）——
    # unassigned_demand_indices 语义不可经 JSON 回放（运行器局限，见报告）。

    # --- p-center 精确 MILP：目标值 vs 枚举 min-max 参考 ------------------
    for name in ("dense4x4", "wide6x3", "expensive_row"):
        cost, weights, p = milp_fixtures[name]
        ref = _brute_force_p_center(cost, weights, p)
        cases.append(case(
            f"p_center_milp_objective_{name}", f"{N_CAL}:solve_p_center_milp",
            r9(ref), args=[cost, weights, p], select="objective_value",
            rtol=1e-6, note="min-max 服务成本枚举独立参考"))
    cost_u, weights_u, p_u = milp_fixtures["expensive_row"]
    big_m_ref = max(c for row in cost_u for c in row if math.isfinite(c))
    cases.append(case(
        "p_center_milp_big_m", f"{N_CAL}:solve_p_center_milp",
        r9(big_m_ref), args=[cost_u, weights_u, p_u], select="big_m",
        rtol=1e-9, note="Big-M = 最大有限代价（独立复算）"))
    cases.append(case(
        "p_center_milp_total_weighted", f"{N_CAL}:solve_p_center_milp",
        r9(_brute_force_p_median(cost_u, weights_u, p_u)),
        args=[cost_u, weights_u, p_u], select="total_weighted_cost",
        rtol=1e-6, note="总加权成本次级判据 = p-median 目标（同语义重算）"))

    # --- 枚举计数 / 规模闸 / 输入校验 -------------------------------------
    for m_, p_, want in ((5, 2, 10), (6, 3, 20), (10, 3, 120), (15, 4, 1365),
                         (7, 7, 1), (5, 0, 1), (3, 5, 0), (4, -1, 0)):
        cases.append(case(
            f"exact_combination_count_{m_}_{p_}",
            f"{N_CAL}:_exact_combination_count", want, args=[m_, p_],
            kind="exact", note="math.comb 语义（C(5,0)=1；p>m 或 p<0 → 0）"))
    cases.append(case("milp_scale_guard_pass", f"{N_CAL}:_milp_scale_guard",
                      None, args=[10, 10], kind="exact",
                      note="规模闸内 → 静默放行"))
    cases.append(case("milp_scale_guard_boundary", f"{N_CAL}:_milp_scale_guard",
                      None, args=[50, 500], kind="exact",
                      note="候选=500 且积=25000 恰在闸上"))
    cases.append(error_case("milp_scale_guard_candidates",
                            f"{N_CAL}:_milp_scale_guard",
                            "RESOURCE_SCALE_MISMATCH", args=[10, 501]))
    cases.append(error_case("milp_scale_guard_product",
                            f"{N_CAL}:_milp_scale_guard",
                            "RESOURCE_SCALE_MISMATCH", args=[60, 500]))
    cases.append(error_case("milp_inputs_empty_matrix",
                            f"{N_CAL}:_validate_milp_inputs", "ValueError",
                            args=[[], [1.0], 1]))
    cases.append(error_case("milp_inputs_empty_weights",
                            f"{N_CAL}:_validate_milp_inputs", "ValueError",
                            args=[[[1.0]], [], 1]))
    cases.append(error_case("milp_inputs_ragged",
                            f"{N_CAL}:_validate_milp_inputs", "ValueError",
                            args=[[[1.0, 2.0], [1.0]], [1.0, 1.0], 1]))
    cases.append(error_case("milp_inputs_row_mismatch",
                            f"{N_CAL}:_validate_milp_inputs", "ValueError",
                            args=[[[1.0, 2.0]], [1.0, 1.0, 1.0], 1]))
    cases.append(error_case("milp_inputs_p_zero",
                            f"{N_CAL}:_validate_milp_inputs", "ValueError",
                            args=[[[1.0, 2.0]], [1.0, 1.0], 0]))
    cases.append(error_case("milp_inputs_p_over_m",
                            f"{N_CAL}:_validate_milp_inputs", "ValueError",
                            args=[[[1.0, 2.0]], [1.0, 1.0], 3]))

    # --- E2SFCA 高斯衰减带（Luo & Qi 2009 闭式 w_r = exp(−0.5(r+0.5)²)）---
    for zones in (1, 2, 3, 4, 5, 6):
        w_ref = [math.exp(-0.5 * ((r + 0.5) ** 2)) for r in range(zones)]
        cases.append(case(
            f"e2sfca_weights_z{zones}_first",
            f"{N_ACC}:NetworkAccessibilityService._e2sfca_zone_weights",
            r9(w_ref[0]), args=[15.0, zones], select="0", rtol=1e-9,
            note="带中点高斯权重闭式复算"))
        if zones >= 3:
            cases.append(case(
                f"e2sfca_weights_z{zones}_second",
                f"{N_ACC}:NetworkAccessibilityService._e2sfca_zone_weights",
                r9(w_ref[1]), args=[15.0, zones], select="1", rtol=1e-9))
    for zones in (2, 4, 5, 7, 8, 9, 10):
        w_ref = [math.exp(-0.5 * ((r + 0.5) ** 2)) for r in range(zones)]
        cases.append(case(
            f"e2sfca_weights_z{zones}_first",
            f"{N_ACC}:NetworkAccessibilityService._e2sfca_zone_weights",
            r9(w_ref[0]), args=[30.0, zones], select="0", rtol=1e-9))
        if zones >= 3:
            cases.append(case(
                f"e2sfca_weights_z{zones}_third",
                f"{N_ACC}:NetworkAccessibilityService._e2sfca_zone_weights",
                r9(w_ref[2]), args=[30.0, zones], select="2", rtol=1e-9,
                note="cutoff 不进权重公式（权重只依赖带序 r）"))
        cases.append(case(
            f"e2sfca_weights_z{zones}_last",
            f"{N_ACC}:NetworkAccessibilityService._e2sfca_zone_weights",
            r9(w_ref[-1]), args=[30.0, zones], select=str(zones - 1),
            rtol=1e-9))
    cases.append(error_case("e2sfca_zones_zero",
                            f"{N_ACC}:NetworkAccessibilityService"
                            "._e2sfca_zone_weights", "ValueError",
                            args=[15.0, 0]))
    cases.append(error_case("e2sfca_zones_eleven",
                            f"{N_ACC}:NetworkAccessibilityService"
                            "._e2sfca_zone_weights", "ValueError",
                            args=[15.0, 11]))
    band_cases = [(5.0, 15.0, 3, 1), (0.01, 15.0, 3, 0), (15.0, 15.0, 3, 2),
                  (4.999, 15.0, 3, 0), (10.0, 15.0, 3, 2), (7.4, 15.0, 4, 1),
                  (20.0, 30.0, 4, 2), (29.99, 30.0, 4, 3),
                  (1.0, 15.0, 5, 0), (14.9, 15.0, 2, 1), (0.0, 15.0, 3, 0),
                  (2.0, 15.0, 5, 0), (11.0, 12.0, 2, 1)]
    for i, (t, cutoff, zones, want) in enumerate(band_cases):
        cases.append(case(
            f"e2sfca_band_index_{i}",
            f"{N_ACC}:NetworkAccessibilityService._band_index",
            want, args=[t, cutoff, zones], kind="exact",
            note="band = min(⌊t/带宽⌋, zones−1)；t==cutoff → 末带"))

    # --- 中心性指标词表 / 交互参数契约 / top-k 决策表 ----------------------
    for metrics, want in (
            ("all", ["degree", "closeness", "betweenness", "edge_betweenness"]),
            ("degree", ["degree"]), ("closeness", ["closeness"]),
            ("betweenness", ["betweenness"]),
            ("edge_betweenness", ["edge_betweenness"]),
            ("eigenvector", ["eigenvector"]), ("DEGREE", ["degree"])):
        cases.append(case(
            f"parse_metrics_{metrics.lower()}", f"{N_CEN}:_parse_metrics",
            want, args=[metrics], kind="exact"))
    cases.append(error_case("parse_metrics_unknown", f"{N_CEN}:_parse_metrics",
                            "UNSUPPORTED_METHOD", args=["pagerank"]))

    for name_, v, lo, hi in (
            ("alpha_mid", 1.0, 0.0, 3.0), ("alpha_lo", 0.0, 0.0, 3.0),
            ("alpha_hi", 3.0, 0.0, 3.0), ("beta_mid", 2.0, 0.5, 4.0),
            ("beta_lo", 0.5, 0.5, 4.0), ("beta_hi", 4.0, 0.5, 4.0)):
        cases.append(case(
            f"check_param_pass_{name_}", f"{N_INT}:_check_param", v,
            args=[name_, v, [lo, hi]], kind="exact",
            note="边界值闭区间放行（原值直通）"))
    for name_, v, lo, hi in (("alpha", -0.1, 0.0, 3.0),
                             ("alpha", 3.5, 0.0, 3.0),
                             ("beta", 0.4, 0.5, 4.0),
                             ("beta", 4.5, 0.5, 4.0)):
        cases.append(error_case(
            f"check_param_rejects_{name_}_"
            f"{str(v).replace('.', 'p').replace('-', 'neg')}",
            f"{N_INT}:_check_param", "ValueError", args=[name_, v, [lo, hi]]))

    top_fixtures = [
        ([["b", 0.3], ["a", 0.3], ["c", 0.4]], 3, ["c", "a", "b"],
         [0.4, 0.3, 0.3]),
        ([["x", 0.5], ["y", 0.5]], 1, ["x"], [0.5]),
        ([["p", 0.9]], 3, ["p"], [0.9]),
        ([["a", 1 / 3], ["b", 2 / 3]], 3, ["b", "a"],
         [round(2 / 3, 6), round(1 / 3, 6)]),
        ([], 3, [], []),
    ]
    for i, (pairs_in, k, want_ids, want_shares) in enumerate(top_fixtures):
        for pos, fid in enumerate(want_ids):
            cases.append(case(
                f"top_contributions_{i}_id{pos}", f"{N_INT}:_top_contributions",
                fid, args=[pairs_in, k],
                select=f"{pos}.facility_id", kind="exact",
                note="share 降序、id 升序平局裁决（确定性 top-k）"))
        for pos, sh in enumerate(want_shares):
            cases.append(case(
                f"top_contributions_{i}_share{pos}",
                f"{N_INT}:_top_contributions", sh, args=[pairs_in, k],
                select=f"{pos}.share", kind="exact"))

    # --- 服务区 break 单位换算 --------------------------------------------
    for raw, want in (("minutes", "minutes"), ("meters", "meters"),
                      ("seconds", "seconds"), ("km", "meters"),
                      (" Minutes ", "minutes")):
        cases.append(case(
            f"normalize_break_unit_{raw.strip().lower().replace(' ', '_')}",
            f"{N_SA}:_normalize_break_unit", want, args=[raw], kind="exact"))
    for bad in ("hours", "", "MIN"):
        tag = bad.strip().lower().replace(" ", "_") or "empty"
        cases.append(error_case(
            f"normalize_break_unit_rejects_{tag}",
            f"{N_SA}:_normalize_break_unit", "ValueError", args=[bad]))
    cutoff_cases = [(15.0, "minutes", 900.0), (5.0, "km", 5000.0),
                    (800.0, "meters", 800.0), (90.0, "seconds", 90.0),
                    (2.5, "minutes", 150.0), (12.5, "km", 12500.0),
                    (0.25, "minutes", 15.0), (0.75, "km", 750.0),
                    (1.0, "minutes", 60.0), (0.5, "meters", 0.5)]
    for i, (brk, unit, want) in enumerate(cutoff_cases):
        cases.append(case(
            f"break_to_cutoff_{i}", f"{N_SA}:_break_to_cutoff", want,
            args=[brk, unit, None], kind="exact",
            note="minutes→秒 ×60；km→米别名 ×1000；meters/seconds 直通"))

    # --- 模式巡航速度表（isochrone 距离预算）------------------------------
    for mode, want in (("walking", 80.0), ("cycling", 250.0),
                       ("driving", 667.0), ("transit", 417.0),
                       ("teleport", 667.0), ("WALKING", 667.0),
                       ("", 667.0)):
        cases.append(case(
            f"speed_m_per_min_{mode}",
            "app.lib.geo_analysis.network:_speed_m_per_min", want,
            args=[mode], kind="exact",
            note="模式速度表（大小写敏感；未知模式回退 driving）"))

    return cases


# ── 域：geostat（地统计 · 变异函数闭式 + 趋势/RBF/TIN 预测 + CV 不变量）──
#
# 元组/数据类返回面（empirical_variogram / ordinary_kriging /
# cross_validate_kriging / indicator_kriging / stratified_subsample …）
# 不可 select —— 数值 case 落在：_gamma 模型曲线闭式、Matérn 相关闭式、
# 各向异性矩阵、trend/rbf/tin 预测面（dict / ndarray 返回）、idw/trend/
# rbf/tin 的 LOOCV dict、H3 driver 的 metadata 不变量/锚。参考值全部
# 独立复算（闭式公式 / np.linalg.lstsq / scipy 直接调用 / 双循环）。

G_MOD = "app.lib.geo_analysis.kriging"
TS_MOD = "app.lib.geo_analysis.trend_surface"
RB_MOD = "app.lib.geo_analysis.rbf_interpolation"
TI_MOD = "app.lib.geo_analysis.tin_interpolation"
ID_MOD = "app.lib.geo_analysis.interpolation"


def _gamma_ref(model, h, sill, rng_, nugget, nu=0.5):
    """理论变异函数 γ(h) 的独立闭式参考（scipy.special 承担 matern）。"""
    from scipy.special import gammaln, kv
    h = np.asarray(h, dtype=float)
    if model == "spherical":
        out = np.full_like(h, nugget + sill)
        hr = np.divide(h, rng_, out=np.full_like(h, np.inf), where=rng_ > 0)
        inside = hr <= 1.0
        out[inside] = nugget + sill * (1.5 * hr[inside] - 0.5 * hr[inside] ** 3)
        return out
    if model == "exponential":
        return nugget + sill * (1.0 - np.exp(-3.0 * h / rng_))
    if model == "gaussian":
        return nugget + sill * (1.0 - np.exp(-3.0 * (h / rng_) ** 2))
    if model == "cubic":
        out = np.full_like(h, nugget + sill)
        x = h / rng_
        inside = x <= 1.0
        xi = x[inside]
        out[inside] = nugget + sill * (7.0 * xi ** 2 - 8.75 * xi ** 3
                                       + 3.5 * xi ** 5 - 0.75 * xi ** 7)
        return out
    if model == "wave":
        x = h / rng_
        ratio = np.where(np.abs(x) > 1e-12,
                         np.sin(np.where(np.abs(x) > 1e-12, x, 1.0))
                         / np.where(np.abs(x) > 1e-12, x, 1.0), 1.0)
        return nugget + sill * (1.0 - ratio)
    if model == "matern":
        x = h / rng_
        t0 = (2.0 ** (nu - 1.0)) * float(np.exp(gammaln(nu)))
        corr = np.where(x > 0,
                        np.power(np.where(x > 0, x, 1.0), nu)
                        * kv(nu, np.where(x > 0, x, 1.0)) / t0, 1.0)
        return nugget + sill * (1.0 - corr)
    raise ValueError(model)


def _anisotropy_matrix(angle_deg, ratio):
    """A = diag(1, ratio)·R(−θ) 的独立矩阵参考。"""
    t = math.radians(angle_deg)
    c, sn = math.cos(t), math.sin(t)
    return np.array([[c, sn], [-ratio * sn, ratio * c]])


def _unit_box_design_ref(xy, order):
    """单位盒缩放 + 单项式设计矩阵（文档化 billed 顺序）的独立复算。"""
    xy = np.asarray(xy, dtype=float)
    mn = xy.min(axis=0)
    span = xy.max(axis=0) - mn
    uv = (xy - mn) / span
    u, v = uv[:, 0], uv[:, 1]
    cols = [np.ones(len(xy))]
    for total in range(1, order + 1):
        for i in range(total + 1):
            cols.append(u ** (total - i) * v ** i)
    return np.column_stack(cols)


def _ref_idw_loocv(pts, vals, power):
    """IDW LOOCV 的独立双循环参考（k=min(5,n−1) 邻域 + 精确重合恢复）。"""
    from scipy.spatial import cKDTree
    pts = np.asarray(pts, dtype=float)
    vals = np.asarray(vals, dtype=float)
    n = len(vals)
    k = min(5, n - 1)
    tree = cKDTree(pts)
    dist, idx = tree.query(pts, k=k + 1)
    dist = np.atleast_2d(dist)
    idx = np.atleast_2d(idx)
    resid = []
    for i in range(n):
        d, v = [], []
        for j in range(k + 1):
            if int(idx[i][j]) != i:
                d.append(float(dist[i][j]))
                v.append(float(vals[int(idx[i][j])]))
        d, v = d[:k], v[:k]
        if any(dd < 1e-9 for dd in d):
            pred = v[next(j for j, dd in enumerate(d) if dd < 1e-9)]
        else:
            w = [1.0 / (dd ** power) for dd in d]
            pred = sum(wi * vi for wi, vi in zip(w, v)) / sum(w)
        resid.append(pred - float(vals[i]))
    return np.asarray(resid)


def _fixture_fc(lonlat, values, field="v"):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [float(x), float(y)]},
         "properties": {field: float(z)}}
        for (x, y), z in zip(np.asarray(lonlat, dtype=float), values)]}


def build_geostat() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []

    # --- γ(h) 模型曲线闭式（5 家族 + matern scipy 参考）-------------------
    sill, rng_, nug = 2.0, 1000.0, 0.5
    gamma_fixtures = {
        "spherical": [0.0, 125.0, 250.0, 500.0, 750.0, 1000.0, 1500.0, 3000.0],
        "exponential": [0.0, 166.667, 333.333, 666.667, 1000.0, 2000.0, 3000.0],
        "gaussian": [0.0, 250.0, 500.0, 750.0, 1000.0, 1500.0, 2500.0],
        "cubic": [0.0, 125.0, 250.0, 500.0, 750.0, 1000.0, 1750.0],
        "wave": [0.0, 125.0, 250.0, 500.0, 785.4, 1000.0, 1570.8, 3000.0],
    }
    for model, h_list in gamma_fixtures.items():
        ref = _gamma_ref(model, h_list, sill, rng_, nug)
        picks = set(range(len(h_list)))
        for i in sorted(picks):
            cases.append(case(
                f"gamma_{model}_{i}", f"{G_MOD}:_gamma", r9(float(ref[i])),
                args=[model, h_list, sill, rng_, nug], select=str(i),
                rtol=1e-9, note=f"{model} 闭式独立复算（nugget+sill·f(h/range)）"))
    # 第二参数组（nugget=0，range 短）—— 闭式在纯块金面下的形态
    sill2, rng2_, nug2 = 1.2, 300.0, 0.0
    for model in ("spherical", "exponential", "gaussian", "cubic"):
        h2 = [0.0, 100.0, 300.0, 900.0]
        ref2 = _gamma_ref(model, h2, sill2, rng2_, nug2)
        for i in (1, 3):
            cases.append(case(
                f"gamma_{model}_cfg2_{i}", f"{G_MOD}:_gamma",
                r9(float(ref2[i])), args=[model, h2, sill2, rng2_, nug2],
                select=str(i), rtol=1e-9,
                note=f"{model} 第二参数组（nugget=0）闭式复算"))
    h_m = [0.0, 400.0, 1000.0, 2000.0]
    ref_m = _gamma_ref("matern", h_m, sill, rng_, nug, nu=0.5)
    for i in (0, 1, 3):
        cases.append(case(
            f"gamma_matern_{i}", f"{G_MOD}:_gamma", r9(float(ref_m[i])),
            args=["matern", h_m, sill, rng_, nug, 0.5], select=str(i),
            rtol=1e-9, note="matern 闭式（scipy kv）独立复算"))
    ref_nug = _gamma_ref("spherical", [100.0, 5000.0], 0.0, rng_, 1.5)
    for i in (0, 1):
        cases.append(case(
            f"gamma_pure_nugget_{i}", f"{G_MOD}:_gamma", 1.5,
            args=["spherical", [100.0, 5000.0], 0.0, rng_, 1.5],
            select=str(i), kind="exact", note="纯块金 → 常数 nugget"))
    cases.append(error_case("gamma_unknown_model", f"{G_MOD}:_gamma",
                            "KrigingInputError",
                            args=["power", [100.0], 1.0, 1.0, 0.0]))

    # --- Matérn 相关闭式（ν=0.5/1.5/2.5 解析族 + 一般 ν）------------------
    x_l = [0.5, 1.5, 3.0]
    corr_05 = np.exp(-np.asarray(x_l))
    for i in (0, 1, 2):
        cases.append(case(
            f"matern_corr_nu05_{i}", f"{G_MOD}:_matern_corr",
            r9(float(corr_05[i])), args=[x_l, 0.5], select=str(i),
            rtol=1e-9, note="ν=1/2 → exp(−x) 解析族"))
    x_s = [0.1, 0.9]
    for i in range(2):
        cases.append(case(
            f"matern_corr_nu05_short_{i}", f"{G_MOD}:_matern_corr",
            r9(float(math.exp(-x_s[i]))), args=[x_s, 0.5], select=str(i),
            rtol=1e-9))
    corr_15 = ((1.0 + np.asarray(x_l)) * np.exp(-np.asarray(x_l)))
    for i in (0, 1, 2):
        cases.append(case(
            f"matern_corr_nu15_{i}", f"{G_MOD}:_matern_corr",
            r9(float(corr_15[i])), args=[x_l, 1.5], select=str(i),
            rtol=1e-9, note="ν=3/2 → (1+x)e^(−x) 解析族"))
    xv = np.array([2.0])
    corr_25 = (1.0 + xv + xv ** 2 / 3.0) * np.exp(-xv)
    cases.append(case("matern_corr_nu25", f"{G_MOD}:_matern_corr",
                      r9(float(corr_25[0])), args=[[2.0], 2.5], select="0",
                      rtol=1e-9, note="ν=5/2 → (1+x+x²/3)e^(−x) 解析族"))
    cases.append(case("matern_corr_zero_lag", f"{G_MOD}:_matern_corr",
                      1.0, args=[[0.0, -1.0], 1.5], select="0", kind="exact",
                      note="h=0 → 相关 1（γ(0)=nugget 的来源）"))
    from scipy.special import gammaln, kv
    nu_gen = 3.2
    t0 = (2.0 ** (nu_gen - 1.0)) * float(np.exp(gammaln(nu_gen)))
    xg = 1.8
    want_gen = xg ** nu_gen * float(kv(nu_gen, xg)) / t0
    cases.append(case("matern_corr_general_nu", f"{G_MOD}:_matern_corr",
                      r9(want_gen), args=[[1.8], nu_gen], select="0",
                      rtol=1e-7, note="2^(1−ν)/Γ(ν)·x^ν·K_ν(x) scipy 复算"))

    # --- 几何各向异性：矩阵元素 + 坐标映射 --------------------------------
    for angle, ratio in ((0.0, 1.0), (30.0, 2.0), (90.0, 3.0), (-45.0, 1.5),
                         (135.0, 2.5), (60.0, 4.0)):
        A = _anisotropy_matrix(angle, ratio)
        for (i, j) in ((0, 0), (0, 1), (1, 0), (1, 1)):
            cases.append(case(
                f"aniso_matrix_a{angle:g}_r{ratio:g}_{i}{j}",
                f"{G_MOD}:anisotropy_transform", r9(float(A[i, j])),
                args=[angle, ratio], select=f"{i}.{j}", rtol=1e-12,
                note="A = diag(1,ratio)·R(−θ) 矩阵独立复算"))
    cases.append(error_case("aniso_ratio_below_1", f"{G_MOD}:anisotropy_transform",
                            "KrigingInputError", args=[0.0, 0.5]))
    cases.append(error_case("aniso_ratio_negative", f"{G_MOD}:anisotropy_transform",
                            "KrigingInputError", args=[0.0, -2.0]))
    cases.append(error_case("aniso_angle_nan", f"{G_MOD}:anisotropy_transform",
                            "KrigingInputError", args=["nan", 2.0]))

    rng_gs = np.random.default_rng(501)
    xy_gs = rng_gs.uniform(0, 100, (4, 2))
    A30 = _anisotropy_matrix(30.0, 2.0)
    ref_xy = np.asarray(xy_gs) @ A30.T
    for i in range(4):
        for j in (0, 1):
            cases.append(case(
                f"apply_anisotropy_{i}{j}", f"{G_MOD}:apply_anisotropy",
                r9(float(ref_xy[i, j])), args=[xy_gs.tolist(), 30.0, 2.0],
                select=f"{i}.{j}", rtol=1e-12, note="xy @ A.T 独立复算"))
    for i in range(3):
        cases.append(case(
            f"apply_anisotropy_identity_{i}", f"{G_MOD}:apply_anisotropy",
            r9(float(xy_gs[i, 0])), args=[xy_gs.tolist(), 0.0, 1.0],
            select=f"{i}.0", rtol=1e-15, note="默认恒等映射原样返回"))
    A45 = _anisotropy_matrix(45.0, 3.0)
    ref45 = np.asarray(xy_gs) @ A45.T
    for i in (0, 2):
        for j in (0, 1):
            cases.append(case(
                f"apply_anisotropy_45_{i}{j}", f"{G_MOD}:apply_anisotropy",
                r9(float(ref45[i, j])), args=[xy_gs.tolist(), 45.0, 3.0],
                select=f"{i}.{j}", rtol=1e-12))

    # --- 契约校验基元 ------------------------------------------------------
    cases.append(case("matern_smoothness_lo", f"{G_MOD}:_validate_matern_smoothness",
                      0.1, args=[0.1], kind="exact"))
    cases.append(case("matern_smoothness_hi", f"{G_MOD}:_validate_matern_smoothness",
                      5.0, args=[5.0], kind="exact"))
    cases.append(error_case("matern_smoothness_too_low",
                            f"{G_MOD}:_validate_matern_smoothness",
                            "KrigingInputError", args=[0.05]))
    cases.append(error_case("matern_smoothness_too_high",
                            f"{G_MOD}:_validate_matern_smoothness",
                            "KrigingInputError", args=[5.5]))
    for backend in ("auto", "numpy_batched", "scipy_linalg"):
        cases.append(case(f"solve_backend_ok_{backend}",
                          f"{G_MOD}:_validate_solve_backend", backend,
                          args=[backend], kind="exact"))
    cases.append(error_case("solve_backend_unknown",
                            f"{G_MOD}:_validate_solve_backend",
                            "KrigingInputError", args=["cuda"]))

    # --- 趋势面：预测 / 系数 / 拟合统计 / LOOCV（lstsq 独立参考）----------
    trend_fixtures = {
        "plane": ([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0], [10.0, 10.0],
                   [5.0, 5.0], [3.0, 7.0], [8.0, 2.0], [2.0, 2.0], [7.0, 8.0],
                   [5.0, 1.0], [1.0, 5.0], [9.0, 6.0]],
                  lambda x, y: 2.0 + 3.0 * x - 1.0 * y, 1),
        "quadratic": ([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0], [10.0, 10.0],
                       [5.0, 5.0], [2.0, 8.0], [8.0, 3.0], [3.0, 2.0],
                       [7.0, 7.0], [1.0, 3.0], [6.0, 1.0], [4.0, 9.0],
                       [2.5, 4.5], [6.5, 5.5], [9.0, 2.0], [1.0, 8.0]],
                      lambda x, y: 1.0 + 0.5 * x - 0.3 * y + 0.05 * x * y, 2),
    }
    rng_tn = np.random.default_rng(502)
    noise = rng_tn.normal(0, 0.4, 12)

    for name, (pts_l, f, order) in trend_fixtures.items():
        vals = [f(x, y) for x, y in pts_l]
        uv = _unit_box_design_ref(pts_l, order)
        beta_ref, *_ = np.linalg.lstsq(uv, np.asarray(vals), rcond=None)
        n_terms = (order + 1) * (order + 2) // 2
        for j in range(n_terms):
            cases.append(case(
                f"trend_beta_{name}_j{j}", f"{TS_MOD}:trend_predict",
                r9(float(beta_ref[j])), args=[pts_l, vals, [[5.0, 5.0]], order],
                select=f"beta.{j}", rtol=1e-8,
                note="单位盒设计 + np.linalg.lstsq 独立参考"))
        targets = [[5.0, 5.0], [10.0, 0.0], [2.5, 7.5]]
        pts_arr = np.asarray(pts_l, dtype=float)
        mn_s = pts_arr.min(axis=0)
        span_s = pts_arr.max(axis=0) - mn_s
        tv = (np.asarray(targets, dtype=float) - mn_s) / span_s
        u_t, v_t = tv[:, 0], tv[:, 1]
        cols_t = [np.ones(len(targets))]
        for total_ in range(1, order + 1):
            for i_ in range(total_ + 1):
                cols_t.append(u_t ** (total_ - i_) * v_t ** i_)
        pred_ref = np.column_stack(cols_t) @ beta_ref
        for i in (0, 1, 2):
            cases.append(case(
                f"trend_prediction_{name}_{i}", f"{TS_MOD}:trend_predict",
                r9(float(pred_ref[i])), args=[pts_l, vals, targets, order],
                select=f"predictions.{i}", rtol=1e-8,
                note="design(u_t) @ beta 独立复算"))
        stats_ref = ts_stats = None
        uvd = _unit_box_design_ref(pts_l, order)
        resid = np.asarray(vals) - uvd @ beta_ref
        ss_res = float(resid @ resid)
        ss_tot = float(((np.asarray(vals) - np.mean(vals)) ** 2).sum())
        r2_ref = 1.0 - ss_res / ss_tot
        n_, p_ = len(vals), n_terms
        dof = max(n_ - p_, 1)
        cases.append(case(
            f"trend_fit_r2_{name}", f"{TS_MOD}:trend_fit_stats", r9(r2_ref),
            args=[pts_l, vals, order], select="r2", rtol=1e-8,
            note="R² = 1 − SS_res/SS_tot 独立复算"))
        cases.append(case(
            f"trend_fit_resvar_{name}", f"{TS_MOD}:trend_fit_stats",
            r9(ss_res / dof), args=[pts_l, vals, order],
            select="residual_variance", rtol=1e-8,
            note="σ̂² = SS_res/(n−p) 独立复算"))
        cases.append(case(
            f"trend_fit_nparams_{name}", f"{TS_MOD}:trend_fit_stats",
            n_terms, args=[pts_l, vals, order], select="n_params",
            kind="exact"))
        for j in (0, 1):
            cases.append(case(
                f"trend_fit_coef_{name}_j{j}", f"{TS_MOD}:trend_fit_stats",
                r9(round(float(beta_ref[j]), 6)), args=[pts_l, vals, order],
                select=f"coefficients.{j}", rtol=1e-5,
                note="单位盒坐标系数（rounded 6 披露口径）"))

    # 噪声 fixture：残差 / LOOCV
    pts_noisy = trend_fixtures["plane"][0]
    vals_noisy = [f(x, y) + noise[i]
                  for i, (x, y) in enumerate(pts_noisy)]
    uv_n = _unit_box_design_ref(pts_noisy, 1)
    resid_ref = np.asarray(vals_noisy) - uv_n @ np.linalg.lstsq(
        uv_n, np.asarray(vals_noisy), rcond=None)[0]
    cases.append(case("trend_residual_mean_noisy", f"{TS_MOD}:trend_predict",
                      r9(0.0), args=[pts_noisy, vals_noisy, [[5.0, 5.0]], 1],
                      select="mean:residuals", rtol=1e-6,
                      note="含截距 OLS 残差均值 ≈ 0"))
    # LOOCV 独立双循环（逐点剔除 + lstsq 重拟合）
    preds_loo = np.empty(len(vals_noisy))
    for i in range(len(vals_noisy)):
        mask = np.ones(len(vals_noisy), dtype=bool)
        mask[i] = False
        b_i, *_ = np.linalg.lstsq(uv_n[mask], np.asarray(vals_noisy)[mask],
                                  rcond=None)
        preds_loo[i] = float(uv_n[i] @ b_i)
    resid_loo = preds_loo - np.asarray(vals_noisy)
    cases.append(case("trend_loocv_rmse_noisy", f"{TS_MOD}:trend_loocv",
                      r9(float(np.sqrt(np.mean(resid_loo ** 2)))),
                      args=[pts_noisy, vals_noisy, 1], select="rmse",
                      rtol=1e-7, note="留一重拟合双循环独立参考"))
    cases.append(case("trend_loocv_mae_noisy", f"{TS_MOD}:trend_loocv",
                      r9(float(np.mean(np.abs(resid_loo)))),
                      args=[pts_noisy, vals_noisy, 1], select="mae",
                      rtol=1e-7))
    plane_pts = trend_fixtures["plane"][0]
    plane_vals_exact = [2.0 + 3.0 * x - 1.0 * y for x, y in plane_pts]
    cases.append(case("trend_loocv_exact_plane_zero", f"{TS_MOD}:trend_loocv",
                      0.0, args=[plane_pts, plane_vals_exact, 1],
                      select="rmse", rtol=1e-9,
                      note="精确平面的 LOOCV 残差恒 0（仿射缩放不改线性空间）"))
    for order_, want in ((0, 1), (1, 3), (2, 6), (3, 10), (4, 15), (-1, 0)):
        cases.append(case(f"trend_terms_order{order_}", f"{TS_MOD}:trend_terms",
                          want, args=[order_], kind="exact",
                          note="(order+1)(order+2)/2（纯计数器，无阶数契约）"))
    cases.append(error_case("trend_order_zero", f"{TS_MOD}:trend_predict",
                            "UNSUPPORTED_METHOD",
                            args=[pts_noisy, vals_noisy, [[5.0, 5.0]], 0]))
    cases.append(error_case("trend_order_four", f"{TS_MOD}:trend_predict",
                            "UNSUPPORTED_METHOD",
                            args=[pts_noisy, vals_noisy, [[5.0, 5.0]], 4]))
    cases.append(error_case("trend_too_few_samples", f"{TS_MOD}:trend_predict",
                            "INSUFFICIENT_SAMPLES",
                            args=[pts_noisy[:3], vals_noisy[:3],
                                  [[5.0, 5.0]], 1]))
    collinear = [[0.0, 0.0], [1.0, 1.0], [2.0, 2.0], [3.0, 3.0],
                 [4.0, 4.0], [5.0, 5.0], [6.0, 6.0], [7.0, 7.0]]
    cases.append(error_case("trend_collinear_degenerate",
                            f"{TS_MOD}:trend_predict", "DEGENERATE_DATA",
                            args=[collinear, [1.0] * 8, [[1.0, 2.0]], 1]))
    cases.append(case("trend_feasible_guard_pass", f"{TS_MOD}:_check_order_feasible",
                      None, args=[1, 12], kind="exact",
                      note="n/3 ≥ order·(order+3)/2 → 放行"))
    cases.append(error_case("trend_feasible_guard_reject",
                            f"{TS_MOD}:_check_order_feasible",
                            "INSUFFICIENT_SAMPLES", args=[3, 8]))

    # --- RBF 预测：scipy 直接调用参考 + 过样本点精确性 --------------------
    rbf_pts = pts_noisy
    rbf_vals = vals_noisy
    from scipy.interpolate import RBFInterpolator
    for kernel in ("thin_plate_spline", "linear", "cubic", "quintic"):
        ref = RBFInterpolator(np.asarray(rbf_pts), np.asarray(rbf_vals),
                              kernel=kernel, smoothing=0.0, neighbors=12,
                              degree=-1)(np.array([[5.0, 5.0], [2.0, 8.0],
                                                   [7.0, 3.0]]))
        for i in (0, 1, 2):
            cases.append(case(
                f"rbf_predict_{kernel}_{i}", f"{RB_MOD}:rbf_predict",
                r9(float(ref[i])), args=[rbf_pts, rbf_vals,
                                         [[5.0, 5.0], [2.0, 8.0], [7.0, 3.0]],
                                         kernel],
                kwargs={"neighbors": 12}, select=str(i), rtol=1e-6,
                note="scipy RBFInterpolator 同参数直接参考"))
    exact_ref = RBFInterpolator(np.asarray(rbf_pts), np.asarray(rbf_vals),
                                kernel="thin_plate_spline", smoothing=0.0,
                                neighbors=12, degree=-1)(np.asarray(rbf_pts[:5]))
    for i in range(5):
        cases.append(case(
            f"rbf_exact_at_sample_{i}", f"{RB_MOD}:rbf_predict",
            r9(float(exact_ref[i])), args=[rbf_pts, rbf_vals,
                                           [rbf_pts[i]], "thin_plate_spline"],
            kwargs={"neighbors": 12}, select="0", rtol=1e-6,
            note="smoothing=0 → 过样本点精确（与样本值一致）"))
    smooth_ref = RBFInterpolator(np.asarray(rbf_pts), np.asarray(rbf_vals),
                                 kernel="linear", smoothing=1.5, neighbors=12,
                                 degree=-1)(np.array([[5.0, 5.0]]))
    cases.append(case("rbf_predict_smoothed", f"{RB_MOD}:rbf_predict",
                      r9(float(smooth_ref[0])),
                      args=[rbf_pts, rbf_vals, [[5.0, 5.0]], "linear"],
                      kwargs={"smoothing": 1.5, "neighbors": 12},
                      select="0", rtol=1e-6, note="平滑 >0 → 正则化拟合参考"))
    cases.append(error_case("rbf_unknown_kernel", f"{RB_MOD}:rbf_predict",
                            "UNSUPPORTED_METHOD",
                            args=[rbf_pts, rbf_vals, [[5.0, 5.0]], "gaussian"]))
    cases.append(error_case("rbf_single_sample", f"{RB_MOD}:rbf_predict",
                            "INSUFFICIENT_SAMPLES",
                            args=[[[1.0, 1.0]], [1.0], [[2.0, 2.0]]]))
    cases.append(error_case("rbf_smoothing_over_10", f"{RB_MOD}:rbf_predict",
                            "ValueError",
                            args=[rbf_pts, rbf_vals, [[5.0, 5.0]]],
                            kwargs={"kernel": "linear", "smoothing": 11.0}))
    cases.append(error_case("rbf_neighbors_zero", f"{RB_MOD}:rbf_predict",
                            "ValueError",
                            args=[rbf_pts, rbf_vals, [[5.0, 5.0]]],
                            kwargs={"kernel": "linear", "neighbors": 0}))
    # RBF LOOCV（逐点剔除 + scipy 重拟合双循环参考）
    loo_rbf = np.empty(len(rbf_vals))
    for i in range(len(rbf_vals)):
        mask = np.ones(len(rbf_vals), dtype=bool)
        mask[i] = False
        loo_rbf[i] = RBFInterpolator(
            np.asarray(rbf_pts)[mask], np.asarray(rbf_vals)[mask],
            kernel="linear", smoothing=0.0,
            neighbors=min(12, len(rbf_vals) - 1), degree=-1)(
            np.asarray(rbf_pts)[i:i + 1])[0]
    resid_rbf = loo_rbf - np.asarray(rbf_vals)
    cases.append(case("rbf_loocv_rmse", f"{RB_MOD}:rbf_loocv",
                      r9(float(np.sqrt(np.mean(resid_rbf ** 2)))),
                      args=[rbf_pts, rbf_vals, "linear"], kwargs={"neighbors": 12},
                      select="rmse", rtol=1e-6,
                      note="留一重拟合双循环（scipy）独立参考"))
    cases.append(case("rbf_loocv_sample_count", f"{RB_MOD}:rbf_loocv",
                      len(rbf_vals), args=[rbf_pts, rbf_vals, "linear"],
                      kwargs={"neighbors": 12}, select="sample_count",
                      kind="exact"))

    # --- TIN：平面精确恢复（线性 = 分片平面）+ 退化拒绝 --------------------
    from scipy.interpolate import LinearNDInterpolator, CloughTocher2DInterpolator
    tin_pts = [[0.0, 0.0], [10.0, 0.0], [0.0, 10.0], [10.0, 10.0], [5.0, 5.0],
               [2.0, 7.0], [8.0, 3.0], [4.0, 2.0]]
    tin_vals = [2.0 + 0.5 * x - 0.25 * y for x, y in tin_pts]
    tin_targets = [[5.0, 5.0], [2.0, 3.0], [7.0, 7.0], [3.5, 5.5], [6.0, 2.5]]
    plane_z = lambda x, y: 2.0 + 0.5 * x - 0.25 * y  # noqa: E731
    for i, (tx, ty) in enumerate(tin_targets):
        cases.append(case(
            f"tin_linear_plane_recovery_{i}", f"{TI_MOD}:tin_predict",
            r9(plane_z(tx, ty)), args=[tin_pts, tin_vals, [[tx, ty]], "linear"],
            select="0", rtol=1e-9, note="线性 TIN 分片平面 → 精确恢复"))
    ct_ref = CloughTocher2DInterpolator(
        np.asarray(tin_pts), np.asarray(tin_vals),
        fill_value=np.nan)(np.array(tin_targets))
    for i in (0, 1):
        cases.append(case(
            f"tin_clough_tocher_plane_{i}", f"{TI_MOD}:tin_predict",
            r9(float(ct_ref[i])), args=[tin_pts, tin_vals, [tin_targets[i]],
                                        "clough_tocher"],
            select="0", rtol=1e-9, note="C¹ Clough-Tocher scipy 直接参考"
                                         "（平面上同为精确恢复）"))
    lin_ref = LinearNDInterpolator(np.asarray(tin_pts), np.asarray(tin_vals),
                                   fill_value=np.nan)(np.array(tin_targets))
    cases.append(case("tin_linear_scipy_reference", f"{TI_MOD}:tin_predict",
                      r9(float(lin_ref[2])), args=[tin_pts, tin_vals,
                                                   [tin_targets[2]], "linear"],
                      select="0", rtol=1e-9, note="scipy LinearND 直接参考"))
    cases.append(case("tin_linear_scipy_reference_4", f"{TI_MOD}:tin_predict",
                      r9(float(lin_ref[3])), args=[tin_pts, tin_vals,
                                                   [tin_targets[3]], "linear"],
                      select="0", rtol=1e-9))
    cases.append(error_case("tin_two_points_degenerate", f"{TI_MOD}:tin_predict",
                            "DEGENERATE_DATA",
                            args=[[[0.0, 0.0], [1.0, 1.0]], [1.0, 2.0],
                                  [[0.5, 0.5]]]))
    cases.append(error_case("tin_collinear_degenerate", f"{TI_MOD}:tin_predict",
                            "DEGENERATE_DATA",
                            args=[collinear, [1.0] * 8, [[1.0, 2.0]]]))
    cases.append(error_case("tin_unknown_method", f"{TI_MOD}:tin_predict",
                            "UNSUPPORTED_METHOD",
                            args=[tin_pts, tin_vals, [[5.0, 5.0]], "nearest"]))
    # TIN LOOCV（留一 + scipy 重拟合；凸包收缩处残差诚实缺失）
    loo_tin, tin_covered = [], 0
    for i in range(len(tin_vals)):
        mask = np.ones(len(tin_vals), dtype=bool)
        mask[i] = False
        p_i = LinearNDInterpolator(np.asarray(tin_pts)[mask],
                                   np.asarray(tin_vals)[mask],
                                   fill_value=np.nan)(
            np.asarray(tin_pts)[i:i + 1])[0]
        if np.isfinite(p_i):
            loo_tin.append(float(p_i) - tin_vals[i])
            tin_covered += 1
    loo_tin = np.asarray(loo_tin)
    cases.append(case("tin_loocv_rmse", f"{TI_MOD}:tin_loocv",
                      r9(float(np.sqrt(np.mean(loo_tin ** 2)))),
                      args=[tin_pts, tin_vals, "linear"], select="rmse",
                      rtol=1e-7, note="留一重拟合（scipy）；凸包外残差剔除"))
    cases.append(case("tin_loocv_sample_count", f"{TI_MOD}:tin_loocv",
                      tin_covered, args=[tin_pts, tin_vals, "linear"],
                      select="sample_count", kind="exact",
                      note="仅凸包内留一点有残差（诚实覆盖数）"))
    cases.append(case("tin_loocv_mae", f"{TI_MOD}:tin_loocv",
                      r9(float(np.mean(np.abs(loo_tin)))),
                      args=[tin_pts, tin_vals, "linear"], select="mae",
                      rtol=1e-7))

    # --- IDW LOOCV：独立双循环参考（多幂次配置）---------------------------
    idw_pts = [[float(x), float(y)] for x, y in
               np.column_stack([rng_gs.uniform(0, 100, 10),
                                rng_gs.uniform(0, 100, 10)])]
    idw_vals = [float(v) for v in rng_gs.uniform(0, 50, 10)]
    idw_dup = [[0.0, 0.0], [0.0, 0.0], [10.0, 0.0]]
    idw_dup_vals = [5.0, 5.0, 7.0]
    for power in (1.0, 2.0, 4.0):
        resid_idw = _ref_idw_loocv(idw_pts, idw_vals, power)
        cases.append(case(
            f"idw_loocv_rmse_power{power:g}", f"{ID_MOD}:idw_loocv",
            r9(float(np.sqrt(np.mean(resid_idw ** 2)))),
            args=[idw_pts, idw_vals, power], select="rmse", rtol=1e-8,
            note="k=min(5,n−1) 邻域 + 精确重合恢复的双循环独立参考"))
        cases.append(case(
            f"idw_loocv_bias_power{power:g}", f"{ID_MOD}:idw_loocv",
            r9(float(np.mean(resid_idw))), args=[idw_pts, idw_vals, power],
            select="bias", rtol=1e-8))
        cases.append(case(
            f"idw_loocv_mae_power{power:g}", f"{ID_MOD}:idw_loocv",
            r9(float(np.mean(np.abs(resid_idw)))),
            args=[idw_pts, idw_vals, power], select="mae", rtol=1e-8))
        cases.append(case(
            f"idw_loocv_count_power{power:g}", f"{ID_MOD}:idw_loocv", 10,
            args=[idw_pts, idw_vals, power], select="sample_count",
            kind="exact"))
    resid_dup = _ref_idw_loocv(idw_dup, idw_dup_vals, 2.0)
    cases.append(case("idw_loocv_duplicate_exact_hit",
                      f"{ID_MOD}:idw_loocv",
                      r9(float(np.mean(resid_dup))),
                      args=[idw_dup, idw_dup_vals, 2.0], select="bias",
                      rtol=1e-9, note="重合样本 → 精确重合恢复（残差 0 分量）"))
    cases.append(case("idw_loocv_method_tag", f"{ID_MOD}:idw_loocv",
                      "loocv", args=[idw_pts, idw_vals, 2.0],
                      select="method", kind="exact"))
    cases.append(error_case("idw_loocv_single_sample", f"{ID_MOD}:idw_loocv",
                            "INSUFFICIENT_SAMPLES",
                            args=[[[1.0, 1.0]], [1.0], 2.0]))
    cases.append(error_case("idw_power_zero", f"{ID_MOD}:idw_loocv",
                            "UNSUPPORTED_METHOD",
                            args=[idw_pts, idw_vals, 0.0]))

    # --- H3 driver：metadata 不变量（独立计数）+ 锚 ------------------------
    rng_fc = np.random.default_rng(503)
    fc_ll = np.column_stack([rng_fc.uniform(116.00, 116.05, 24),
                             rng_fc.uniform(39.90, 39.95, 24)])
    fc_vv = [float(v) for v in rng_fc.normal(10, 1, 24)]
    fc = _fixture_fc(fc_ll, fc_vv)
    res_k = kriging_res = None
    from app.lib.geo_analysis import kriging as kg_build
    res_k = kg_build.kriging_interpolation(fc, "v", resolution=7,
                                           cross_validate=True)
    md = res_k["metadata"]
    cases.append(case("kriging_driver_n_samples", f"{G_MOD}:kriging_interpolation",
                      24, args=[fc, "v"], kwargs={"resolution": 7,
                                                  "cross_validate": True},
                      select="metadata.n_samples", kind="exact"))
    cases.append(case("kriging_driver_n_fit_samples",
                      f"{G_MOD}:kriging_interpolation",
                      24, args=[fc, "v"], kwargs={"resolution": 7,
                                                  "cross_validate": True},
                      select="metadata.n_fit_samples", kind="exact",
                      note="n ≤ 拟合上限 → 不触发分层抽稀"))
    cases.append(case("kriging_driver_neighbors",
                      f"{G_MOD}:kriging_interpolation", 12,
                      args=[fc, "v"], kwargs={"resolution": 7,
                                              "cross_validate": True},
                      select="metadata.neighbors", kind="exact"))
    cases.append(case("kriging_driver_degraded_zero",
                      f"{G_MOD}:kriging_interpolation", 0,
                      args=[fc, "v"], kwargs={"resolution": 7,
                                              "cross_validate": True},
                      select="metadata.degraded_cells", kind="exact",
                      note="病态系统退化像元数 0（不变量）"))
    cases.append(case("kriging_driver_cv_n_samples",
                      f"{G_MOD}:kriging_interpolation", 24,
                      args=[fc, "v"], kwargs={"resolution": 7,
                                              "cross_validate": True},
                      select="metadata.cross_validation.n_samples",
                      kind="exact", note="CV 折划分覆盖全部样本（不变量）"))
    folds_expected = max(2, min(5, 24 // 4))
    cases.append(case("kriging_driver_cv_folds",
                      f"{G_MOD}:kriging_interpolation", folds_expected,
                      args=[fc, "v"], kwargs={"resolution": 7,
                                              "cross_validate": True},
                      select="metadata.cross_validation.folds", kind="exact",
                      note="folds = max(2, min(5, n//4)) 独立复算"))
    # index 方案 fold = i % folds 的逐折 n_test 独立计数
    for f_idx in (0, 4):
        want_nt = int(np.sum(np.arange(24) % folds_expected == f_idx))
        cases.append(case(
            f"kriging_driver_cv_fold{f_idx}_ntest",
            f"{G_MOD}:kriging_interpolation", want_nt,
            args=[fc, "v"], kwargs={"resolution": 7, "cross_validate": True},
            select=f"metadata.cross_validation.per_fold.{f_idx}.n_test",
            kind="exact", note="index 方案 fold=i%folds 逐折计数独立复算"))
    cases.append(case(
        "kriging_driver_cv_scheme", f"{G_MOD}:kriging_interpolation",
        "index", args=[fc, "v"], kwargs={"resolution": 7,
                                         "cross_validate": True},
        select="metadata.cross_validation.scheme", kind="exact"))
    cases.append(case(
        "kriging_driver_variogram_model_vocab",
        f"{G_MOD}:kriging_interpolation", md["variogram"]["model"],
        args=[fc, "v"], kwargs={"resolution": 7, "cross_validate": True},
        select="metadata.variogram.model", kind="exact", anchor=True,
        note="回归锚：auto 选型落在 6 家族词表内"))
    # spatial_block 方案：fold 划分独立复算（dense rank → 3×3 块 → %folds）
    fc_sb = _fixture_fc(fc_ll, fc_vv)
    res_sb = kg_build.kriging_interpolation(fc_sb, "v", resolution=7,
                                            cross_validate=True,
                                            cv_scheme="spatial_block")
    sb_md = res_sb["metadata"]["cross_validation"]
    n_grid = math.ceil(math.sqrt(folds_expected))
    rx = np.argsort(np.argsort(fc_ll[:, 0], kind="stable"), kind="stable")
    ry = np.argsort(np.argsort(fc_ll[:, 1], kind="stable"), kind="stable")
    block = (ry * n_grid) // 24 * n_grid + (rx * n_grid) // 24
    fold_ref = block % folds_expected
    used0 = int(np.sum(fold_ref == 0))
    cases.append(case(
        "kriging_driver_spatial_block_fold0",
        f"{G_MOD}:kriging_interpolation", used0,
        args=[fc_sb, "v"], kwargs={"resolution": 7, "cross_validate": True,
                                   "cv_scheme": "spatial_block"},
        select="metadata.cross_validation.per_fold.0.n_test", kind="exact",
        note="spatial_block：dense-rank 网格分块 %folds 独立复算"))
    cases.append(case(
        "kriging_driver_spatial_block_scheme",
        f"{G_MOD}:kriging_interpolation", "spatial_block",
        args=[fc_sb, "v"], kwargs={"resolution": 7, "cross_validate": True,
                                   "cv_scheme": "spatial_block"},
        select="metadata.cross_validation.scheme", kind="exact"))
    # universal 精确平面 → 零残差退化：drift = OLS 平面系数（pyproj 投影独立参考）
    from pyproj import Transformer
    plane_ll = np.column_stack([np.tile(np.linspace(116.00, 116.04, 5), 5),
                                np.repeat(np.linspace(39.90, 39.94, 5), 5)])
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
    mx, my = tr.transform(plane_ll[:, 0], plane_ll[:, 1])
    u_ = (mx - mx.min()) / (mx.max() - mx.min())
    v_ = (my - my.min()) / (my.max() - my.min())
    plane_z_vals = 2.0 + 3.0 * u_ - 1.0 * v_
    # UK 漂移系数是**米制原始坐标** [1, x, y] 的 OLS（非单位盒缩放）
    plane_xy = np.column_stack([np.ones(25), mx, my])
    beta_plane, *_ = np.linalg.lstsq(plane_xy, plane_z_vals, rcond=None)
    fc_plane = _fixture_fc(plane_ll, plane_z_vals)
    cases.append(case(
        "kriging_driver_plane_drift0", f"{G_MOD}:kriging_interpolation",
        r9(round(float(beta_plane[0]), 6)),
        args=[fc_plane, "v"], kwargs={"resolution": 7, "method": "universal",
                                      "cross_validate": False},
        select="metadata.drift.coefficients.0", rtol=1e-6,
        note="精确平面 → 零残差退化的漂移系数 = pyproj 投影 + lstsq 独立参考"))
    cases.append(case(
        "kriging_driver_plane_disclosure", f"{G_MOD}:kriging_interpolation",
        ["zero_residual_variance"],
        args=[fc_plane, "v"], kwargs={"resolution": 7, "method": "universal",
                                      "cross_validate": False},
        select="metadata.disclosures", kind="exact",
        note="零残差方差诚实披露"))
    cases.append(error_case(
        "kriging_driver_bad_variogram_model", f"{G_MOD}:kriging_interpolation",
        "KrigingInputError", args=[fc, "v"],
        kwargs={"resolution": 7, "variogram_model": "power"}))
    cases.append(error_case(
        "kriging_driver_bad_matern_smoothness", f"{G_MOD}:kriging_interpolation",
        "KrigingInputError", args=[fc, "v"],
        kwargs={"resolution": 7, "variogram_model": "matern",
                "matern_smoothness": 9.0}))
    cases.append(error_case(
        "kriging_driver_bad_backend", f"{G_MOD}:kriging_interpolation",
        "KrigingInputError", args=[fc, "v"],
        kwargs={"resolution": 7, "solve_backend": "cuda"}))

    # --- indicator / collocated / fit_variogram / nn / sibson 错误面 -------
    ik_pts = [[float(x), float(y)] for x, y in rng_gs.uniform(0, 50, (7, 2))]
    ik_vals = [float(v) for v in rng_gs.uniform(0, 10, 7)]
    cases.append(error_case("indicator_kriging_too_few", f"{G_MOD}:indicator_kriging",
                            "KrigingInputError",
                            args=[ik_pts, ik_vals, [[5.0, 5.0]], [3.0]]))
    ik_pts8 = [[float(x), float(y)] for x, y in rng_gs.uniform(0, 50, (10, 2))]
    ik_vals8 = [float(v) for v in rng_gs.uniform(0, 10, 10)]
    cases.append(error_case("indicator_kriging_empty_thresholds",
                            f"{G_MOD}:indicator_kriging", "KrigingInputError",
                            args=[ik_pts8, ik_vals8, [[5.0, 5.0]], []]))
    cases.append(error_case("indicator_kriging_nan_threshold",
                            f"{G_MOD}:indicator_kriging", "KrigingInputError",
                            args=[ik_pts8, ik_vals8, [[5.0, 5.0]], ["nan"]]))
    cases.append(error_case("indicator_kriging_etype_mismatch",
                            f"{G_MOD}:indicator_kriging", "KrigingInputError",
                            args=[ik_pts8, ik_vals8, [[5.0, 5.0]], [3.0, 6.0]],
                            kwargs={"etype_values": [3.0]}))
    cases.append(error_case("indicator_kriging_bad_model",
                            f"{G_MOD}:indicator_kriging", "KrigingInputError",
                            args=[ik_pts8, ik_vals8, [[5.0, 5.0]], [3.0]],
                            kwargs={"variogram_model": "power"}))
    cases.append(error_case(
        "ordinary_kriging_single_sample", f"{G_MOD}:ordinary_kriging",
        "INSUFFICIENT_SAMPLES",
        args=[[[0.0, 0.0], [1.0, 1.0]], [1.0], [[2.0, 2.0]], None],
        note="variogram 形参先占位（n=1 在任何使用前类型化拒绝）"))
    cases.append(error_case(
        "cokriging_weak_rho", f"{G_MOD}:collocated_cokriging",
        "SCIENTIFIC_PRECONDITION_FAILED",
        args=[ik_pts8, ik_vals8, ik_pts8,
              [float(v) for v in rng_gs.uniform(0, 10, 10)], [[5.0, 5.0]]],
        kwargs={"correlation_rho": 0.05},
        note="|ρ| < 0.2 结构化拒绝（不输出无意义表面）"))
    cases.append(error_case(
        "cokriging_rho_out_of_range", f"{G_MOD}:collocated_cokriging",
        "KrigingInputError",
        args=[ik_pts8, ik_vals8, ik_pts8, ik_vals8, [[5.0, 5.0]]],
        kwargs={"correlation_rho": 1.5}))
    cases.append(error_case(
        "cokriging_too_few_primary", f"{G_MOD}:collocated_cokriging",
        "KrigingInputError",
        args=[ik_pts, ik_vals, ik_pts, ik_vals, [[5.0, 5.0]]]))
    cases.append(error_case(
        "fit_variogram_unknown_model", f"{G_MOD}:fit_variogram",
        "KrigingInputError",
        args=[[[float(x) * 100.0, float(y) * 100.0] for x, y in idw_pts],
              [float(v) for v in idw_vals]], kwargs={"model": "power"}))
    # 注：fit_variogram 主体需 ndarray 入参（identity 各向异性快路径不转换），
    # 共线样本的 "有效滞后 bin < 4" 拒绝无法经 JSON 回放（运行器局限）。
    cases.append(error_case(
        "nearest_neighbor_empty", f"{ID_MOD}:nearest_neighbor_interpolation",
        "INSUFFICIENT_SAMPLES", args=[[], [], [[1.0, 1.0]]]))
    cases.append(error_case(
        "sibson_two_points", f"{TI_MOD}:natural_neighbor_interpolation",
        "DEGENERATE_DATA",
        args=[[[0.0, 0.0], [1.0, 1.0]], [1.0, 2.0], [[0.5, 0.5]]]))
    cases.append(error_case(
        "sibson_collinear", f"{TI_MOD}:natural_neighbor_interpolation",
        "DEGENERATE_DATA",
        args=[collinear, [1.0] * 8, [[1.0, 2.0]]]))

    # --- driver 锚（tin/trend/nn/sibson metadata + record 值）--------------
    from app.lib.geo_analysis import tin_interpolation as ti_build
    from app.lib.geo_analysis import trend_surface as ts_build
    from app.lib.geo_analysis import interpolation as id_build
    tin_driver_pts = np.column_stack([rng_fc.uniform(116.00, 116.05, 12),
                                      rng_fc.uniform(39.90, 39.95, 12)])
    tin_driver_vals = [float(v) for v in rng_fc.uniform(0, 10, 12)]
    fc_tin = _fixture_fc(tin_driver_pts, tin_driver_vals)
    res_tin = ti_build.tin_surface(fc_tin, "v", resolution=7,
                                   cross_validate=True)
    tin_md = res_tin["metadata"]
    from scipy.spatial import Delaunay
    from pyproj import Transformer as _Tr
    tr2 = _Tr.from_crs("EPSG:4326", tin_md["working_crs"], always_xy=True)
    tmx, tmy = tr2.transform(tin_driver_pts[:, 0], tin_driver_pts[:, 1])
    tri_count = len(Delaunay(np.column_stack([tmx, tmy])).simplices)
    cases.append(case(
        "tin_driver_triangle_count", f"{TI_MOD}:tin_surface", tri_count,
        args=[fc_tin, "v"], kwargs={"resolution": 7, "cross_validate": True},
        select="metadata.triangle_count", kind="exact",
        note="pyproj 投影 + Delaunay 独立复算"))
    cases.append(case(
        "tin_driver_n_samples", f"{TI_MOD}:tin_surface", 12,
        args=[fc_tin, "v"], kwargs={"resolution": 7, "cross_validate": True},
        select="metadata.n_samples", kind="exact"))
    cases.append(case(
        "tin_driver_first_record_value", f"{TI_MOD}:tin_surface",
        r9(res_tin["records"][0]["value"]),
        args=[fc_tin, "v"], kwargs={"resolution": 7, "cross_validate": True},
        select="records.0.value", anchor=True,
        note="回归锚：首记录插值值（凸包内）"))
    ts_vals_driver = [float(v) for v in rng_fc.uniform(0, 10, 12)]
    fc_ts = _fixture_fc(tin_driver_pts, ts_vals_driver)
    res_ts = ts_build.trend_surface(fc_ts, "v", resolution=7, order=1,
                                    cross_validate=False)
    cases.append(case(
        "trend_driver_n_params", f"{TS_MOD}:trend_surface", 3,
        args=[fc_ts, "v"], kwargs={"resolution": 7, "order": 1,
                                   "cross_validate": False},
        select="metadata.n_params", kind="exact",
        note="order=1 → 3 项（独立计数）"))
    ts_b, *_ = np.linalg.lstsq(np.column_stack([np.ones(12), tmx, tmy]),
                               np.asarray(ts_vals_driver), rcond=None)
    ts_resid = np.asarray(ts_vals_driver) - np.column_stack(
        [np.ones(12), tmx, tmy]) @ ts_b
    ts_r2 = 1.0 - float(ts_resid @ ts_resid) / float(
        ((np.asarray(ts_vals_driver) - np.mean(ts_vals_driver)) ** 2).sum())
    cases.append(case(
        "trend_driver_r2_reference", f"{TS_MOD}:trend_surface",
        r9(round(ts_r2, 6)),
        args=[fc_ts, "v"], kwargs={"resolution": 7, "order": 1,
                                   "cross_validate": False},
        select="metadata.r2", rtol=1e-5,
        note="pyproj 投影 + 平面 OLS R² 独立复算（单位盒缩放不改 R²）"))
    nn_fc = _fixture_fc(tin_driver_pts, ts_vals_driver)
    res_nn = id_build.nearest_neighbor_surface(nn_fc, "v", resolution=7)
    cases.append(case(
        "nn_driver_n_samples", f"{ID_MOD}:nearest_neighbor_surface", 12,
        args=[nn_fc, "v"], kwargs={"resolution": 7},
        select="metadata.n_samples", kind="exact"))
    cases.append(case(
        "nn_driver_first_record_value", f"{ID_MOD}:nearest_neighbor_surface",
        r9(res_nn["records"][0]["value"]),
        args=[nn_fc, "v"], kwargs={"resolution": 7},
        select="records.0.value", anchor=True,
        note="回归锚：首记录最近邻值（Voronoi 分段常值场）"))
    from app.lib.geo_analysis import tin_interpolation as _ti_nn
    res_sib = _ti_nn.natural_neighbor_surface(nn_fc, "v", resolution=7)
    cases.append(case(
        "sibson_driver_n_samples", f"{TI_MOD}:natural_neighbor_surface", 12,
        args=[nn_fc, "v"], kwargs={"resolution": 7},
        select="metadata.n_samples", kind="exact"))
    cases.append(case(
        "sibson_driver_fill_fraction", f"{TI_MOD}:natural_neighbor_surface",
        r9(res_sib["metadata"]["fill_fraction"]),
        args=[nn_fc, "v"], kwargs={"resolution": 7},
        select="metadata.fill_fraction", anchor=True,
        note="回归锚：凸包内填充率（≤1 不变量，Sibson 不外推）"))
    cases.append(case(
        "sibson_driver_first_record_value",
        f"{TI_MOD}:natural_neighbor_surface",
        r9(res_sib["records"][0]["value"]),
        args=[nn_fc, "v"], kwargs={"resolution": 7},
        select="records.0.value", anchor=True,
        note="回归锚：首记录 Sibson 插值值"))

    # --- idw_surface driver：metadata 不变量 + 幂次/分辨率错误 --------------
    idw_fc = _fixture_fc(tin_driver_pts, ts_vals_driver)
    cases.append(case(
        "idw_driver_n_samples", f"{ID_MOD}:idw_surface", 12,
        args=[idw_fc, "v"], kwargs={"resolution": 7, "power": 2.0,
                                    "cross_validate": False},
        select="metadata.n_samples", kind="exact"))
    cases.append(case(
        "idw_driver_power_echo", f"{ID_MOD}:idw_surface", 2.0,
        args=[idw_fc, "v"], kwargs={"resolution": 7, "power": 2.0,
                                    "cross_validate": False},
        select="metadata.power", kind="exact"))
    cases.append(error_case("idw_power_over_5", f"{ID_MOD}:idw_surface",
                            "UNSUPPORTED_METHOD",
                            args=[idw_fc, "v"], kwargs={"resolution": 7,
                                                        "power": 6.0}))
    cases.append(error_case("idw_resolution_over_15", f"{ID_MOD}:idw_surface",
                            "ValueError",
                            args=[idw_fc, "v"], kwargs={"resolution": 16}))
    cases.append(error_case("idw_missing_field", f"{ID_MOD}:idw_surface",
                            "ValueError",
                            args=[idw_fc, "nope"], kwargs={"resolution": 7}))

    return cases


# ── 域：sar（SAR 辐射定标 / 斑点滤波 / 时序统计 · 公式独立复算）────────
# 只覆盖既有稳定模块（sar_calibration / sar_filter / sar_temporal）；
# in-flight 的 sar_v3 函数面不在本轮范围。期望值全部由独立公式复算：
# β⁰=DN²/K、σ⁰=β⁰ sinθ、γ⁰=β⁰ tanθ、dB=10log₁₀、Lee MMSE、Frost 权重、
# ENL 矩估计、np.nan-* 时序统计、VV/VH 线性功率比值。

SC_MOD = "app.lib.geo_analysis.sar_calibration"
SF_MOD = "app.lib.geo_analysis.sar_filter"
ST_MOD = "app.lib.geo_analysis.sar_temporal"


def _ref_lee_cell(x, filled, valid, size, i, j, enl):
    """Lee MMSE 单像元独立参考（reflect 窗口统计 + k = var/(var+m²/ENL)）。"""
    filled = np.asarray(filled, dtype=float)
    valid = np.asarray(valid, dtype=bool)
    half = size // 2
    # scipy.ndimage 'reflect' = np.pad 'symmetric'（边缘像元复制）
    xp = np.pad(filled, half, mode="symmetric")
    vp = np.pad(valid.astype(float), half, mode="symmetric")
    win = xp[i:i + size, j:j + size]
    wv = vp[i:i + size, j:j + size]
    n = wv.sum()
    if n <= 0:
        return float("nan")
    mean = float((win * wv).sum() / n)
    var = float((((win - mean) * wv) ** 2).sum() / n)
    denom = var + (mean * mean) / enl
    k = var / denom if denom > 0 else 0.0
    return mean + k * (float(x[i, j]) - mean)


def _ref_frost_cell(x, filled, valid, size, i, j, enl, damping):
    """Frost 单像元独立参考（有效像元归一 exp(−k·d) 权重，zero-pad 偏移）。"""
    filled = np.asarray(filled, dtype=float)
    valid = np.asarray(valid, dtype=bool)
    h, w = filled.shape
    half = size // 2
    xp = np.pad(filled, half, mode="symmetric")
    vp = np.pad(valid.astype(float), half, mode="symmetric")
    win = xp[i:i + size, j:j + size]
    wv = vp[i:i + size, j:j + size]
    n = wv.sum()
    if n <= 0:
        return float("nan")
    mean = float((win * wv).sum() / n)
    var = float((((win - mean) * wv) ** 2).sum() / n)
    if not math.isfinite(mean) or mean <= 0:
        k = 0.0
    else:
        cv = math.sqrt(max(var, 0.0)) / mean
        cvf = 1.0 / math.sqrt(enl)
        k = damping * (cv / cvf) ** 2
    num = den = 0.0
    for dy in range(-half, half + 1):
        for dx in range(-half, half + 1):
            r, c = i + dy, j + dx
            if not (0 <= r < h and 0 <= c < w) or not valid[r, c]:
                continue
            weight = math.exp(-k * (abs(dy) + abs(dx)))
            num += weight * float(filled[r, c])
            den += weight
    return num / den if den > 0 else float("nan")


def build_sar() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    k_const = 4.0
    theta_deg = 30.0

    # --- β⁰ = (DN/K)²（振幅域先平方，披露）-------------------------------
    dn_amp = [[10.0, 20.0], [30.0, 40.0]]
    # I = DN²（振幅先平方）；β⁰ = I/K（≠ (DN/K)²——K 在平方之后除）
    beta_ref = np.asarray(dn_amp) ** 2 / k_const
    for i, j in ((0, 0), (0, 1), (1, 0), (1, 1)):
        cases.append(case(
            f"calibrate_beta0_amp_{i}{j}", f"{SC_MOD}:calibrate_sar",
            r9(float(beta_ref[i, j])), args=[dn_amp],
            kwargs={"calibration_constant": k_const,
                    "input_domain": "dn_amplitude",
                    "output_product": "beta0"},
            select=f"array.{i}.{j}", rtol=1e-12,
            note="β⁰ = DN²/K 独立复算（振幅先平方再除 K）"))
    # --- σ⁰ = β⁰·sinθ；γ⁰ = β⁰·tanθ --------------------------------------
    sin_t = math.sin(math.radians(theta_deg))
    tan_t = math.tan(math.radians(theta_deg))
    sigma_ref = beta_ref * sin_t
    gamma_ref = beta_ref * tan_t
    for i, j in ((0, 0), (0, 1), (1, 0), (1, 1)):
        cases.append(case(
            f"calibrate_sigma0_{i}{j}", f"{SC_MOD}:calibrate_sar",
            r9(float(sigma_ref[i, j])), args=[dn_amp],
            kwargs={"calibration_constant": k_const, "incidence_deg": theta_deg,
                    "input_domain": "dn_amplitude",
                    "output_product": "sigma0"},
            select=f"array.{i}.{j}", rtol=1e-12,
            note="σ⁰ = (DN²/K)·sin(θ) 独立复算"))
        cases.append(case(
            f"calibrate_gamma0_{i}{j}", f"{SC_MOD}:calibrate_sar",
            r9(float(gamma_ref[i, j])), args=[dn_amp],
            kwargs={"calibration_constant": k_const, "incidence_deg": theta_deg,
                    "input_domain": "dn_amplitude",
                    "output_product": "gamma0"},
            select=f"array.{i}.{j}", rtol=1e-12,
            note="γ⁰ = (DN²/K)·tan(θ) 独立复算"))
    # --- 强度域直通：β⁰ = I/K --------------------------------------------
    dn_int = [[8.0, 12.0]]
    beta_int = np.asarray(dn_int) / k_const
    for j in (0, 1):
        cases.append(case(
            f"calibrate_beta0_intensity_{j}", f"{SC_MOD}:calibrate_sar",
            r9(float(beta_int[0, j])), args=[dn_int],
            kwargs={"calibration_constant": k_const,
                    "input_domain": "dn_intensity",
                    "output_product": "beta0"},
            select=f"array.0.{j}", rtol=1e-12, note="强度域直通 I/K"))
    dn_int2 = [[5.0, 25.0]]
    beta_int2 = np.asarray(dn_int2) / k_const
    for j in (0, 1):
        cases.append(case(
            f"calibrate_beta0_intensity2_{j}", f"{SC_MOD}:calibrate_sar",
            r9(float(beta_int2[0, j])), args=[dn_int2],
            kwargs={"calibration_constant": k_const,
                    "input_domain": "dn_intensity",
                    "output_product": "beta0"},
            select=f"array.0.{j}", rtol=1e-12))
    # --- all 产品集 + dB 换算 ---------------------------------------------
    cases.append(case("calibrate_all_products", f"{SC_MOD}:calibrate_sar",
                      ["sigma0", "beta0", "gamma0"], args=[dn_amp],
                      kwargs={"calibration_constant": k_const,
                              "incidence_deg": theta_deg,
                              "input_domain": "dn_amplitude",
                              "output_product": "all"},
                      select="meta.products_computed", kind="exact"))
    cases.append(case("calibrate_primary_is_sigma0", f"{SC_MOD}:calibrate_sar",
                      "sigma0", args=[dn_amp],
                      kwargs={"calibration_constant": k_const,
                              "incidence_deg": theta_deg,
                              "input_domain": "dn_amplitude",
                              "output_product": "all"},
                      select="meta.primary_product", kind="exact"))
    db_ref = 10.0 * np.log10(sigma_ref)
    cases.append(case("calibrate_sigma0_db", f"{SC_MOD}:calibrate_sar",
                      r9(float(db_ref[0, 0])), args=[dn_amp],
                      kwargs={"calibration_constant": k_const,
                              "incidence_deg": theta_deg,
                              "input_domain": "dn_amplitude",
                              "output_product": "sigma0", "to_db": True},
                      select="array.0.0", rtol=1e-12,
                      note="dB = 10·log₁₀（强度量纲惯例）"))
    # --- 逐像元入射角平面 ---------------------------------------------------
    inc_map = [[30.0, 45.0], [60.0, 30.0]]
    sig_map_ref = np.asarray(dn_amp) ** 2 / k_const * np.sin(
        np.radians(np.asarray(inc_map)))
    cases.append(case("calibrate_incidence_map", f"{SC_MOD}:calibrate_sar",
                      r9(float(sig_map_ref[1, 0])), args=[dn_amp],
                      kwargs={"calibration_constant": k_const,
                              "incidence_map": inc_map,
                              "input_domain": "dn_amplitude",
                              "output_product": "sigma0"},
                      select="array.1.0", rtol=1e-12,
                      note="逐像元 σ⁰ = (DN²/K)·sin(θ_ij) 独立复算"))
    cases.append(case("calibrate_formula_text", f"{SC_MOD}:calibrate_sar",
                      "σ⁰ = (DN²/K)·sin(θ) 或 (DN/K)²·sin(θ)",
                      args=[dn_amp],
                      kwargs={"calibration_constant": k_const,
                              "incidence_deg": theta_deg,
                              "input_domain": "dn_amplitude",
                              "output_product": "sigma0"},
                      select="meta.formula", kind="exact"))
    # --- 定标错误族 ---------------------------------------------------------
    cases.append(error_case("calibrate_missing_k", f"{SC_MOD}:calibrate_sar",
                            "MISSING_REQUIRED_FIELD", args=[dn_amp],
                            kwargs={"calibration_constant": None,
                                    "incidence_deg": theta_deg},
                            note="K 显式缺省（JSON null）→ 拒绝虚构定标常数"))
    cases.append(error_case("calibrate_zero_k", f"{SC_MOD}:calibrate_sar",
                            "ValueError", args=[dn_amp],
                            kwargs={"calibration_constant": 0.0}))
    dn_amp_neg = [[-10.0, 20.0], [30.0, 40.0]]
    cases.append(error_case("calibrate_negative_dn", f"{SC_MOD}:calibrate_sar",
                            "UNSUPPORTED_METHOD", args=[dn_amp_neg],
                            kwargs={"calibration_constant": k_const,
                                    "input_domain": "dn_amplitude",
                                    "output_product": "beta0"},
                            note="负振幅物理无意义（拒绝前不做任何计算）"))
    cases.append(error_case("calibrate_incidence_over_90",
                            f"{SC_MOD}:calibrate_sar", "INVALID_UNITS",
                            args=[dn_amp],
                            kwargs={"calibration_constant": k_const,
                                    "incidence_deg": 95.0,
                                    "output_product": "sigma0"}))
    cases.append(error_case("calibrate_incidence_zero",
                            f"{SC_MOD}:calibrate_sar", "INVALID_UNITS",
                            args=[dn_amp],
                            kwargs={"calibration_constant": k_const,
                                    "incidence_deg": 0.0,
                                    "output_product": "sigma0"}))
    cases.append(error_case("calibrate_both_incidence_modes",
                            f"{SC_MOD}:calibrate_sar", "ValueError",
                            args=[dn_amp],
                            kwargs={"calibration_constant": k_const,
                                    "incidence_deg": 30.0,
                                    "incidence_map": inc_map,
                                    "output_product": "sigma0"}))
    cases.append(error_case("calibrate_sigma0_needs_incidence",
                            f"{SC_MOD}:calibrate_sar", "MISSING_REQUIRED_FIELD",
                            args=[dn_amp],
                            kwargs={"calibration_constant": k_const,
                                    "output_product": "sigma0"}))
    cases.append(error_case("calibrate_bad_product", f"{SC_MOD}:calibrate_sar",
                            "ValueError", args=[dn_amp],
                            kwargs={"calibration_constant": k_const,
                                    "output_product": "tau0"}))
    cases.append(error_case("calibrate_bad_domain", f"{SC_MOD}:calibrate_sar",
                            "ValueError", args=[dn_amp],
                            kwargs={"calibration_constant": k_const,
                                    "input_domain": "db"}))
    cases.append(error_case("calibrate_1d_input", f"{SC_MOD}:calibrate_sar",
                            "ValueError", args=[[1.0, 2.0]],
                            kwargs={"calibration_constant": k_const}))
    # --- 证据 facts 抽取 ----------------------------------------------------
    ev_result = {"meta": {"input_domain": "dn_amplitude",
                          "products_computed": ["beta0"]}, "to_db": False}
    cases.append(case("calibration_evidence_domain",
                      f"{SC_MOD}:calibration_evidence_facts", "dn_amplitude",
                      args=[ev_result],
                      select="calibration_input_domain", kind="exact"))
    cases.append(case("calibration_evidence_products",
                      f"{SC_MOD}:calibration_evidence_facts", "beta0",
                      args=[ev_result],
                      select="calibration_products", kind="exact"))
    cases.append(case("calibration_evidence_todb",
                      f"{SC_MOD}:calibration_evidence_facts", True,
                      args=[{"meta": {"input_domain": "dn_amplitude",
                                      "products_computed": ["beta0"]},
                             "to_db": True}],
                      select="calibration_to_db", kind="exact"))

    # --- Lee 滤波：手窗口独立循环参考 ---------------------------------------
    lee_hand = [[12.0, 13.0, 11.0], [14.0, 20.0, 12.0], [11.0, 12.0, 10.0]]
    valid3 = [[True] * 3 for _ in range(3)]
    for i, j in ((1, 1), (0, 0), (2, 2), (1, 0), (0, 2), (2, 1),
                 (0, 1), (1, 2), (2, 0)):
        want = _ref_lee_cell(np.asarray(lee_hand), lee_hand, valid3, 3,
                             i, j, 4.0)
        cases.append(case(
            f"lee_hand_window_{i}{j}", f"{SF_MOD}:speckle_filter",
            r9(want), args=[lee_hand, "lee"],
            kwargs={"window": 3, "enl": 4.0},
            select=f"array.{i}.{j}", rtol=1e-9,
            note="Lee 1980 MMSE 手窗口独立循环复算（reflect 窗口统计）"))
    # nodata 感知窗口统计
    rng_sar = np.random.default_rng(601)
    lee_nodata = [[float(v) for v in rng_sar.uniform(5, 20, 5)] for _ in range(5)]
    lee_nodata[2][2] = -9999.0  # 哨兵无效像元
    nod_mask = [[True] * 5 for _ in range(5)]
    nod_mask[2][2] = False
    lee_filled = [[(v if m else 0.0) for v, m in zip(row, mrow)]
                  for row, mrow in zip(lee_nodata, nod_mask)]
    for i, j in ((1, 1), (3, 3)):
        want = _ref_lee_cell(np.asarray(lee_filled), lee_filled, nod_mask, 5,
                             i, j, 8.0)
        cases.append(case(
            f"lee_nodata_window_{i}{j}", f"{SF_MOD}:speckle_filter",
            r9(want), args=[lee_nodata, "lee"],
            kwargs={"window": 5, "enl": 8.0, "nodata": -9999.0},
            select=f"array.{i}.{j}", rtol=1e-9,
            note="窗口统计只在有效像元上计算（nodata 哨兵，填充 0 不计入）"))
    const3 = [[5.0] * 3 for _ in range(3)]
    cases.append(case("lee_constant_field_identity", f"{SF_MOD}:speckle_filter",
                      5.0, args=[const3, "lee"], kwargs={"window": 3, "enl": 4.0},
                      select="array.1.1", kind="exact",
                      note="常数场 var=0 → k=0 → 输出 = 均值（诚实恒等）"))
    # --- ENL 矩估计 ---------------------------------------------------------
    vals_est = np.asarray(lee_nodata)[np.asarray(lee_nodata) != -9999.0]
    enl_ref = float(vals_est.mean() ** 2 / vals_est.var())
    cases.append(case("lee_enl_moment_estimate", f"{SF_MOD}:speckle_filter",
                      r9(enl_ref), args=[lee_nodata, "lee"],
                      kwargs={"window": 3, "nodata": -9999.0},
                      select="enl", rtol=1e-9,
                      note="ENL = mean²/var（总体方差 ddof=0；有效像元上）矩估计"))
    cases.append(case("lee_enl_source_estimated", f"{SF_MOD}:speckle_filter",
                      "estimated", args=[lee_nodata, "lee"],
                      kwargs={"window": 3, "nodata": -9999.0},
                      select="enl_source", kind="exact"))
    cases.append(case("lee_enl_source_explicit", f"{SF_MOD}:speckle_filter",
                      "explicit", args=[lee_nodata, "lee"],
                      kwargs={"window": 3, "enl": 8.0, "nodata": -9999.0},
                      select="enl_source", kind="exact"))
    # --- Frost：手窗口独立参考 + 常数恒等 + 阻尼契约 ------------------------
    for i, j in ((1, 1), (0, 1)):
        want = _ref_frost_cell(np.asarray(lee_hand), lee_hand, valid3, 3,
                               i, j, 4.0, 1.0)
        cases.append(case(
            f"frost_hand_window_{i}{j}", f"{SF_MOD}:speckle_filter",
            r9(want), args=[lee_hand, "frost"],
            kwargs={"window": 3, "enl": 4.0, "damping": 1.0},
            select=f"array.{i}.{j}", rtol=1e-9,
            note="Frost 1982 exp(−k·d) 权重独立循环复算"))
    cases.append(case("frost_constant_field_identity", f"{SF_MOD}:speckle_filter",
                      5.0, args=[const3, "frost"],
                      kwargs={"window": 3, "enl": 4.0, "damping": 2.0},
                      select="array.1.1", kind="exact",
                      note="常数场 CV=0 → k=0 → 权重全 1 → 输出 = 均值"))
    cases.append(case("refined_lee_constant_field_identity",
                      f"{SF_MOD}:speckle_filter", 5.0,
                      args=[const3, "refined_lee"],
                      kwargs={"window": 5, "enl": 8.0},
                      select="array.2.2", kind="exact"))
    cases.append(error_case("frost_damping_below_range",
                            f"{SF_MOD}:speckle_filter", "ValueError",
                            args=[lee_hand, "frost"],
                            kwargs={"window": 3, "enl": 4.0, "damping": 0.1}))
    cases.append(error_case("frost_damping_above_range",
                            f"{SF_MOD}:speckle_filter", "ValueError",
                            args=[lee_hand, "frost"],
                            kwargs={"window": 3, "enl": 4.0, "damping": 6.0}))
    # --- 滤波错误族 ---------------------------------------------------------
    cases.append(error_case("speckle_bad_window", f"{SF_MOD}:speckle_filter",
                            "ValueError", args=[lee_hand, "lee"],
                            kwargs={"window": 4, "enl": 4.0}))
    cases.append(error_case("speckle_bad_method", f"{SF_MOD}:speckle_filter",
                            "ValueError", args=[lee_hand, "median"],
                            kwargs={"enl": 4.0}))
    cases.append(error_case("speckle_negative_input_db",
                            f"{SF_MOD}:speckle_filter", "UNSUPPORTED_METHOD",
                            args=[[[-1.0, 2.0], [3.0, 4.0]], "lee"]))
    const_flat = [[3.0] * 4 for _ in range(4)]
    cases.append(error_case("speckle_constant_field_enl_undefined",
                            f"{SF_MOD}:speckle_filter", "DEGENERATE_DATA",
                            args=[const_flat, "lee"]))
    cases.append(error_case("speckle_1d_input", f"{SF_MOD}:speckle_filter",
                            "ValueError", args=[[1.0, 2.0], "lee"],
                            kwargs={"enl": 4.0}))

    # --- 时序栈统计：np.nan-* 独立复算 --------------------------------------
    stack = np.stack([np.full((2, 2), v) for v in
                      (1.0, 2.0, 3.0, 6.0, 4.0)])
    filled = stack.copy()
    prod_cases = (("mean", np.nanmean),
                  ("std", lambda a, axis: np.nanstd(a, axis)),
                  ("min", np.nanmin), ("max", np.nanmax),
                  ("range", lambda a, axis: np.nanmax(a, axis)
                   - np.nanmin(a, axis)))
    for prod, fn in prod_cases:
        ref = float(fn(filled, axis=0)[0, 0])
        cases.append(case(
            f"stack_{prod}_reference", f"{ST_MOD}:temporal_stack_statistics",
            r9(ref), args=[stack.tolist()], kwargs={"product": prod},
            select="array.0.0", rtol=1e-12,
            note="np.nan-统计独立复算" + ("（总体 std ddof=0）" if prod == "std" else "")))
        ref_b = float(fn(filled, axis=0)[1, 1])
        cases.append(case(
            f"stack_{prod}_pixel11", f"{ST_MOD}:temporal_stack_statistics",
            r9(ref_b), args=[stack.tolist()], kwargs={"product": prod},
            select="array.1.1", rtol=1e-12))
    # nodata 哨兵掩膜
    stack_nodata = stack.copy()
    stack_nodata[1, 0, 0] = -9999.0
    stack_nodata[3, 0, 0] = -9999.0
    ref_mean = float(np.nanmean(np.where(stack_nodata == -9999.0, np.nan,
                                         stack_nodata), axis=0)[0, 0])
    cases.append(case("stack_mean_nodata_masked",
                      f"{ST_MOD}:temporal_stack_statistics",
                      r9(ref_mean), args=[stack_nodata.tolist()],
                      kwargs={"product": "mean", "nodata": -9999.0},
                      select="array.0.0", rtol=1e-12,
                      note="哨兵值逐切片剔除后在其余有效切片统计"))
    cases.append(case("stack_partial_valid_meta",
                      f"{ST_MOD}:temporal_stack_statistics",
                      1, args=[stack_nodata.tolist()],
                      kwargs={"product": "mean", "nodata": -9999.0},
                      select="meta.pixels_partially_valid", kind="exact",
                      note="部分有效像元计数（独立计数）"))
    cases.append(case("stack_all_valid_meta", f"{ST_MOD}:temporal_stack_statistics",
                      3, args=[stack_nodata.tolist()],
                      kwargs={"product": "mean", "nodata": -9999.0},
                      select="meta.pixels_all_slices_valid", kind="exact"))
    # CV + 分位数
    mean_p = float(filled[:, 0, 0].mean())
    std_p = float(filled[:, 0, 0].std())
    cases.append(case("stack_cv_reference", f"{ST_MOD}:temporal_stack_statistics",
                      r9(std_p / mean_p), args=[stack.tolist()],
                      kwargs={"product": "mean", "include_cv": True},
                      select="cv.0.0", rtol=1e-12,
                      note="CV = std/mean（ddof=0）独立复算"))
    p25 = float(np.nanpercentile(filled[:, 0, 0], 25))
    cases.append(case("stack_p25_reference", f"{ST_MOD}:temporal_stack_statistics",
                      r9(p25), args=[stack.tolist()],
                      kwargs={"product": "mean", "percentiles": [25.0, 90.0]},
                      select="percentiles.p25.0.0", rtol=1e-12,
                      note="np.nanpercentile 线性插值法独立复算"))
    cases.append(case("stack_percentile_requested", f"{ST_MOD}:temporal_stack_statistics",
                      [25.0, 90.0], args=[stack.tolist()],
                      kwargs={"product": "mean", "percentiles": [25.0, 90.0]},
                      select="meta.percentiles_requested", kind="exact",
                      note="请求顺序保留"))
    # 时序统计错误族
    cases.append(error_case("stack_bad_product", f"{ST_MOD}:temporal_stack_statistics",
                            "ValueError", args=[stack.tolist()],
                            kwargs={"product": "median"}))
    big_stack = np.zeros((25, 2, 2))
    cases.append(error_case("stack_T_over_24", f"{ST_MOD}:temporal_stack_statistics",
                            "RESOURCE_SCALE_MISMATCH", args=[big_stack.tolist()],
                            kwargs={"product": "mean"}))
    cases.append(error_case("stack_percentiles_over_5",
                            f"{ST_MOD}:temporal_stack_statistics", "ValueError",
                            args=[stack.tolist()], kwargs={"product": "mean",
                                                           "percentiles": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]}))
    cases.append(error_case("stack_percentile_over_100",
                            f"{ST_MOD}:temporal_stack_statistics", "ValueError",
                            args=[stack.tolist()], kwargs={"product": "mean",
                                                           "percentiles": [101.0]}))
    cases.append(error_case("stack_2d_input", f"{ST_MOD}:temporal_stack_statistics",
                            "ValueError", args=[[[1.0, 2.0]]],
                            kwargs={"product": "mean"}))

    # --- 时序合成 -----------------------------------------------------------
    med_ref = float(np.nanmedian(filled[:, 0, 0]))
    cases.append(case("composite_median_reference", f"{ST_MOD}:temporal_composite",
                      r9(med_ref), args=[stack.tolist()], kwargs={"method": "median"},
                      select="array.0.0", rtol=1e-12,
                      note="np.nanmedian 独立复算"))
    cases.append(case("composite_mean_reference", f"{ST_MOD}:temporal_composite",
                      r9(mean_p), args=[stack.tolist()], kwargs={"method": "mean"},
                      select="array.0.0", rtol=1e-12))
    p75 = float(np.nanpercentile(filled[:, 0, 0], 75))
    cases.append(case("composite_p75_reference", f"{ST_MOD}:temporal_composite",
                      r9(p75), args=[stack.tolist()],
                      kwargs={"method": "percentile", "percentile": 75.0},
                      select="array.0.0", rtol=1e-12))
    cases.append(error_case("composite_percentile_needs_param",
                            f"{ST_MOD}:temporal_composite",
                            "MISSING_REQUIRED_FIELD", args=[stack.tolist()],
                            kwargs={"method": "percentile"}))
    cases.append(error_case("composite_bad_method", f"{ST_MOD}:temporal_composite",
                            "ValueError", args=[stack.tolist()],
                            kwargs={"method": "sum"}))
    cases.append(error_case("composite_percentile_range",
                            f"{ST_MOD}:temporal_composite", "ValueError",
                            args=[stack.tolist()],
                            kwargs={"method": "percentile", "percentile": 101.0}))

    # --- VV/VH 极化比（线性功率域）+ 对数比值 --------------------------------
    vv = [[2.0, 4.0], [6.0, 8.0]]
    vh = [[1.0, 2.0], [3.0, 0.0]]
    ratio_ref = np.asarray(vv) / np.asarray(vh)
    for i, j in ((0, 0), (1, 0)):
        cases.append(case(
            f"vh_ratio_{i}{j}", f"{ST_MOD}:vh_ratio", r9(float(ratio_ref[i, j])),
            args=[vv, vh], select=f"array.{i}.{j}", rtol=1e-12,
            note="线性功率域比值 vv/vh 独立复算"))
    cases.append(case("vh_ratio_zero_denominator_invalid",
                      f"{ST_MOD}:vh_ratio", 3, args=[vv, vh],
                      select="stats.valid_pixels", kind="exact",
                      note="VH=0 像元 NaN（有效像元计数独立判定）"))
    cases.append(case("vh_ratio_formula_text", f"{ST_MOD}:vh_ratio",
                      "vv / vh (linear power)", args=[vv, vh],
                      select="meta.formula", kind="exact"))
    cases.append(error_case("vh_ratio_negative_db_input",
                            f"{ST_MOD}:vh_ratio", "UNSUPPORTED_METHOD",
                            args=[[[-2.0, 4.0]], [[1.0, 2.0]]]))
    lg_ref = math.log(4.0) - math.log(2.0)
    cases.append(case("log_ratio_reference", f"{ST_MOD}:temporal_log_ratio_change",
                      r9(lg_ref), args=[[[4.0]], [[2.0]]],
                      select="array.0.0", rtol=1e-12,
                      note="log(a) − log(b) 独立复算"))
    cases.append(case("log_ratio_symmetry_meta", f"{ST_MOD}:temporal_log_ratio_change",
                      "log_ratio(a, b) == −log_ratio(b, a)",
                      args=[[[4.0]], [[2.0]]], select="meta.symmetry",
                      kind="exact"))

    # --- 分位数键名基元 ------------------------------------------------------
    for p_in, want in ((12.5, "p12.5"), (100.0, "p100"), (25.0, "p25"),
                       (0.5, "p0.5"), (90.0, "p90")):
        cases.append(case(f"percentile_key_{want}", f"{ST_MOD}:_percentile_key",
                          want, args=[p_in], kind="exact",
                          note="%g 格式键名"))
    cases.append(error_case("validated_percentiles_over_5",
                            f"{ST_MOD}:_validated_percentiles", "ValueError",
                            args=[[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]]))
    cases.append(error_case("validated_percentiles_negative",
                            f"{ST_MOD}:_validated_percentiles", "ValueError",
                            args=[[-1.0]]))

    return cases


# ── 域：edge_cases（类型化错误扫描 + 值语义边界）──────────────────────
# 跨稳定模块的守卫面：空输入 / 单点 / 全 nodata / 度-CRS 拒绝 / 超规模
# 先拒绝 / CRS 词表分类与 UTM 带估计（反经线、极区、带边界）/ NaN 传播
# 不变量（零分母 → NaN → 聚合 null）/ 精确重合恢复。

EC_CRS = "app.lib.gis.crs_safety"
EC_ID = "app.lib.geo_analysis.interpolation"
EC_SP = "app.lib.geo_analysis.spectral"
EC_TS = "app.lib.geo_analysis.trend_surface"
EC_KG = "app.lib.geo_analysis.kriging"


def build_edge_cases() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []

    # --- classify_crs 词表（地理 / 投影 / 局部度量 / 未知）-----------------
    classify_fixtures = [
        ("EPSG:3413", "projected_local_metric", "北极极方位度量投影按局部度量"),
        ("EPSG:3031", "projected_local_metric", "南极极方位度量投影"),
        ("EPSG:32601", "projected_local_metric", "UTM 北带 1（326xx 且 1≤nn≤60）"),
        ("EPSG:32760", "projected_local_metric", "UTM 南带 60"),
        ("EPSG:4269", "geographic", "NAD83 地理"),
        ("EPSG:4258", "geographic", "ETRS89 地理"),
        ("wgs84", "geographic", "词法 WGS84 规则"),
        ("CGCS2000", "geographic", "CGCS2000 词法规则"),
        ("+proj=longlat +datum=WGS84", "geographic", "proj4 串 pyproj 兜底"),
        ("WGS84 / UTM zone 50N", "projected_local_metric",
         "含 utm 跳过词法地理规则；pyproj 米制非 mercator → 局部度量"),
        ("EPSG:99999", "unknown", "pyproj 无法解析 → 诚实未知"),
        ("garbage:xyz", "unknown", "非 CRS 文本 → 未知"),
        ("", "unknown", "空串 → 未知"),
        (None, "unknown", "None → 未知（非字符串）"),
        ("epsg:4326", "geographic", "大小写不敏感 EPSG 前缀"),
        ("EPSG:3857", "projected", "Web Mercator 投影米但非局部度量"),
        ("EPSG:32701", "projected_local_metric", "UTM 南带 1"),
        ("EPSG:4490", "geographic", "CGCS2000 地理"),
        ("EPSG:2154", "projected_local_metric", "Lambert-93 米制投影"),
        ("EPSG:32600", "projected_local_metric",
         "带号 0 不中词法规则；pyproj 解析为米制投影 → 局部度量"),
        ("EPSG:4327", "geographic", "WGS84 地理（lat/long 顺序变体）"),
    ]
    for crs, want, note in classify_fixtures:
        tag = (crs or "none").replace(":", "").replace(" ", "_").replace(
            "+", "").replace("=", "_").lower()
        cases.append(case(f"classify_crs_{tag}", f"{EC_CRS}:classify_crs",
                          want, args=[crs], kind="exact", note=note))

    # --- recommend_metric_crs：UTM 带估计（反经线 / 极区 / 带边界）---------
    utm_fixtures = [
        ([116.0, 39.9, 117.0, 40.1], "EPSG:32650", "北京 → 50 带"),
        ([-0.1, 51.4, 0.1, 51.6], "EPSG:32631", "伦敦 → 中央经线 0° = 31 带"),
        ([151.0, -34.0, 152.0, -33.5], "EPSG:32756", "悉尼 → 南带 56"),
        ([-74.0, 40.6, -73.8, 40.9], "EPSG:32618", "纽约 → 18 带"),
        ([0.0, 85.0, 1.0, 86.0], "EPSG:3413", "北极 → UPS 北"),
        ([0.0, -85.0, 1.0, -84.0], "EPSG:3031", "南极 → UPS 南"),
        ([-179.0, 5.0, -178.0, 6.0], "EPSG:32601", "带 1 西缘"),
        ([179.0, 5.0, 180.0, 6.0], "EPSG:32660", "带 60 东缘"),
        ([179.5, 10.0, -179.5, 11.0], "EPSG:32631",
         "反经线 naive 中点 0° → 31 带（行为锚，非环绕解）"),
        ([12.0, 0.0, 13.0, 1.0], "EPSG:32633", "带边界 12° → 33 带"),
        ([-174.0, 0.0, -173.0, 1.0], "EPSG:32602", "带 2"),
        ([0.0, 84.0, 1.0, 84.5], "EPSG:3413",
         "clat=84.25 > 84 → 极区（开边界语义）"),
        ([], "", "非法 bbox → 空串由调用方处理"),
        ([1.0, 2.0], "", "长度 ≠ 4 → 空串"),
    ]
    for i, (bbox, want, note) in enumerate(utm_fixtures):
        cases.append(case(f"recommend_metric_crs_{i}", f"{EC_CRS}:recommend_metric_crs",
                          want, args=[bbox], kind="exact", note=note))

    # --- crs_class_allows：算法 CRS 类 × 数据类兼容谓词 --------------------
    allows_fixtures = [
        ("LOCAL_METRIC_REQUIRED", "projected", False),
        ("LOCAL_METRIC_REQUIRED", "projected_local_metric", True),
        ("LOCAL_METRIC_REQUIRED", "geographic", False),
        ("PROJECTED_REQUIRED", "projected", True),
        ("PROJECTED_REQUIRED", "geographic", False),
        ("GEODESIC", "geographic", True),
        ("GEOGRAPHIC_OK", "projected_local_metric", True),
        ("CRS_AGNOSTIC", "geographic", True),
        ("RASTER_GRID", "projected", True),
        ("LOCAL_METRIC_REQUIRED", "unknown", True),
        ("PROJECTED_REQUIRED", "unknown", True),
        ("", "geographic", True),
        ("GEOGRAPHIC_OK", "unknown", True),
        ("CRS_AGNOSTIC", "unknown", True),
        ("PROJECTED_REQUIRED", "projected_local_metric", True),
        ("LOCAL_METRIC_REQUIRED", "", False),
    ]
    for i, (kls, data, want) in enumerate(allows_fixtures):
        cases.append(case(f"crs_class_allows_{i}", f"{EC_CRS}:crs_class_allows",
                          want, args=[kls, data], kind="exact",
                          note="unknown 数据类永远放行（诚实缺省）"))

    # 注：_pick_metric_crs 首行即 lonlat[:, 1]（要求 ndarray 入参）——
    # JSON list 回放 AttributeError，无法入 corpus（运行器局限）。
    # --- H3 单元数估计 / 降分辨率建议（world_cells·面积占比 公式复算）------
    import h3 as _h3
    for tag, (dlon, dlat, res_) in (
            ("midlat_r8", (1.0, 1.0, 8)), ("world_band_r2", (300.0, 120.0, 2))):
        want = int(_h3.get_num_cells(res_)
                   * (abs(dlon) * abs(dlat) / 41253.0))
        cases.append(case(
            f"estimate_h3_cells_{tag}", f"{EC_ID}:_estimate_h3_cells", want,
            args=[0.0, 0.0, dlon, dlat, res_], kind="exact",
            note="world_cells × bbox 球面面积占比 独立复算"))
    # 巨型 bbox 在 res 8 的降分辨率建议（≤1.5M 上限内取 res-1/-2/-3）
    dlon, dlat = 60.0, 60.0
    suggest_ref = []
    for r_ in (7, 6, 5):
        est = int(_h3.get_num_cells(r_) * (abs(dlon) * abs(dlat) / 41253.0))
        if est <= 1_500_000:
            suggest_ref.append(r_)
    cases.append(case(
        "suggest_lower_resolutions_huge_bbox", f"{EC_ID}:_suggest_lower_resolutions",
        suggest_ref, args=[0.0, 0.0, dlon, dlat, 8], kind="exact",
        note="仅上限内的相邻低分辨率进入建议（独立复算）"))
    # 南极界语义（clat=-80 开边界）与 -80.5 极区
    cases.append(case("recommend_metric_crs_south_boundary",
                      f"{EC_CRS}:recommend_metric_crs", "EPSG:32731",
                      args=[[0.0, -80.0, 1.0, -79.5]], kind="exact",
                      note="clat=-80 不小于 -80 → UTM 南带"))
    cases.append(case("recommend_metric_crs_south_polar",
                      f"{EC_CRS}:recommend_metric_crs", "EPSG:3031",
                      args=[[0.0, -80.5, 1.0, -80.2]], kind="exact"))
    cases.append(case("recommend_metric_crs_zone6_boundary",
                      f"{EC_CRS}:recommend_metric_crs", "EPSG:32632",
                      args=[[6.0, 0.0, 7.0, 1.0]], kind="exact",
                      note="6° 经线恰为带界（(6+180)//6=31 → 32 带）"))
    cases.append(case("recommend_metric_crs_zone_west",
                      f"{EC_CRS}:recommend_metric_crs", "EPSG:32629",
                      args=[[-12.0, 0.0, -11.0, 1.0]], kind="exact",
                      note="(-12+180)//6=28 → 29 带"))
    cases.append(case("recommend_metric_crs_south_equator",
                      f"{EC_CRS}:recommend_metric_crs", "EPSG:32734",
                      args=[[18.0, -0.5, 19.0, 0.0]], kind="exact",
                      note="clat<0 → 南带；clon 18.5 → 34 带"))
    cases.append(case("recommend_metric_crs_east_zone",
                      f"{EC_CRS}:recommend_metric_crs", "EPSG:32645",
                      args=[[84.0, 10.0, 85.0, 11.0]], kind="exact",
                      note="(84+180)//6=44 → 45 带"))
    cases.append(case("recommend_metric_crs_far_east",
                      f"{EC_CRS}:recommend_metric_crs", "EPSG:32653",
                      args=[[132.0, 30.0, 133.0, 31.0]], kind="exact",
                      note="clon 132.5 → 53 带"))

    # --- NaN 传播不变量：光谱指数零分母 → NaN → 聚合 null -------------------
    red_p = [[0.4, 0.2], [0.3, 0.1]]
    nir_p = [[0.8, 0.6], [0.5, 0.0]]
    with np.errstate(invalid="ignore", divide="ignore"):
        nd = (np.asarray(nir_p) - np.asarray(red_p)) / (
            np.asarray(nir_p) + np.asarray(red_p))
    valid_frac = float(np.isfinite(nd).mean())
    cases.append(case("gndvi_zero_denominator_valid_fraction",
                      f"{EC_SP}:compute_spectral_index",
                      r9(valid_frac),
                      args=[{"red": red_p, "nir": nir_p}, "ndvi"],
                      select="valid_pixel_fraction", rtol=1e-7,
                      note="零分母像元 → NaN（诚实无效，不计入有限像元）"))
    cases.append(case("gndvi_zero_denominator_mean_null",
                      f"{EC_SP}:compute_spectral_index",
                      r9(float(np.nanmean(nd))),
                      args=[{"red": red_p, "nir": nir_p}, "ndvi"],
                      select="mean:array", rtol=1e-7,
                      note="mean 聚合跳过 NaN（全 NaN → null 的对偶）"))
    cases.append(case("ndvi_all_zero_denominator_all_nan",
                      f"{EC_SP}:compute_spectral_index",
                      None,
                      args=[{"red": [[0.0, 0.0], [0.0, 0.0]],
                             "nir": [[0.0, 0.0], [0.0, 0.0]]}, "ndvi"],
                      select="mean:array", kind="exact",
                      note="全零输入 → 全 NaN → mean:array = null"))

    # --- 空输入 / 单点类型化拒绝 ---------------------------------------------
    cases.append(error_case("idw_parse_empty_fc", f"{EC_ID}:_parse_point_values",
                            "ValueError",
                            args=[{"type": "FeatureCollection", "features": []},
                                  "v"]))
    cases.append(error_case("idw_parse_missing_field", f"{EC_ID}:_parse_point_values",
                            "ValueError",
                            args=[{"type": "FeatureCollection", "features": [
                                {"type": "Feature",
                                 "geometry": {"type": "Point",
                                              "coordinates": [0.0, 0.0]},
                                 "properties": {}}]}, "v"]))
    cases.append(error_case("nn_interpolation_empty", f"{EC_ID}:nearest_neighbor_interpolation",
                            "INSUFFICIENT_SAMPLES", args=[[], [], [[0.0, 0.0]]]))
    cases.append(error_case("trend_single_point", f"{EC_TS}:trend_predict",
                            "INSUFFICIENT_SAMPLES",
                            args=[[[1.0, 1.0]], [1.0], [[2.0, 2.0]], 1]))
    cases.append(error_case("kriging_single_sample", f"{EC_KG}:ordinary_kriging",
                            "INSUFFICIENT_SAMPLES",
                            args=[[[0.0, 0.0]], [1.0], [[2.0, 2.0]], None],
                            note="n=1 在任何克里金计算前类型化拒绝"))

    # --- 度-CRS 拒绝（克里金驱动） -------------------------------------------
    fc_deg = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 40.0]},
         "properties": {"v": float(i)}} for i in range(10)]}
    cases.append(error_case(
        "kriging_declares_projected_but_degrees", f"{EC_KG}:kriging_interpolation",
        "KrigingInputError", args=[fc_deg, "v"],
        kwargs={"resolution": 7, "declared_crs": "EPSG:32650"},
        note="声明投影 CRS 但坐标是度 → 类型化拒绝（度≠米语义）"))
    cases.append(error_case(
        "kriging_unknown_declared_crs", f"{EC_KG}:kriging_interpolation",
        "KrigingCrsError", args=[fc_deg, "v"],
        kwargs={"resolution": 7, "declared_crs": "EPSG:99999999"}))

    # --- 超规模先拒绝（H3 单元上限；不 OOM） ----------------------------------
    fc_huge = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": c},
         "properties": {"v": float(i)}}
        for i, c in enumerate([(0.0, 0.0), (60.0, 60.0)])]}
    cases.append(error_case(
        "idw_h3_cell_ceiling_rejected", f"{EC_ID}:idw_surface",
        "InterpolationResourceExceededError", args=[fc_huge, "v"],
        kwargs={"resolution": 7, "cross_validate": False},
        note="世界尺度 bbox 的 polyfill 估计超 150 万单元上限（附建议分辨率）"))
    cases.append(error_case(
        "nn_h3_cell_ceiling_rejected", f"{EC_ID}:nearest_neighbor_surface",
        "InterpolationResourceExceededError", args=[fc_huge, "v"],
        kwargs={"resolution": 7}))

    # --- 幂次 / H3 分辨率契约 -------------------------------------------------
    for p_in, want in ((5.0, 5.0), (2.5, 2.5), (True, 1.0)):
        cases.append(case(
            f"validate_power_ok_{str(p_in).lower()}", f"{EC_ID}:_validate_power",
            want, args=[p_in], kind="exact",
            note="闭区间 (0,5] 直通（True → float 1.0 的诚实直通）"))
    for bad in (0.0, -2.0, "abc", 6.0):
        cases.append(error_case(
            f"validate_power_rejects_{str(bad).replace('.', 'p')}",
            f"{EC_ID}:_validate_power", "UNSUPPORTED_METHOD", args=[bad]))
    for r_in in (0, 15, 7):
        cases.append(case(f"validate_resolution_ok_{r_in}",
                          f"{EC_ID}:_validate_resolution", None, args=[r_in],
                          kind="exact", note="界内 → 静默放行（返回 None）"))
    for bad in (16, True, 8.5, "7"):
        tag = str(bad).replace('.', 'p').lower()
        cases.append(error_case(
            f"validate_resolution_rejects_{tag}",
            f"{EC_ID}:_validate_resolution", "ValueError", args=[bad],
            note="bool/浮点/字符串分辨率均拒绝（显式 int 契约）"))

    return cases




# ── science-v5：可验证可扩展科学计算（CV/批量求解器/不确定性/物候/水文）──

def build_science_v5() -> List[Dict[str, Any]]:
    """science-v5 oracle 域（Review R1-M2 修订：全部绑定**生产目标**）。

    回放契约 = "import target → run → compare"：每个 case 的 target 都是
    生产函数（含 JSON 原生驱动），期望值由生成期黄金路径计算后经
    ``select`` 路径冻结。零 ``_identity`` 哑弹（R1 发现 33/35 case 永假
    真的问题在此修正——哑弹 case 已删除，数值网移回生产绑定）。
    """
    import numpy as np

    cases: List[Dict[str, Any]] = []

    # ── CV：temporal_forward 前向链（生产目标 + 精确折分配锚）────────
    times = [0.0, 0.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 4.0]
    from app.lib.geo_analysis.cv import temporal_forward_folds

    fold_id, block_id = temporal_forward_folds(np.asarray(times), 3)
    cases.append(case(
        "cv_temporal_forward_fold_sum",
        "app.lib.geo_analysis.cv:temporal_forward_folds",
        int(fold_id.sum()),
        args=[times, 3], select="sum:0", kind="exact",
        note="folds=3 同值时间组：块边界只落唯一值边界（fold 数组求和冻结）",
    ))
    cases.append(case(
        "cv_temporal_forward_block_sum",
        "app.lib.geo_analysis.cv:temporal_forward_folds",
        int(block_id.sum()),
        args=[times, 3], select="sum:1", kind="exact",
    ))
    cases.append(case(
        "cv_temporal_forward_first_test_sample",
        "app.lib.geo_analysis.cv:temporal_forward_folds",
        int(fold_id[4]),
        args=[times, 3], select="0.4", kind="exact",
        note="块 0 纯训练库 → 首个 fold=0 的样本在切片 4",
    ))
    cases.append(case(
        "cv_temporal_forward_folds_gt_unique",
        "app.lib.geo_analysis.cv:temporal_forward_folds", None,
        args=[times, 11], kind="error",
        note="folds > unique 时间值数 → 类型化拒绝",
    ))

    # ── 批量 LMC：surface 驱动（JSON 原生；内部 fit_lmc 黄金路径）─────
    from app.lib.geo_analysis.cokriging_lmc import cokriging_lmc_surface

    def _fc(points):
        feats = []
        for lon, lat, props in points:
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": dict(props),
            })
        return {"type": "FeatureCollection", "features": feats}

    rng = np.random.default_rng(42)
    xy1 = np.column_stack([
        rng.uniform(116.0, 116.08, 60), rng.uniform(39.0, 39.08, 60)])
    field = rng.normal(0, 1.0, 60)
    z1 = 10.0 + 3.0 * field + rng.normal(0, 0.3, 60)
    xy2 = np.column_stack([
        rng.uniform(116.0, 116.08, 50), rng.uniform(39.0, 39.08, 50)])
    from scipy.spatial import cKDTree

    tree1 = cKDTree(xy1)
    _d, idx = tree1.query(xy2, k=1)
    z2 = 5.0 + 2.0 * (0.8 * field[idx]
                      + np.sqrt(1 - 0.64) * rng.normal(0, 1, 50))
    primary = _fc([(float(a), float(b), {"v": float(v)})
                   for (a, b), v in zip(xy1, z1)])
    secondary = _fc([(float(a), float(b), {"w": float(v)})
                     for (a, b), v in zip(xy2, z2)])
    res = cokriging_lmc_surface(primary, "v", secondary, "w", resolution=7)
    meta = res["metadata"]
    cases.append(case(
        "lmc_driver_rho", "app.lib.geo_analysis.cokriging_lmc:cokriging_lmc_surface",
        r9(meta["lmc"]["rho"]),
        args=[primary, "v", secondary, "w", 7],
        kwargs={"neighbors1": 12, "neighbors2": 8},
        select="metadata.lmc.rho", rtol=1e-9,
        note="内部 fit_lmc + 批量堆叠求解黄金路径（挑战 R0-#3 语义）",
    ))
    cases.append(case(
        "lmc_driver_variance_max", "app.lib.geo_analysis.cokriging_lmc:cokriging_lmc_surface",
        r9(meta["variance_range"][1]),
        args=[primary, "v", secondary, "w", 7],
        kwargs={"neighbors1": 12, "neighbors2": 8},
        select="metadata.variance_range.1",
    ))
    cases.append(case(
        "lmc_driver_estimator", "app.lib.geo_analysis.cokriging_lmc:cokriging_lmc_surface",
        meta["uncertainty"]["estimator"],
        args=[primary, "v", secondary, "w", 7],
        kwargs={"neighbors1": 12, "neighbors2": 8},
        select="metadata.uncertainty.estimator", kind="exact",
    ))
    cases.append(case(
        "lmc_driver_execution_plan", "app.lib.geo_analysis.cokriging_lmc:cokriging_lmc_surface",
        meta["execution_plan"]["variant_id"],
        args=[primary, "v", secondary, "w", 7],
        kwargs={"neighbors1": 12, "neighbors2": 8},
        select="metadata.execution_plan.variant_id", kind="exact",
    ))

    # ── 批量 SGS：surface 驱动（backend=auto → numpy_batched 决策锚）──
    from app.lib.geo_analysis.kriging_simulation import sgs_simulation_surface

    rng = np.random.default_rng(7)
    g_xy = np.column_stack([
        rng.uniform(116.0, 116.05, 36), rng.uniform(39.0, 39.05, 36)])
    g_z = 20 + 5 * np.sin(g_xy[:, 0] * 1e4) + rng.normal(0, 0.3, 36)
    fc = _fc([(float(a), float(b), {"v": float(v)})
              for (a, b), v in zip(g_xy, g_z)])
    out = sgs_simulation_surface(fc, "v", resolution=7,
                                 n_realizations=12, seed=42, neighbors=8)
    ometa = out["metadata"]
    cases.append(case(
        "sgs_driver_backend", "app.lib.geo_analysis.kriging_simulation:sgs_simulation_surface",
        ometa["backend"],
        args=[fc, "v", 7, 12, 42, 8],
        select="metadata.backend", kind="exact",
        note="auto → plan_execution(raster_cells) → numpy_batched 决策链",
    ))
    cases.append(case(
        "sgs_driver_std_max", "app.lib.geo_analysis.kriging_simulation:sgs_simulation_surface",
        r9(ometa["ensemble_std_range"][1]),
        args=[fc, "v", 7, 12, 42, 8],
        select="metadata.ensemble_std_range.1",
    ))
    cases.append(case(
        "sgs_driver_uncertainty_estimator",
        "app.lib.geo_analysis.kriging_simulation:sgs_simulation_surface",
        ometa["uncertainty"]["estimator"],
        args=[fc, "v", 7, 12, 42, 8],
        select="metadata.uncertainty.estimator", kind="exact",
    ))

    # ── 批量 ST：surface 驱动 ──────────────────────────────────────────
    from app.lib.geo_analysis.kriging_st import st_kriging_surface

    rng = np.random.default_rng(5)
    t0 = 1_700_000_000.0
    feats = []
    for i in range(40):
        s = i % 10
        tt = t0 + (i // 10) * 86400
        v = 10 + 0.5 * s + 0.1 * (i // 10) + float(
            rng.normal(0, 0.2))
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point",
                                   "coordinates": [116.0 + s * 0.01,
                                                   39.0 + s * 0.008]},
                      "properties": {"v": v, "t": float(tt)}})
    st_res = st_kriging_surface(
        {"type": "FeatureCollection", "features": feats}, "v", "t",
        target_time_sec=t0 + 2 * 86400, resolution=7,
        temporal_range_sec=5 * 86400)
    st_meta = st_res["metadata"]
    cases.append(case(
        "st_driver_variance_max", "app.lib.geo_analysis.kriging_st:st_kriging_surface",
        r9(st_meta["variance_range"][1]),
        args=[{"type": "FeatureCollection", "features": feats}, "v", "t",
              t0 + 2 * 86400, 7],
        kwargs={"temporal_range_sec": 5 * 86400.0},
        select="metadata.variance_range.1",
    ))
    cases.append(case(
        "st_driver_estimator", "app.lib.geo_analysis.kriging_st:st_kriging_surface",
        st_meta["uncertainty"]["estimator"],
        args=[{"type": "FeatureCollection", "features": feats}, "v", "t",
              t0 + 2 * 86400, 7],
        kwargs={"temporal_range_sec": 5 * 86400.0},
        select="metadata.uncertainty.estimator", kind="exact",
    ))
    cases.append(case(
        "st_driver_plan_variant", "app.lib.geo_analysis.kriging_st:st_kriging_surface",
        st_meta["execution_plan"]["variant_id"],
        args=[{"type": "FeatureCollection", "features": feats}, "v", "t",
              t0 + 2 * 86400, 7],
        kwargs={"temporal_range_sec": 5 * 86400.0},
        select="metadata.execution_plan.variant_id", kind="exact",
    ))

    # ── 物候 / 异常：from_arrays 生产适配器（正弦手算锚）────────────────
    from app.lib.geo_analysis.phenology import (
        phenology_features_from_arrays,
        temporal_anomaly_from_arrays,
    )

    t_len = 24
    t_norm = np.arange(t_len) / t_len
    base = 0.1 + 0.2 * np.sin(2 * np.pi * t_norm)
    stack = np.broadcast_to(base[:, None, None], (t_len, 3, 3)).copy()
    times_h = (np.arange(t_len) * 3600.0).tolist()
    pf = phenology_features_from_arrays(stack.tolist(), times_h, window=5)
    f = pf["features"]
    cases.append(case(
        "pheno_sine_sos",
        "app.lib.geo_analysis.phenology:phenology_features_from_arrays",
        float(f["sos_idx"][0, 0]),
        args=[stack.tolist(), times_h], kwargs={"window": 5},
        select="features.sos_idx.0.0", kind="exact",
        note="正弦 0.1+0.2sin：thr=0.1 → 首越切片 1；SG 平滑后峰值 t=6",
    ))
    cases.append(case(
        "pheno_sine_peak_time",
        "app.lib.geo_analysis.phenology:phenology_features_from_arrays",
        float(f["peak_time"][0, 0]),
        args=[stack.tolist(), times_h], kwargs={"window": 5},
        select="features.peak_time.0.0", kind="exact",
    ))
    cases.append(case(
        "pheno_sine_amplitude",
        "app.lib.geo_analysis.phenology:phenology_features_from_arrays",
        r9(float(f["amplitude"][0, 0])),
        args=[stack.tolist(), times_h], kwargs={"window": 5},
        select="features.amplitude.0.0",
    ))
    a_stack = np.ones((12, 2, 2))
    a_stack[-1] = 3.0
    an = temporal_anomaly_from_arrays(a_stack.tolist(), np.arange(12.0).tolist())
    cases.append(case(
        "anomaly_z_hand_anchor",
        "app.lib.geo_analysis.phenology:temporal_anomaly_from_arrays",
        r9(float(an["features"]["anomaly_last"][0, 0])),
        args=[a_stack.tolist(), np.arange(12.0).tolist()],
        select="features.anomaly_last.0.0",
        note="11×1 + 1×3：mean=7/6、std=√(1/3)=0.57735 → z=3.17543",
    ))
    cases.append(case(
        "cube_over_512_slices_rejected",
        "app.lib.geo_analysis.temporal_cube:build_cube", None,
        args=[np.zeros((513, 2, 2)).tolist(), list(range(513))],
        kind="error",
        note="时间片 >512 → ResourceScaleMismatch（先拒绝不 OOM）",
    ))

    # ── 多级 Pfafstetter + 拓扑：生产函数（d8 数组 JSON 内嵌）──────────
    from app.lib.geo_analysis.terrain import (
        d8_flow,
        fill_depressions,
        flow_accumulation,
        pfafstetter_codes_multilevel,
        validate_flow_topology,
    )

    def _basin(n: int, seed: int) -> np.ndarray:
        rr = np.random.default_rng(seed)
        yy, xx = np.mgrid[0:n, 0:n]
        zz = 80.0 - 0.5 * xx - 0.3 * yy + 0.05 * rr.normal(size=(n, n))
        zz[10:30, 10:30] -= 3.0
        zz[40:70, 20:65] -= 2.0
        return zz.astype(float)

    z = _basin(64, 3)
    filled, _ = fill_depressions(z, 30.0)
    d8, _ = d8_flow(filled, 30.0)
    acc, _ = flow_accumulation(d8)
    d8_json = {
        "direction": d8["direction"].tolist(),
        "receiver": d8["receiver"].tolist(),
        "valid": d8["valid"].tolist(),
    }
    acc_list = acc.tolist()
    rr_i, cc_i = np.unravel_index(int(np.argmax(acc)), acc.shape)
    outlet = [int(rr_i), int(cc_i)]
    codes2, m2 = pfafstetter_codes_multilevel(
        d8, acc, 30.0, (outlet[0], outlet[1]), levels=2)
    cases.append(case(
        "pfaf_ml_distinct_codes",
        "app.lib.geo_analysis.terrain:pfafstetter_codes_multilevel",
        int(m2["distinct_codes"]),
        args=[d8_json, acc_list, 30.0, outlet],
        kwargs={"levels": 2}, select="1.distinct_codes", kind="exact",
    ))
    cases.append(case(
        "pfaf_ml_code_distribution",
        "app.lib.geo_analysis.terrain:pfafstetter_codes_multilevel",
        {str(k): int(v) for k, v in m2["code_distribution"].items()},
        args=[d8_json, acc_list, 30.0, outlet],
        kwargs={"levels": 2}, select="1.code_distribution", kind="exact",
        note="码分布冻结（含二位码结构：父×10+位，位 ≤8）",
    ))
    cases.append(case(
        "pfaf_ml_sample_cell_code",
        "app.lib.geo_analysis.terrain:pfafstetter_codes_multilevel",
        int(codes2[outlet[0], outlet[1]]),
        args=[d8_json, acc_list, 30.0, outlet],
        kwargs={"levels": 2},
        select=f"0.{outlet[0]}.{outlet[1]}",
        kind="exact",
        note="出口像元位码（level-1 干流段）",
    ))
    topo, _ = validate_flow_topology(d8, acc)
    cases.append(case(
        "topo_is_consistent",
        "app.lib.geo_analysis.terrain:validate_flow_topology",
        bool(topo["is_consistent"]),
        args=[d8_json, acc_list],
        select="0.is_consistent", kind="exact",
        note="epsilon 填洼后合成流域：拓扑一致汇总位",
    ))
    cases.append(case(
        "topo_cycle_count",
        "app.lib.geo_analysis.terrain:validate_flow_topology",
        int(topo["cycles"]),
        args=[d8_json, acc_list],
        select="0.cycles", kind="exact",
    ))
    cases.append(case(
        "topo_accumulation_violations",
        "app.lib.geo_analysis.terrain:validate_flow_topology",
        int(topo["accumulation_violations"]),
        args=[d8_json, acc_list],
        select="0.accumulation_violations", kind="exact",
    ))

    return cases


BUILDERS: Dict[str, Callable[[], List[Dict[str, Any]]]] = {
    "point_pattern": build_point_pattern,
    "spectral": build_spectral,
    "crs_units": build_crs_units,
    "statistics_global": build_statistics_global,
    "statistics_local": build_statistics_local,
    "geodetector": build_geodetector,
    "regression": build_regression,
    "terrain": build_terrain,
    "network": build_network,
    "geostat": build_geostat,
    "sar": build_sar,
    "edge_cases": build_edge_cases,
    "science_v5": build_science_v5,
}


def write_domain(name: str, cases: List[Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"domain": name, "count": len(cases), "cases": cases}
    out = DATA_DIR / f"{name}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1,
                              sort_keys=True, allow_nan=False) + "\n")
    print(f"wrote {out.relative_to(ROOT)} ({len(cases)} cases)")


def main(argv: List[str]) -> int:
    wanted = argv[1:] or list(BUILDERS)
    unknown = [w for w in wanted if w not in BUILDERS]
    if unknown:
        print(f"unknown domains: {unknown}; available: {sorted(BUILDERS)}")
        return 2
    total = 0
    for name in wanted:
        cases = BUILDERS[name]()
        write_domain(name, cases)
        total += len(cases)
    print(f"total cases: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
