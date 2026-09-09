"""Science V4 Wave 5 —— SGS 条件高斯模拟 conformance。

验收锚点（descriptor ``interpolation.sgs`` 承诺）：
- caller_seeded：同 seed 双跑实现矩阵**逐位一致**；不同 seed 不同；
- ensemble：P10 ≤ P50 ≤ P90、std > 0、均值有限（真实多实现，非虚构）；
- 资源契约：R×N 预算 / 实现数上限类型化拒绝（先拒绝不 OOM）；
- 取消：realization 边界 checkpoint（CancelledError 上抛）；
- 驱动 e2e：sgs_simulation_surface（工具同一入口）。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.gis.scientific_errors import ResourceScaleMismatch
from app.lib.geo_analysis.kriging_simulation import (
    SGS_MAX_ENSEMBLE_CELLS,
    SGS_MAX_REALIZATIONS,
    sequential_gaussian_simulation,
)


def _fixture(n: int = 48, n_t: int = 220, seed: int = 7):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0, 10_000, (n, 2))
    z = 20 + 5 * np.sin(xy[:, 0] / 2000.0) + rng.normal(0, 0.3, n)
    targets = rng.uniform(0, 10_000, (n_t, 2))
    return xy, z, targets


def test_sgs_same_seed_bitwise_reproducible():
    xy, z, targets = _fixture()
    ens_a = sequential_gaussian_simulation(xy, z, targets, n_realizations=6, seed=42, k=8,
                                   return_realizations=True)
    ens_b = sequential_gaussian_simulation(xy, z, targets, n_realizations=6, seed=42, k=8,
                                   return_realizations=True)
    assert np.array_equal(ens_a.realizations, ens_b.realizations)
    ens_c = sequential_gaussian_simulation(xy, z, targets, n_realizations=6, seed=43, k=8,
                                   return_realizations=True)
    assert not np.array_equal(ens_a.realizations, ens_c.realizations)


def test_sgs_ensemble_quantiles_ordered():
    xy, z, targets = _fixture()
    ens = sequential_gaussian_simulation(xy, z, targets, n_realizations=25, seed=42, k=8)
    assert np.isfinite(ens.mean).all()
    assert np.all(ens.p10 <= ens.p50)
    assert np.all(ens.p50 <= ens.p90)
    assert float(ens.std.min()) > 0.0
    # 条件模拟在样本点处的 ensemble 均值应接近样本值（数据 anchor 语义，
    # k 邻域近似下放宽为同量级）
    from scipy.spatial import cKDTree

    _, near = cKDTree(targets).query(xy, k=1)
    ens_at_data = ens.mean[near]
    assert np.corrcoef(ens_at_data, z)[0, 1] > 0.8


def test_sgs_budget_typed_reject():
    xy, z, targets = _fixture(n_t=10)
    with pytest.raises(ResourceScaleMismatch):
        sequential_gaussian_simulation(
            xy, z, targets, n_realizations=SGS_MAX_REALIZATIONS + 1, seed=1)
    with pytest.raises(ResourceScaleMismatch):
        sequential_gaussian_simulation(
            xy, z, targets, n_realizations=SGS_MAX_ENSEMBLE_CELLS, seed=1)
    with pytest.raises(ResourceScaleMismatch):
        sequential_gaussian_simulation(xy, z, targets, n_realizations=0, seed=1)


def test_sgs_insufficient_samples_typed():
    from app.lib.gis.scientific_errors import InsufficientSamples

    xy, _, targets = _fixture()
    with pytest.raises(InsufficientSamples):
        sequential_gaussian_simulation(xy, xy[:5], targets, n_realizations=2, seed=1)


def test_sgs_cancellation_at_realization_boundary():
    """预先取消的 token 在第一个 realization checkpoint 即上抛。"""
    from app.lib.cancellation import CURRENT_TOKEN, OperationCancelled, CancellationToken

    xy, z, targets = _fixture(n_t=400)
    token = CancellationToken(job_id="sgs-cancel")
    token.cancel()
    token_context = CURRENT_TOKEN.set(token)
    try:
        with pytest.raises(OperationCancelled):
            sequential_gaussian_simulation(
                xy, z, targets, n_realizations=50, seed=42, k=8)
    finally:
        CURRENT_TOKEN.reset(token_context)


def test_sgs_driver_end_to_end():
    from app.lib.geo_analysis.kriging_simulation import sgs_simulation_surface

    fc = {
        "type": "FeatureCollection", "crs": "EPSG:4326",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [116.0 + (i % 6) * 0.008,
                                          39.0 + (i // 6) * 0.008]},
             "properties": {"v": float(10 + i * 0.7)
                            + float((i * 2654435761 % 97) / 97.0)}}
            for i in range(36)
        ],
    }
    r = sgs_simulation_surface(fc, "v", resolution=7, n_realizations=10, seed=7)
    assert r["metadata"]["algorithm"] == "interpolation.sgs"
    assert len(r["records"]) > 0
    rec = r["records"][0]
    assert rec["p10"] <= rec["value"] <= rec["p90"]
    # ensemble 真实展开（至少部分格点实现间有差异——纯噪声-free 线性场
    # 的条件分布会退化，那是 SGS 的正确语义而非缺陷）
    assert max(rr["sgs_std"] for rr in r["records"]) > 0
    assert r["metadata"]["seed"] == 7


def test_sgs_simulation_tool_registered_with_complete_descriptor():
    """工具 sgs_simulation 注册存在且描述符完整（quality 棘轮闸静态引用）。"""
    from app.tools.advanced_spatial import register_advanced_spatial_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_advanced_spatial_tools(reg)
    d = reg.descriptors()["sgs_simulation"]
    assert d.side_effect.value == "deterministic_compute"
    assert "sgs" in d.tags
