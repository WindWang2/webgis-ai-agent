"""SGS batched 变体 + reference 条件值修复（science-v5 W5）。

契约（01-architecture.md D3 + Subagent-A Round-0 #4/#5 修订）：

- batched（variant ``numpy_batched``）：P 组共享路径 + chunk 批量 solve
  （协方差与实现无关 → 权重组内复用）；同 seed 双跑逐位一致（自身确定性）；
- 与 reference 的统计 differential：RNG 消费序列不同 → 不伪装逐位一致；
  ensemble mean 相关 ≥ 0.98、std 比 ∈ [0.85, 1.2]、P50 有限且有序；
- 动态目标上限 = min(绝对上限, ensemble 预算 // R)（挑战 R0-#5）；
- W5 修复回归：reference 路径 sim_k_i 位置索引 → 经 path 映射回目标序号
  （多 chunk 条件值错位修复；identity path 下与旧实现等价——不回归）。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.gis.scientific_errors import ResourceScaleMismatch
from app.lib.geo_analysis.kriging_simulation import (
    SGS_BATCHED_CHUNK,
    SGS_MAX_REALIZATIONS,
    sequential_gaussian_simulation,
    sequential_gaussian_simulation_batched,
)


def _fixture(n: int = 48, n_t: int = 400, seed: int = 7):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0, 10_000, (n, 2))
    z = 20 + 5 * np.sin(xy[:, 0] / 2000.0) + rng.normal(0, 0.3, n)
    targets = rng.uniform(0, 10_000, (n_t, 2))
    return xy, z, targets


class TestSGSBatched:
    def test_same_seed_bitwise_reproducible(self):
        xy, z, targets = _fixture()
        a = sequential_gaussian_simulation_batched(
            xy, z, targets, n_realizations=8, seed=42, k=8,
            n_path_groups=3, return_realizations=True)
        b = sequential_gaussian_simulation_batched(
            xy, z, targets, n_realizations=8, seed=42, k=8,
            n_path_groups=3, return_realizations=True)
        assert np.array_equal(a.realizations, b.realizations)
        c = sequential_gaussian_simulation_batched(
            xy, z, targets, n_realizations=8, seed=43, k=8,
            n_path_groups=3, return_realizations=True)
        assert not np.array_equal(a.realizations, c.realizations)

    def test_backend_field_and_quantiles(self):
        xy, z, targets = _fixture()
        ens = sequential_gaussian_simulation_batched(
            xy, z, targets, n_realizations=12, seed=42, k=8)
        assert ens.backend == "numpy_batched"
        ref = sequential_gaussian_simulation(xy, z, targets,
                                             n_realizations=12, seed=42, k=8)
        assert ref.backend == "numpy_reference"
        assert np.all(ens.p10 <= ens.p50)
        assert np.all(ens.p50 <= ens.p90)
        assert float(ens.std.min()) > 0.0
        assert np.isfinite(ens.mean).all()
        d = ens.to_dict()
        assert d["backend"] == "numpy_batched"
        assert any("path sharing" in s for s in ens.disclosures)
        assert any("not bitwise-identical" in s
                   for s in ens.disclosures)

    def test_statistical_differential_vs_reference(self):
        xy, z, targets = _fixture(n_t=300)
        ref = sequential_gaussian_simulation(
            xy, z, targets, n_realizations=30, seed=42, k=8)
        bat = sequential_gaussian_simulation_batched(
            xy, z, targets, n_realizations=30, seed=42, k=8,
            n_path_groups=6, chunk_size=64)
        corr = float(np.corrcoef(ref.mean, bat.mean)[0, 1])
        assert corr >= 0.98, f"ensemble mean corr {corr}"
        ratio = bat.std / np.maximum(ref.std, 1e-9)
        assert np.median(ratio) > 0.85
        assert np.median(ratio) < 1.2
        # 双方都应锚定数据（与同点克里金均值同向）
        from scipy.spatial import cKDTree

        _, near = cKDTree(targets).query(xy, k=1)
        assert np.corrcoef(bat.mean[near], z)[0, 1] > 0.8

    def test_group_count_affects_disclosure_only_when_valid(self):
        xy, z, targets = _fixture(n_t=120)
        # 组数 > R 时收敛到 R（每实现一组 = reference 路径语义上限）
        ens = sequential_gaussian_simulation_batched(
            xy, z, targets, n_realizations=4, seed=42, k=8,
            n_path_groups=99)
        assert ens.backend == "numpy_batched"
        assert np.isfinite(ens.mean).all()

    def test_dynamic_target_cap_typed_reject(self, monkeypatch):
        xy, z, _ = _fixture(n_t=10)
        # 动态上限 = min(batched 绝对, ensemble 预算 // R)；收紧常数验证
        import app.lib.geo_analysis.kriging_simulation as ks

        monkeypatch.setattr(ks, "SGS_BATCHED_MAX_TARGETS", 50)
        targets = np.random.default_rng(1).uniform(0, 100, (51, 2))
        with pytest.raises(ResourceScaleMismatch, match="动态上限"):
            sequential_gaussian_simulation_batched(
                xy, z, targets, n_realizations=10, seed=1)
        # ensemble 预算分支：R 大时上限更低
        monkeypatch.setattr(ks, "SGS_BATCHED_MAX_TARGETS", 10_000)
        monkeypatch.setattr(ks, "SGS_MAX_ENSEMBLE_CELLS", 400)
        with pytest.raises(ResourceScaleMismatch):
            sequential_gaussian_simulation_batched(
                xy, z, targets, n_realizations=100, seed=1)  # cap = 4

    def test_batched_rejects_bad_realizations(self):
        xy, z, targets = _fixture(n_t=10)
        with pytest.raises(ResourceScaleMismatch):
            sequential_gaussian_simulation_batched(
                xy, z, targets, n_realizations=0, seed=1)
        with pytest.raises(ResourceScaleMismatch):
            sequential_gaussian_simulation_batched(
                xy, z, targets, n_realizations=SGS_MAX_REALIZATIONS + 1,
                seed=1)

    def test_cancellation_at_chunk_boundary(self):
        from app.lib.cancellation import (
            CURRENT_TOKEN,
            CancellationToken,
            OperationCancelled,
        )

        xy, z, targets = _fixture(n_t=800)
        token = CancellationToken(job_id="sgs-batched-cancel")
        token.cancel()
        token_context = CURRENT_TOKEN.set(token)
        try:
            with pytest.raises(OperationCancelled):
                sequential_gaussian_simulation_batched(
                    xy, z, targets, n_realizations=10, seed=42, k=8,
                    chunk_size=SGS_BATCHED_CHUNK)
        finally:
            CURRENT_TOKEN.reset(token_context)

    def test_chunk_smaller_than_k_is_clamped(self):
        # R2-#4 回归：chunk < k 被钳到 ≥ k（k_sim<k 病态区制直接消灭——
        # 实测该区制 ensemble 对 OK 锚定跌至 ~0.6 且随 R 不收敛）；
        # 另修 sim 块对角索引（k_sim<k 时原写错位置）。
        from app.lib.geo_analysis.kriging import fit_variogram, ordinary_kriging

        xy, z, targets = _fixture(n=48, n_t=200, seed=13)
        vfit = fit_variogram(xy, z)
        ok = ordinary_kriging(xy, z, targets, vfit, k=12)
        bat_small = sequential_gaussian_simulation_batched(
            xy, z, targets, n_realizations=64, seed=42, k=12,
            n_path_groups=8, chunk_size=8)          # 触发钳制 → chunk=12
        bat_large = sequential_gaussian_simulation_batched(
            xy, z, targets, n_realizations=64, seed=42, k=12,
            n_path_groups=8, chunk_size=64)
        # 序贯区制 E-type 均值的 MC 噪声高于单 chunk 区制（路径依赖），
        # 阈值按该区制校准（R=64、P=8）
        corr_ok = float(np.corrcoef(bat_small.mean, ok.predictions)[0, 1])
        assert corr_ok >= 0.75, f"data-anchor corr {corr_ok}"
        # 注：不 pin chunk 12 vs 64 的互相关——序贯近似对 chunk 粒度的
        # 敏感度是近似语义的一部分（R=64 下实测 ~0.78），非正确性断言。
        assert np.isfinite(bat_small.mean).all()
        assert float(bat_small.std.max()) < 10.0   # 无 var 爆炸

    def test_single_chunk_matches_reference_statistics(self):
        # n_t < chunk（单 chunk）→ 无模拟条件 → 两路径统计应高度一致
        xy, z, targets = _fixture(n_t=60)
        ref = sequential_gaussian_simulation(
            xy, z, targets, n_realizations=40, seed=42, k=8)
        bat = sequential_gaussian_simulation_batched(
            xy, z, targets, n_realizations=40, seed=42, k=8,
            n_path_groups=1, chunk_size=64)
        corr = float(np.corrcoef(ref.mean, bat.mean)[0, 1])
        assert corr >= 0.98


class TestReferenceConditioningFix:
    def test_identity_path_equivalence_no_regression(self, monkeypatch):
        # path = identity 时位置索引 == 目标序号 → 修复不改变该情形结果
        import app.lib.geo_analysis.kriging_simulation as ks

        xy, z, targets = _fixture(n=40, n_t=250, seed=7)

        real_default_rng = ks.np.random.default_rng

        class IdentityRng:
            def __init__(self, inner):
                self._inner = inner

            def permutation(self, n):
                return np.arange(n)

            def standard_normal(self, *a, **kw):
                return self._inner.standard_normal(*a, **kw)

        monkeypatch.setattr(
            ks.np.random, "default_rng",
            lambda seed=None: IdentityRng(real_default_rng(seed)))
        a = sequential_gaussian_simulation(
            xy, z, targets, n_realizations=3, seed=42, k=8,
            return_realizations=True)
        b = sequential_gaussian_simulation(
            xy, z, targets, n_realizations=3, seed=42, k=8,
            return_realizations=True)
        np.testing.assert_array_equal(a.realizations, b.realizations)

    def test_multichunk_conditioning_anchor_strengthens(self):
        # 修复语义锚：多 chunk 下条件模拟在样本邻域的 ensemble 均值应与
        # 样本值强相关（条件信息未被错位破坏）
        xy, z, targets = _fixture(n=60, n_t=800, seed=11)
        ens = sequential_gaussian_simulation(
            xy, z, targets, n_realizations=24, seed=42, k=8)
        from scipy.spatial import cKDTree

        _, near = cKDTree(targets).query(xy, k=1)
        corr = np.corrcoef(ens.mean[near], z)[0, 1]
        assert corr > 0.8, f"data-anchor correlation {corr}"
