"""科学评价原语回归锁（method-experiment WP3）—— 独立 oracle 对照。

红线落实：块 CV 的 IDW 结果对照**测试内独立实现**的 numpy IDW
（不 import 生产内核）；KDE 守恒用解析可验证的合成输入；聚类稳定性
用可预测的聚类结构 fixture。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.tools.registry import ToolRegistry
from app.tools.science_eval_primitives import (
    register_science_eval_primitive_tools,
)


@pytest.fixture(scope="module")
def registry():
    r = ToolRegistry()
    register_science_eval_primitive_tools(r)
    return r


def _get(registry, name):
    spec = registry.get(name)
    assert spec is not None, f"{name} 未注册"
    return spec.func if hasattr(spec, "func") else spec


def _linear_surface_fc(n=40, seed=11):
    """解析线性面 z = 3x + 2y（米制 UTM48N 内近常数斜率），插值法应能
    高保真重构 —— 独立 oracle：各方法 CV RMSE ≪ 值域跨度。"""
    rng = np.random.default_rng(seed)
    lons = 104.0 + rng.uniform(-0.03, 0.03, n)
    lats = 30.6 + rng.uniform(-0.03, 0.03, n)
    # 局部线性近似（小范围lon/lat→米比例 ~ cos(30.6°)）
    mx = (lons - 104.0) * 111_320 * np.cos(np.deg2rad(30.6))
    my = (lats - 30.6) * 110_570
    vals = 3.0 * mx + 2.0 * my
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [float(lo), float(la)]},
         "properties": {"z": float(v)}}
        for lo, la, v in zip(lons, lats, vals)]}


def _clustered_fc():
    """三个紧致高斯簇 + 少量噪声点 —— 聚类稳定性应给出高 ARI。"""
    rng = np.random.default_rng(5)
    centers = [(104.00, 30.60), (104.05, 30.60), (104.025, 30.645)]
    feats = []
    for cx, cy in centers:
        for _ in range(15):
            lo = cx + rng.normal(0, 0.002)
            la = cy + rng.normal(0, 0.002)
            feats.append((lo, la))
    for _ in range(3):
        feats.append((104.0 + rng.uniform(-0.02, 0.07),
                      30.59 + rng.uniform(0, 0.08)))
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [float(lo), float(la)]},
         "properties": {}}
        for lo, la in feats]}


# ── 插值分块 CV ─────────────────────────────────────────────────────────

def test_block_cv_linear_surface_oracle(registry):
    """线性面 + 平滑趋势：trend/rbf 的 CV 误差应远小于值域跨度（独立
    oracle：z 值域 ≈ 3×6.6km+2×6.6km ≈ 3.3 万量级；重构误差应 <5%）。"""
    fn = _get(registry, "interpolation_block_cv")
    fc = _linear_surface_fc()
    out = fn(fc, "z", method="trend_surface", folds=4)
    assert out.get("success") is True
    rmse = out.get("rmse")
    assert rmse is not None
    span = max(f["properties"]["z"] for f in fc["features"]) - min(
        f["properties"]["z"] for f in fc["features"])
    assert rmse < 0.05 * span, f"线性面重构误差过大: rmse={rmse}, span={span}"
    assert out["folds_used"] >= 2
    assert out["scheme"] == "spatial_block"


def _oracle_idw_block_cv(lonlat, vals, power=2.0, folds=4, scheme="index"):
    """独立 oracle：确定性折 + 独立 IDW 预测（k=5, 精确命中）。

    折分配独立重实现（index = arange % folds；spatial_block = x/y 双
    argsort 稳定秩 → 分位数 ⌈√folds⌉ 网格块 → block % folds，与 cv.py
    同规格、代码独立）；IDW 内核独立编写；CRS 硬编码 UTM 48N（fixture
    经度 ∈ (102,108)）。
    """
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32648", always_xy=True)
    xs, ys = tr.transform(lonlat[:, 0], lonlat[:, 1])
    pts = np.column_stack([xs, ys])
    n = len(vals)
    folds = max(2, min(folds, n // 4))
    if scheme == "index":
        fold = np.arange(n) % folds
    else:
        n_grid = int(np.ceil(np.sqrt(folds)))
        rx = np.argsort(np.argsort(pts[:, 0], kind="stable"), kind="stable")
        ry = np.argsort(np.argsort(pts[:, 1], kind="stable"), kind="stable")
        gx = (rx * n_grid) // max(n, 1)
        gy = (ry * n_grid) // max(n, 1)
        block = gy * n_grid + gx
        fold = block % folds
    errs = []
    for f in np.unique(fold):
        test = fold == f
        train = ~test
        if not train.any() or not test.any():
            continue
        tp, tv = pts[train], vals[train]
        sp = pts[test]
        for i in range(len(sp)):
            d = np.linalg.norm(tp - sp[i], axis=1)
            j = int(np.argmin(d))
            if d[j] < 1e-9:
                pred = tv[j]
            else:
                k = min(5, len(tv))
                idx = np.argsort(d)[:k]
                w = 1.0 / np.maximum(d[idx] ** power, 1e-30)
                pred = float((w * tv[idx]).sum() / w.sum())
            errs.append(pred - vals[test][i])
    errs = np.array(errs)
    return float(np.sqrt(np.mean(errs ** 2)))


def test_block_cv_idw_matches_independent_oracle(registry):
    fn = _get(registry, "interpolation_block_cv")
    fc = _linear_surface_fc()
    out = fn(fc, "z", method="idw", power=2.0, folds=4, scheme="index")
    assert out.get("rmse") is not None
    assert out.get("metric_crs") == "EPSG:32648", (
        f"CRS 选择与 oracle 假设不符: {out.get('metric_crs')}")
    lonlat = np.array([f["geometry"]["coordinates"]
                       for f in fc["features"]])
    vals = np.array([f["properties"]["z"] for f in fc["features"]])
    oracle = _oracle_idw_block_cv(lonlat, vals, power=2.0, folds=4,
                                  scheme="index")
    # 工具与 oracle 同折分配同内核 → 相对偏差 <1%（浮点/CRS 细节容差）
    assert abs(out["rmse"] - oracle) / oracle < 0.01, (
        f"tool={out['rmse']} vs oracle={oracle}")


def test_block_cv_rejects_unknown_method(registry):
    fn = _get(registry, "interpolation_block_cv")
    fc = _linear_surface_fc(n=8)
    out = fn(fc, "z", method="magic_interp")
    assert out.get("success") is False
    assert "unknown_method" in out.get("error", "")


def test_block_cv_insufficient_samples(registry):
    fn = _get(registry, "interpolation_block_cv")
    fc = _linear_surface_fc(n=1)
    out = fn(fc, "z", method="idw")
    assert out.get("success") is False
    assert "insufficient" in out.get("error", "")


# ── 密度守恒/边界诊断 ───────────────────────────────────────────────────

def test_density_conservation_holds_within_tolerance(registry):
    """解析 oracle：KDE 全域积分=1 → 恢复点数 ≈ n；网格离散误差应 <10%。"""
    fn = _get(registry, "density_edge_diagnostic")
    fc = _linear_surface_fc(n=50, seed=3)
    out = fn(fc, method="kde_fixed", bandwidth_m=800.0)
    assert out.get("success") is True
    n = out["n_points"]
    assert abs(out["integral_mass"] - n) / n < 0.10, (
        f"守恒误差过大: mass={out['integral_mass']} vs n={n}")
    assert 0.0 <= out["mass_error_ratio"] < 0.10
    assert out["edge_mass_share"] is not None
    assert out["grid_facts"]["nx"] <= 128


def test_density_wider_bandwidth_increases_edge_spill(registry):
    """带宽越大 → 研究区外质量外泄越多（边界效应单调性的物理常识）。"""
    fn = _get(registry, "density_edge_diagnostic")
    fc = _linear_surface_fc(n=50, seed=3)
    small = fn(fc, method="kde_fixed", bandwidth_m=300.0)
    large = fn(fc, method="kde_fixed", bandwidth_m=5000.0)
    assert small["edge_mass_share"] is not None
    assert large["edge_mass_share"] is not None
    assert large["edge_mass_share"] >= small["edge_mass_share"]


def test_density_rejects_tiny_input(registry):
    fn = _get(registry, "density_edge_diagnostic")
    fc = _linear_surface_fc(n=2)
    out = fn(fc, method="kde_fixed", bandwidth_m=1000.0)
    assert out.get("success") is False


# ── 聚类稳定性 ──────────────────────────────────────────────────────────

def test_cluster_stability_three_gaussian_clusters(registry):
    """可预测结构：三紧致簇 → kmeans(k=3) 跨 seed 高 ARI、高轮廓。"""
    fn = _get(registry, "cluster_stability")
    fc = _clustered_fc()
    out = fn(fc, method="kmeans", k=3, runs=5, seed=42)
    assert out.get("success") is True
    assert out["ari_mean"] > 0.9, f"高可分数据 ARI 应≈1: {out['ari_mean']}"
    assert out["silhouette_mean"] > 0.5
    assert out["k_found"] == 3
    assert out["noise_share"] is None or out["noise_share"] == 0


def test_cluster_stability_dbscan_bootstrap(registry):
    fn = _get(registry, "cluster_stability")
    fc = _clustered_fc()
    out = fn(fc, method="dbscan", eps_m=1200.0, min_samples=4, runs=4)
    assert out.get("success") is True
    assert out["noise_share"] is not None
    assert out["runs_used"] == 4
    assert out["ari_mean"] is not None


def test_cluster_stability_rejects_value_field_v1(registry):
    fn = _get(registry, "cluster_stability")
    fc = _clustered_fc()
    out = fn(fc, method="kmeans", value_field="anything")
    assert out.get("success") is False
    assert "value_field" in out.get("error", "")


def test_cluster_stability_rejects_bad_params(registry):
    fn = _get(registry, "cluster_stability")
    fc = _clustered_fc()
    out = fn(fc, method="dbscan", eps_m=0)
    assert out.get("success") is False
