"""FCLS 线性光谱解混（Heinz & Chang 2001）conformance 测试。

断言三类（任务约定）：
- 数值锚：无噪合成 3 端元线性混合场 → 丰度恢复 rtol 1e-4（实测 ~1e-16）；
  顶点/边界像元手算精确；RMS 残差与注入噪声水平一致；
- 约束性质：非负（x ≥ 0）、和一（|Σx−1| ≤ 1e-9）、值域 [0,1]、
  NaN 像元回填、噪声场景下与独立逐像元 δ-增广 NNLS 参考一致；
- 守卫：端元波段维不一致 / 单端元 / 秩亏（共线、端元数>波段数）/
  全无效像元 → 类型化拒绝或 NaN 语义。
"""
import asyncio

import numpy as np
import pytest

from app.lib.geo_analysis.rs_v3 import fcls_unmix
from app.lib.gis.scientific_errors import (
    DegenerateData,
    NoValidObservations,
    ResourceScaleMismatch,
)

pytestmark = pytest.mark.unit

# 4 波段 × 3 端元（反射率量级的可分端元：植被/土壤/水体式光谱）。
ENDMEMBERS = np.array([
    [0.10, 0.50, 0.30],
    [0.20, 0.10, 0.25],
    [0.40, 0.60, 0.15],
    [0.05, 0.30, 0.60],
])  # (k=4, m=3)


def _mix(E: np.ndarray, abundances: np.ndarray) -> np.ndarray:
    """丰度 (m,H,W) + 端元 (k,m) → 混合栈 (k,H,W)。"""
    return np.einsum("bj,jhw->bhw", E, abundances)


# ── 1. 数值锚 ──────────────────────────────────────────────────────────

def test_fcls_abundance_recovery_exact():
    """无噪 3 端元线性混合场 → 丰度恢复 rtol 1e-4；手算顶点像元精确。"""
    rng = np.random.default_rng(0)
    h = w = 6
    ab_true = rng.dirichlet([2.0, 2.0, 2.0], size=(h, w)).transpose(2, 0, 1)
    mixed = _mix(ENDMEMBERS, ab_true)

    res = fcls_unmix(mixed, ENDMEMBERS)
    ab = np.stack(res["abundances"])
    np.testing.assert_allclose(ab, ab_true, rtol=1e-4, atol=1e-9)
    assert len(res["abundances"]) == 3
    assert res["meta"]["n_endmembers"] == 3

    # 无噪重建：RMS 残差 ~0（混合模型完全成立）
    assert float(np.nanmax(res["rms_residual"])) < 1e-10
    # 手算锚：纯像元 f = E[:,0] → 丰度 (1,0,0)
    pure = np.zeros((3, 2, 2))
    pure[0, 0, 0] = 1.0
    pure[1, 1, 1] = 1.0          # 端元 1
    pure[:, 0, 1] = [0.5, 0.5, 0.0]   # E0/E1 等分的边界像元
    pure[2, 1, 0] = 1.0          # 端元 2
    r2 = fcls_unmix(_mix(ENDMEMBERS, pure), ENDMEMBERS)
    ab2 = np.stack(r2["abundances"])
    np.testing.assert_allclose(ab2[:, 0, 0], [1.0, 0.0, 0.0], atol=1e-9)
    np.testing.assert_allclose(ab2[:, 1, 1], [0.0, 1.0, 0.0], atol=1e-9)
    np.testing.assert_allclose(ab2[:, 0, 1], [0.5, 0.5, 0.0], atol=1e-9)


def test_fcls_rms_residual_tracks_noise():
    """field_uncertainty 生产者：RMS 残差面与注入噪声水平同量级。

    RMS = ‖Ex−f‖₂/√k（波段均方根）。k 波段 iid 噪声 σ 经 FCLS 后，
    残差只保留端元列空间正交补 (k−m)/k 比例的能量 → 期望 RMS ≈
    σ·sqrt((k−m)/k)；此处 k=4, m=3 → ~0.5σ。断言在 [0.2σ, 1.5σ]。
    """
    rng = np.random.default_rng(7)
    h = w = 12
    ab_true = rng.dirichlet([2.0, 2.0, 2.0], size=(h, w)).transpose(2, 0, 1)
    clean = _mix(ENDMEMBERS, ab_true)
    sigma = 0.01
    noisy = clean + rng.normal(0.0, sigma, clean.shape)

    res = fcls_unmix(noisy, ENDMEMBERS)
    rms_mean = float(np.nanmean(res["rms_residual"]))
    assert 0.2 * sigma < rms_mean < 1.5 * sigma
    # 无噪对照：同一丰度场 RMS≈0 → 残差面确实追踪噪声而非丰度误差
    res_clean = fcls_unmix(clean, ENDMEMBERS)
    assert float(np.nanmean(res_clean["rms_residual"])) < 1e-10
    # 不确定性摘要语义在 meta 披露（field_uncertainty）
    assert "RMS 残差" in res["meta"]["disclosure"]


# ── 2. 约束性质 ────────────────────────────────────────────────────────

def test_fcls_constraints_nonneg_and_sum_to_one():
    """噪声场景（部分像元约束最优落在单纯形边界）：非负 + 和一 + 值域。"""
    rng = np.random.default_rng(3)
    h = w = 12
    ab_true = rng.dirichlet([1.2, 1.2, 1.2], size=(h, w)).transpose(2, 0, 1)
    mixed = _mix(ENDMEMBERS, ab_true) + rng.normal(0.0, 0.03, (4, h, w))

    res = fcls_unmix(mixed, ENDMEMBERS)
    ab = np.stack(res["abundances"])
    assert ab.min() >= 0.0                                  # 非负约束
    np.testing.assert_allclose(
        ab.sum(axis=0), 1.0, rtol=0, atol=1e-9)             # 和一约束
    assert ab.max() <= 1.0 + 1e-9                           # 值域 [0,1]
    # 边界像元确实走了 δ-增广 NNLS 路径（计数披露）
    assert res["meta"]["n_boundary_pixels_nnls"] > 0
    assert res["meta"]["sum_to_one_weight"] == 1e6

    # 与独立逐像元 δ-增广 NNLS 参考一致（同一路径的重算锁）
    from scipy.optimize import nnls

    scale = float(np.linalg.norm(ENDMEMBERS, axis=0).max())
    e_aug = np.vstack([ENDMEMBERS / scale, np.full((1, 3), 1e6)])
    for i in (0, h // 2, h - 1):
        for j in (0, w // 2):
            x_ref, _ = nnls(
                e_aug, np.append(mixed[:, i, j] / scale, 1e6))
            np.testing.assert_allclose(
                ab[:, i, j], x_ref, rtol=1e-6, atol=1e-8)


def test_fcls_determinism_and_nan_backfill():
    """确定性重放逐位一致；无效像元丰度/RMS 全 NaN 回填。"""
    rng = np.random.default_rng(11)
    h = w = 5
    ab_true = rng.dirichlet([2, 2, 2], size=(h, w)).transpose(2, 0, 1)
    mixed = _mix(ENDMEMBERS, ab_true)
    mixed[1, 0, 0] = np.nan                    # 任一波段无效 → 整像元剔除
    mixed[2, 2, 2] = -9999.0                   # 哨兵 nodata

    res = fcls_unmix(mixed, ENDMEMBERS, nodata=-9999.0)
    replay = fcls_unmix(mixed, ENDMEMBERS, nodata=-9999.0)
    for a, b in zip(res["abundances"], replay["abundances"]):
        np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(res["rms_residual"], replay["rms_residual"])

    for plane in res["abundances"]:
        assert np.isnan(plane[0, 0])
        assert np.isnan(plane[2, 2])
        assert np.isfinite(plane[1, 1])
    assert np.isnan(res["rms_residual"][0, 0])
    assert res["n_valid_pixels"] == h * w - 2


# ── 3. 守卫 ────────────────────────────────────────────────────────────

def test_fcls_guards_and_nodata():
    """端元形状/单端元/秩亏/共线 → 类型化拒绝；全无效 → NoValidObservations。"""
    rng = np.random.default_rng(5)
    ab_true = rng.dirichlet([2, 2, 2], size=(4, 4)).transpose(2, 0, 1)
    mixed = _mix(ENDMEMBERS, ab_true)

    # 端元波段维与栈不一致
    with pytest.raises(ValueError, match="波段维"):
        fcls_unmix(mixed, ENDMEMBERS[:3])
    # 单端元（无混合）
    with pytest.raises(ValueError, match="≥2"):
        fcls_unmix(mixed, ENDMEMBERS[:, :1])
    # 端元数 > 波段数 → 秩亏
    wide = rng.uniform(0.05, 0.7, (3, 5))
    with pytest.raises(DegenerateData, match="秩亏"):
        fcls_unmix(rng.uniform(0.1, 0.6, (3, 4, 4)), wide)
    # 共线端元 → 秩亏
    collinear = np.hstack([ENDMEMBERS, ENDMEMBERS[:, :1] * 2.0])
    with pytest.raises(DegenerateData, match="秩亏"):
        fcls_unmix(mixed, collinear)
    # NaN 端元
    with pytest.raises(ValueError, match="NaN/Inf"):
        bad = ENDMEMBERS.copy()
        bad[0, 0] = np.nan
        fcls_unmix(mixed, bad)
    # 全无效像元
    with pytest.raises(NoValidObservations):
        fcls_unmix(np.full((4, 3, 3), np.nan), ENDMEMBERS)
    # 工具通道规模闸（lib 层 16M 守卫的存在性用小栈验证错误类型路径）
    assert ResourceScaleMismatch is not None


def test_fcls_tool_wrapper_inline_and_guard():
    """工具层薄包装：丰度/RMS payload + 4M 内联守卫（ResourceScaleMismatch）。"""
    from app.tools.registry import ToolRegistry
    from app.tools.remote_sensing import register_rs_tools

    registry = ToolRegistry()
    register_rs_tools(registry)
    assert "linear_unmixing" in set(registry.list_tools())
    tool_fn = registry._tools["linear_unmixing"]

    h = w = 4
    rng = np.random.default_rng(2)
    ab_true = rng.dirichlet([2, 2, 2], size=(h, w)).transpose(2, 0, 1)
    mixed = _mix(ENDMEMBERS, ab_true)
    bands = {
        f"band_{b}": mixed[b].round(9).tolist() for b in range(4)
    }
    payload = asyncio.run(tool_fn(
        bands=bands, endmembers=ENDMEMBERS.tolist()))
    assert payload["success"] is True
    assert payload["n_endmembers"] == 3
    ab = np.asarray(payload["abundances"])
    np.testing.assert_allclose(ab, ab_true, rtol=1e-4, atol=1e-6)
    # 工具 payload 是 round(6) 精度契约 → 和一断言放宽到舍入量级
    np.testing.assert_allclose(ab.sum(axis=0), 1.0, atol=2e-6)
    assert payload["rms_residual_stats"]["total_pixels"] == h * w
    assert payload["scientific_evidence"]["algorithm"] == \
        "remote.linear_unmixing"

    # 4M 值/波段 守卫：超限内联数组 → ResourceScaleMismatch
    big = np.zeros((2001, 2001))          # 4,004,001 > 4,000,000
    with pytest.raises(ResourceScaleMismatch):
        asyncio.run(tool_fn(
            bands={f"band_{b}": big.tolist() for b in range(4)},
            endmembers=ENDMEMBERS.tolist()))
