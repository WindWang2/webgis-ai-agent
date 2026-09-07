"""SAR V3 批次 conformance 测试（Foundation V3 · SAR batch）。

覆盖（app/lib/geo_analysis/{sar_calibration,sar_filter,sar_v3}.py）：

- 入射角 LUT 定标：LUT（常数平面）与标量路径 parity、lut_pixels 披露、
  形状/开区间/互斥守卫、v2 诚实披露（SAFE-XML 边界保留）；
- 热噪声去除：标量 floor/LUT 精确相减 + 钳 0 计数；缺参/双参/负值
  类型化错误；SAFE annotation XML 不解析的诚实披露；
- 量纲换算：round-trip 恒等（amplitude↔intensity、linear↔dB）、
  ε 下限计数、负值 → NaN 计数、mode 守卫；
- Gamma MAP（三分支 + Newton 迭代上限披露）与 Kuan（闭式 MMSE 手算
  精确一致）：噪声下降、确定性、常数场恒等；
- MT-Lee：合成斑点栈噪声下降 + 均值保持 + 确定性重放；T<3/栈深闸/
  负值/常数栈守卫；权重方向（时序常数 → w=1）；
- 相干性：a==a → γ=1、随机相位平移失相干、强度-only 类型化拒绝、
  re/im 双通道、窗口有效对计数；
- RTC：平地恒等（γ_flat=σ⁰）、合成斜坡 cos 比手算精确、叠掩 guard、
  入射角 LUT parity、必需参数/方位角守卫；
- 叠掩/阴影：楔形 DEM 三类分类 + 占比、nodata 类、必需参数守卫；
- ENL 图：合成 4 视斑点 ENL≈4（全局 + 滑窗中位）、退化窗口 NaN、
  常数场/负值/窗口守卫；
- 注册表：validate()（sar.* 前缀零 issue）、parity 门零 issue、
  契约 v2 枚举/版本、能力映射；工具 schema + 证据块。
"""
import asyncio
import math

import numpy as np
import pytest

from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    InvalidUnits,
    MissingRequiredField,
    NoValidObservations,
    ResourceScaleMismatch,
    UnsupportedMethod,
)
from app.lib.geo_analysis.sar_calibration import (
    calibrate_sar,
    remove_thermal_noise,
    sar_log_scale,
)
from app.lib.geo_analysis.sar_filter import (
    gamma_map_filter,
    kuan_filter,
    speckle_filter,
)
from app.lib.geo_analysis.sar_v3 import (
    coherence_estimate,
    enl_map,
    layover_shadow_mask,
    multitemporal_speckle,
    radiometric_terrain_correction,
)

pytestmark = pytest.mark.unit


# ── 1. 入射角 LUT 定标（sar_calibration v2）──────────────────────────

def test_calibration_incidence_lut_and_v2_disclosure():
    dn = np.array([[2.0, 4.0], [6.0, 8.0]])
    lut = np.full((2, 2), 30.0)
    res_lut = calibrate_sar(dn, calibration_constant=2.0, incidence_map=lut,
                            output_product="sigma0")
    res_scalar = calibrate_sar(dn, calibration_constant=2.0,
                               incidence_deg=30.0, output_product="sigma0")
    # LUT（常数平面）与标量路径 parity（同一平面 → 逐位一致）
    np.testing.assert_array_equal(res_lut["products"]["sigma0"],
                                  res_scalar["products"]["sigma0"])
    assert res_lut["meta"]["incidence_mode"] == "per_pixel"
    assert res_lut["meta"]["lut_pixels"] == 4
    assert res_scalar["meta"]["lut_pixels"] is None

    # 变化 LUT：逐像元 sin(θ) 手算精确
    lut2 = np.array([[30.0, 45.0], [60.0, 40.0]])
    res2 = calibrate_sar(dn, calibration_constant=2.0, incidence_map=lut2,
                         output_product="sigma0")
    for i in range(2):
        for j in range(2):
            expect = (dn[i, j] / 2.0) * math.sin(math.radians(lut2[i, j]))
            assert res2["products"]["sigma0"][i, j] == pytest.approx(
                expect, abs=1e-12)

    # 守卫：形状不一致 / LUT 越界 / 标量与 LUT 互斥
    with pytest.raises(ValueError, match="形状"):
        calibrate_sar(dn, calibration_constant=2.0,
                      incidence_map=np.full((3, 3), 30.0),
                      output_product="sigma0")
    bad_lut = np.full((2, 2), 30.0)
    bad_lut[0, 0] = 95.0
    with pytest.raises(InvalidUnits):
        calibrate_sar(dn, calibration_constant=2.0, incidence_map=bad_lut,
                      output_product="sigma0")
    with pytest.raises(ValueError, match="二选一"):
        calibrate_sar(dn, calibration_constant=2.0, incidence_deg=30.0,
                      incidence_map=lut, output_product="sigma0")

    # v2 诚实披露：σ⁰ 定标 LUT（SAFE XML）不解析 + 热噪声独立算法
    disc = res_lut["meta"]["disclosure"]
    assert "LUT" in disc and "SAFE" in disc
    assert "热噪声" in disc and "thermal_noise_removal" in disc


# ── 2. 热噪声去除 ─────────────────────────────────────────────────────

def test_thermal_noise_floor_removal_exact():
    img = np.array([[5.0, 1.0], [0.5, 2.0]])
    res = remove_thermal_noise(img, noise_floor=1.0)
    expected = np.array([[4.0, 0.0], [0.0, 1.0]])
    np.testing.assert_allclose(res["array"], expected, atol=1e-12)
    assert res["mode"] == "scalar"
    # 只有 0.5 − 1.0 < 0 被钳（1.0 − 1.0 = 0 原地保留，不计钳 0）
    assert res["meta"]["clamped_pixels"] == 1
    # 严格双钳 golden：floor 3 → 两个负值钳 0
    res3 = remove_thermal_noise(np.array([[2.0, 1.0]]), noise_floor=3.0)
    np.testing.assert_allclose(res3["array"], [[0.0, 0.0]], atol=1e-12)
    assert res3["meta"]["clamped_pixels"] == 2
    assert res["meta"]["formula"] == "I_dn = max(I − N, 0)"

    # LUT 路径：同值 LUT == 标量路径（逐位一致）
    lut = np.full((2, 2), 1.0)
    res_lut = remove_thermal_noise(img, noise_lut=lut)
    np.testing.assert_allclose(res_lut["array"], expected, atol=1e-12)
    assert res_lut["mode"] == "lut"
    assert res_lut["meta"]["lut_pixels"] == 4

    # nodata 哨兵 → NaN（不参与钳 0 统计）
    res_nd = remove_thermal_noise(
        np.array([[5.0, -9999.0]]), noise_floor=1.0, nodata=-9999.0)
    assert res_nd["array"][0, 0] == pytest.approx(4.0, abs=1e-12)
    assert np.isnan(res_nd["array"][0, 1])

    # 诚实披露：annotation XML / SAFE / 钳 0
    assert "annotation XML" in res["meta"]["disclosure"]
    assert "SAFE" in res["meta"]["disclosure"]
    assert "钳 0" in res["meta"]["disclosure"]


def test_thermal_noise_guards():
    img = np.array([[5.0, 1.0], [0.5, 2.0]])
    # 缺噪声参数 → MissingRequiredField（绝不虚构）
    with pytest.raises(MissingRequiredField):
        remove_thermal_noise(img)
    # 双参互斥 → ValueError
    with pytest.raises(ValueError, match="二选一"):
        remove_thermal_noise(img, noise_floor=1.0,
                             noise_lut=np.zeros((2, 2)))
    # 负 floor / 负 LUT → ValueError（噪声底在强度域非负）
    with pytest.raises(ValueError, match="非负"):
        remove_thermal_noise(img, noise_floor=-1.0)
    with pytest.raises(ValueError, match="非负"):
        remove_thermal_noise(img, noise_lut=np.array([[1.0, -0.1],
                                                      [1.0, 1.0]]))
    # dB（负强度）输入 → UnsupportedMethod
    with pytest.raises(UnsupportedMethod):
        remove_thermal_noise(np.array([[1.0, -2.0]]), noise_floor=0.5)
    # LUT 形状不一致 → ValueError
    with pytest.raises(ValueError, match="形状"):
        remove_thermal_noise(img, noise_lut=np.zeros((3, 3)))
    # LUT 非有限像元 → 输出 NaN（诚实，不插值）
    lut_nan = np.array([[1.0, np.nan], [1.0, 1.0]])
    res = remove_thermal_noise(img, noise_lut=lut_nan)
    assert np.isnan(res["array"][0, 1])
    assert res["meta"]["invalid_pixels"] == 1


# ── 3. 量纲换算（log scaling）─────────────────────────────────────────

def test_log_scale_round_trip_identities():
    # amplitude → intensity → amplitude：手算 + 逐位恒等
    amp = np.array([[2.0, 3.0], [0.5, 4.0]])
    inten = sar_log_scale(amp, "amplitude_to_intensity")["array"]
    np.testing.assert_allclose(inten, np.array([[4.0, 9.0], [0.25, 16.0]]),
                               atol=1e-15)
    amp_back = sar_log_scale(inten, "intensity_to_amplitude")["array"]
    np.testing.assert_array_equal(amp_back, amp)

    # linear → db → linear：随机正数阵 round-trip（1e-12 rel）
    rng = np.random.RandomState(0)
    lin = rng.uniform(0.1, 100.0, size=(8, 8))
    db = sar_log_scale(lin, "linear_to_db")["array"]
    np.testing.assert_allclose(
        db, 10.0 * np.log10(lin), rtol=1e-12)
    lin_back = sar_log_scale(db, "db_to_linear")["array"]
    np.testing.assert_allclose(lin_back, lin, rtol=1e-12)

    # db → linear → db round-trip
    db2 = sar_log_scale(lin_back, "linear_to_db")["array"]
    np.testing.assert_allclose(db2, db, rtol=1e-9)

    # 手算 golden：100 → 20 dB；40 dB → 10^4
    one = np.array([[100.0]])
    assert sar_log_scale(one, "linear_to_db")["array"][0, 0] == pytest.approx(
        20.0, abs=1e-12)
    assert sar_log_scale(np.array([[40.0]]), "db_to_linear")["array"][0, 0] \
        == pytest.approx(1e4, rel=1e-12)


def test_log_scale_guards_and_epsilon():
    # 未知 mode → ValueError
    with pytest.raises(ValueError, match="unsupported log scale mode"):
        sar_log_scale(np.array([[1.0]]), "to_complex")
    # 非负域换算的负值 → NaN（计数披露）
    neg = np.array([[4.0, -1.0]])
    res = sar_log_scale(neg, "intensity_to_amplitude")
    assert np.isnan(res["array"][0, 1])
    assert res["meta"]["nonpositive_to_nan"] == 1
    # linear_to_db 的 0/负值 → ε 下限（计数披露）
    zeros = np.array([[0.0, 100.0], [-5.0, 1.0]])
    res_db = sar_log_scale(zeros, "linear_to_db")
    assert res_db["meta"]["floored_cells"] == 2
    assert res_db["array"][0, 0] == pytest.approx(10.0 * math.log10(1e-12),
                                                  abs=1e-9)
    assert "ε" in res_db["meta"]["formula"]
    # 非法 nodata 哨兵 → NaN；非 2D → ValueError
    res_nd = sar_log_scale(np.array([[100.0, -9999.0]]), "linear_to_db",
                           nodata=-9999.0)
    assert np.isnan(res_nd["array"][0, 1])
    assert res_nd["meta"]["floored_cells"] == 0
    with pytest.raises(ValueError, match="2D"):
        sar_log_scale(np.array([1.0, 2.0]), "linear_to_db")


# ── 4. Gamma MAP / Kuan（sar_filter v2 additive）─────────────────────

def test_gamma_map_newton_edge_and_noise():
    rng = np.random.RandomState(42)
    noisy = rng.gamma(shape=4.0, scale=25.0, size=(32, 32))
    res = gamma_map_filter(noisy, window=5, enl=4.0)
    # 噪声下降 + 均值近似保持（斑点均值 1 → 无系统偏移 > 5%）
    assert res["array"].std() < noisy.std()
    assert abs(res["array"].mean() - noisy.mean()) < 0.05 * noisy.mean()
    # Newton 迭代：上限内收敛、轮数披露、公式在场
    assert res["meta"]["iterations_used"] is not None
    assert 1 <= res["meta"]["iterations_used"] <= 10
    assert "Newton" in res["meta"]["formula"]
    assert res["enl_source"] == "explicit"
    # 确定性重放（逐位一致）
    again = gamma_map_filter(noisy, window=5, enl=4.0)
    np.testing.assert_array_equal(res["array"], again["array"])
    # 常数场（均匀分支 Cv ≤ Cu）→ 恒等映射
    const = np.full((8, 8), 100.0)
    np.testing.assert_allclose(
        gamma_map_filter(const, enl=4.0)["array"], const, atol=1e-12)
    # 纯阶梯边缘：输出有限（点目标/异质分支不产生 NaN）
    step = np.concatenate([np.full((10, 5), 50.0), np.full((10, 5), 100.0)],
                          axis=1)
    assert np.isfinite(gamma_map_filter(step, enl=4.0)["array"]).all()
    # 迭代上限守卫
    with pytest.raises(ValueError, match="max_iterations"):
        speckle_filter(noisy, "gamma_map", enl=4.0, max_iterations=51)
    # 未知方法仍被拒绝（枚举守卫）
    with pytest.raises(ValueError, match="unsupported speckle filter"):
        speckle_filter(noisy, "kuwahara")


def test_kuan_closed_form_hand_exact():
    # 高对比窗口（cv² > Cu²=1/enl，走非均匀分支）
    img = np.array([
        [10.0, 12.0, 14.0, 16.0, 18.0],
        [12.0, 14.0, 16.0, 18.0, 20.0],
        [14.0, 16.0, 60.0, 64.0, 68.0],
        [16.0, 18.0, 64.0, 20.0, 22.0],
        [18.0, 20.0, 22.0, 24.0, 26.0],
    ])
    enl = 4.0
    res = kuan_filter(img, window=3, enl=enl)
    # 独立复算内点 (2,2)：窗口统计 + Kuan 闭式 k（含 [0,1] 钳）
    win = img[1:4, 1:4]
    m = win.mean()
    v = win.var()                              # 总体方差 ddof=0
    cu2 = 1.0 / enl
    cv2 = v / (m * m)
    k_raw = (1.0 - cu2 / cv2) / (1.0 + cu2)
    assert 0.0 < k_raw < 1.0                   # 非均匀分支（k 未触钳）
    expected = m + k_raw * (img[2, 2] - m)
    assert res["array"][2, 2] == pytest.approx(expected, abs=1e-12)
    assert res["meta"]["formula"].startswith("R = m + k·(x−m)")
    assert "Kuan 1985" in res["meta"]["formula"]
    # 常数场：cv2=0 ≤ cu2 → k=0 → 恒等映射（均匀分支）
    const = np.full((6, 6), 42.0)
    np.testing.assert_allclose(
        kuan_filter(const, window=3, enl=enl)["array"], const, atol=1e-12)
    # dB（负值）+ 缺省 ENL → UnsupportedMethod
    with pytest.raises(UnsupportedMethod):
        kuan_filter(np.array([[1.0, -2.0], [3.0, 4.0]]))


# ── 5. 多时相斑点抑制（MT-Lee）───────────────────────────────────────

def test_multitemporal_speckle_noise_reduction_and_determinism():
    rng = np.random.RandomState(42)
    # 常数场景 + 独立 4 视斑点（T=6）——时序维也是独立实现
    stack = 25.0 * rng.gamma(4.0, 1.0, size=(6, 24, 24)) / 4.0
    res = multitemporal_speckle(stack, window=3)
    out = res["stack"]
    assert out.shape == stack.shape
    assert out.std() < stack.std()
    assert abs(out.mean() - stack.mean()) < 0.05 * stack.mean()
    assert res["meta"]["time_slices"] == 6
    assert res["meta"]["enl_source"] == "estimated"
    w = res["mean_temporal_weight"]
    assert w is not None and 0.0 <= w <= 1.0
    # 确定性重放（逐位一致）
    again = multitemporal_speckle(stack, window=3)
    np.testing.assert_array_equal(out, again["stack"])
    # nodata 感知：切片内哨兵 → NaN（不虚构）
    stack_nd = stack.copy()
    stack_nd[0, 5, 5] = -9999.0
    res_nd = multitemporal_speckle(stack_nd, window=3, nodata=-9999.0)
    assert np.isnan(res_nd["stack"][0, 5, 5])
    assert np.isfinite(res_nd["stack"][1, 5, 5])


def test_multitemporal_speckle_guards():
    rng = np.random.RandomState(1)
    stack = rng.gamma(4.0, 1.0, size=(5, 8, 8))
    # T < 3 → InsufficientSamples
    with pytest.raises(InsufficientSamples):
        multitemporal_speckle(stack[:2])
    # T > 24 → ResourceScaleMismatch
    with pytest.raises(ResourceScaleMismatch):
        multitemporal_speckle(np.zeros((25, 4, 4)))
    # 非 3D → ValueError；负值（dB）→ UnsupportedMethod
    with pytest.raises(ValueError, match="三维"):
        multitemporal_speckle(np.zeros((4, 4)))
    with pytest.raises(UnsupportedMethod):
        multitemporal_speckle(np.full((3, 4, 4), -1.0))
    # 常数栈：缺省 ENL → DegenerateData；显式 ENL → 恒等 + w=1
    const = np.full((4, 6, 6), 7.0)
    with pytest.raises(DegenerateData):
        multitemporal_speckle(const)
    res = multitemporal_speckle(const, enl=4.0)
    np.testing.assert_allclose(res["stack"], 7.0, atol=1e-12)
    assert res["mean_temporal_weight"] == pytest.approx(1.0, abs=1e-12)
    # 全 nodata → NoValidObservations
    with pytest.raises(NoValidObservations):
        multitemporal_speckle(np.full((3, 4, 4), np.nan))


# ── 6. 复数相干性（EXPERIMENTAL）─────────────────────────────────────

def test_coherence_self_is_one_and_decorrelation():
    rng = np.random.RandomState(7)
    phase = rng.uniform(0.0, 2.0 * np.pi, size=(32, 32))
    slc_a = np.exp(1j * phase)
    res_self = coherence_estimate(slc_a, slc_a, window=5)
    # a == b → γ ≡ 1（窗口内）
    assert res_self["gamma"].min() == pytest.approx(1.0, abs=1e-9)
    assert res_self["meta"]["scientific_status"] == "EXPERIMENTAL"
    # 有效对计数：内点 = w²（25），窗口计数在场
    assert res_self["valid_pairs"][16, 16] == pytest.approx(25.0, abs=1e-9)

    # 随机相位平移（失相干）→ γ 显著 < 1
    slc_b = np.roll(slc_a, 3, axis=1)
    res_shift = coherence_estimate(slc_a, slc_b, window=5)
    assert res_shift["gamma"].max() < 0.9
    # re/im 双通道（纯 JSON 工具路径）与 complex 路径 parity
    res_tuple = coherence_estimate(
        (slc_a.real, slc_a.imag), (slc_b.real, slc_b.imag), window=5)
    np.testing.assert_allclose(res_tuple["gamma"], res_shift["gamma"],
                               atol=1e-12)
    # 确定性重放
    np.testing.assert_array_equal(
        res_shift["gamma"], coherence_estimate(slc_a, slc_b,
                                               window=5)["gamma"])


def test_coherence_guards():
    rng = np.random.RandomState(3)
    phase = rng.uniform(0, 2 * np.pi, size=(12, 12))
    slc = np.exp(1j * phase)
    # 强度-only（纯实数）→ UnsupportedMethod（相位不可虚构）
    with pytest.raises(UnsupportedMethod, match="纯实数"):
        coherence_estimate(np.abs(slc), np.abs(slc))
    # complex 但虚部全零 → UnsupportedMethod
    with pytest.raises(UnsupportedMethod, match="虚部全零"):
        coherence_estimate(slc + 0.0j, np.zeros((12, 12), dtype=complex))
    # re/im 形状不一致 → ValueError
    with pytest.raises(ValueError, match="形状不一致"):
        coherence_estimate((slc.real, slc.imag[:, :10]), (slc.real, slc.imag))
    # 两历元形状不一致 → ValueError；窗口守卫 → ValueError
    with pytest.raises(ValueError, match="形状不一致"):
        coherence_estimate(slc, np.exp(1j * phase[:10, :10]))
    with pytest.raises(ValueError, match="window"):
        coherence_estimate(slc, slc, window=4)
    # 全 nodata → NoValidObservations
    with pytest.raises(NoValidObservations):
        coherence_estimate(
            np.full((8, 8), np.nan, dtype=complex),
            np.full((8, 8), np.nan, dtype=complex))


# ── 7. RTC 地形辐射校正 ──────────────────────────────────────────────

def test_rtc_flattens_synthetic_slope():
    sigma = np.full((10, 10), 2.0)
    flat = np.zeros((10, 10))
    # 平地：θl = θi → γ_flat = σ⁰ 恒等（逐位）
    res_flat = radiometric_terrain_correction(
        sigma, flat, 1.0, 270.0, incidence_deg=35.0)
    assert res_flat["array"][5, 5] == 2.0
    assert res_flat["meta"]["invalid_geometry_pixels"] == 0

    # 合成斜坡（α=20°，西坡向即下坡朝西=朝向传感器）：
    # θl = θi − α = 15° → γ_flat = σ⁰·cos35°/cos15°（手算精确）
    alpha = math.radians(20.0)
    xs = np.arange(10) * 1.0
    dem = np.tile(math.tan(alpha) * xs, (10, 1))
    res = radiometric_terrain_correction(
        sigma, dem, 1.0, 270.0, incidence_deg=35.0)
    expect = 2.0 * math.cos(math.radians(35.0)) / math.cos(math.radians(15.0))
    assert res["array"][5, 5] == pytest.approx(expect, rel=1e-9)
    assert res["local_incidence_deg"][5, 5] == pytest.approx(15.0, abs=1e-6)
    # 背坡（下坡朝东=背向传感器）：θl = θi + α → 投影面积增大、
    # γ_flat = σ⁰·cosθi/cos(θi+α) > σ⁰（放大；面坡 1.70 < σ⁰=2 < 背坡）
    dem_back = np.tile(-math.tan(alpha) * xs, (10, 1))
    res_back = radiometric_terrain_correction(
        sigma, dem_back, 1.0, 270.0, incidence_deg=35.0)
    expect_back = 2.0 * math.cos(math.radians(35.0)) \
        / math.cos(math.radians(55.0))
    assert res_back["array"][5, 5] == pytest.approx(expect_back, rel=1e-9)
    # 面坡衰减（< σ⁰）、背坡放大（> σ⁰）——方向性 golden
    assert res["array"][5, 5] < 2.0 < res_back["array"][5, 5]
    # 确定性
    np.testing.assert_array_equal(
        res["array"],
        radiometric_terrain_correction(
            sigma, dem, 1.0, 270.0, incidence_deg=35.0)["array"])


def test_rtc_layover_guard_and_lut():
    sigma = np.full((10, 10), 2.0)
    xs = np.arange(10) * 1.0
    # 面坡且 α=50° > θi=30° → 叠掩 → nodata（计数披露）
    dem_steep = np.tile(math.tan(math.radians(50.0)) * xs, (10, 1))
    res = radiometric_terrain_correction(
        sigma, dem_steep, 1.0, 270.0, incidence_deg=30.0)
    assert np.isnan(res["array"][5, 5])
    assert res["meta"]["invalid_geometry_pixels"] >= 1
    assert "叠掩" in res["meta"]["disclosure"]
    assert "无轨道元数据" in res["meta"]["convention"]

    # 入射角 LUT 与标量 parity（常数 LUT）
    lut = np.full((10, 10), 35.0)
    res_lut = radiometric_terrain_correction(
        sigma, np.zeros((10, 10)), 1.0, 270.0, incidence_map=lut)
    res_scalar = radiometric_terrain_correction(
        sigma, np.zeros((10, 10)), 1.0, 270.0, incidence_deg=35.0)
    np.testing.assert_array_equal(res_lut["array"], res_scalar["array"])
    assert res_lut["meta"]["incidence_mode"] == "per_pixel"
    assert res_lut["meta"]["lut_pixels"] == 100

    # 必需参数守卫：入射角 / cell_size 缺失 → MissingRequiredField
    with pytest.raises(MissingRequiredField):
        radiometric_terrain_correction(sigma, np.zeros((10, 10)), 1.0, 270.0)
    with pytest.raises(MissingRequiredField):
        radiometric_terrain_correction(sigma, np.zeros((10, 10)), None, 270.0,
                                       incidence_deg=35.0)
    with pytest.raises(MissingRequiredField):
        radiometric_terrain_correction(sigma, np.zeros((10, 10)), 1.0, None,
                                       incidence_deg=35.0)
    # 方位角越界 → InvalidUnits；cell_size 非正 → ValueError
    with pytest.raises(InvalidUnits):
        radiometric_terrain_correction(sigma, np.zeros((10, 10)), 1.0, 400.0,
                                       incidence_deg=35.0)
    with pytest.raises(ValueError, match="cell_size"):
        radiometric_terrain_correction(sigma, np.zeros((10, 10)), 0.0, 270.0,
                                       incidence_deg=35.0)
    # DEM 形状不一致 → ValueError
    with pytest.raises(ValueError, match="同形"):
        radiometric_terrain_correction(sigma, np.zeros((5, 5)), 1.0, 270.0,
                                       incidence_deg=35.0)


# ── 8. 叠掩/阴影几何分类 ─────────────────────────────────────────────

def _wedge_dem() -> np.ndarray:
    """楔形 DEM：平地（列 0-4）| 面坡 65°（列 5-9）| 背坡 65°（列 10-14）
    | 平地（列 15-29）；传感器在西（270°）、θi=30°。"""
    t = math.tan(math.radians(65.0))
    dem = np.zeros((12, 30))
    for x in range(30):
        if 5 <= x <= 9:
            dem[:, x] = t * (x - 5)          # 下坡朝西 → 面坡 → layover
        elif 10 <= x <= 14:
            dem[:, x] = t * (19 - x)         # 下坡朝东 → 背坡 → shadow
        elif x > 14:
            dem[:, x] = t * 5.0              # 平台 → normal
    return dem


def test_layover_shadow_wedge_classification():
    dem = _wedge_dem()
    res = layover_shadow_mask(dem, 1.0, 270.0, incidence_deg=30.0)
    mask = res["mask"]
    # 逐像元类别：平地 normal、面坡陡于 θi → layover、背坡超掠射角 → shadow
    assert mask[6, 2] == 0.0
    assert mask[6, 7] == 1.0
    assert mask[6, 12] == 2.0
    assert mask[6, 25] == 0.0
    # 三类占比均为正、合计 1
    fr = res["fractions"]
    assert fr["layover"] > 0 and fr["shadow"] > 0 and fr["normal"] > 0
    assert fr["nodata"] == 0.0
    assert abs(sum(fr.values()) - 1.0) < 1e-9
    # 缓坡面坡（α < θi）→ normal（非 layover）
    dem_gentle = np.zeros((8, 16))
    tg = math.tan(math.radians(10.0))
    for x in range(16):
        if 4 <= x <= 11:
            dem_gentle[:, x] = tg * x
    res_gentle = layover_shadow_mask(dem_gentle, 1.0, 270.0,
                                     incidence_deg=30.0)
    assert res_gentle["mask"][4, 8] == 0.0
    # 确定性
    np.testing.assert_array_equal(
        mask, layover_shadow_mask(dem, 1.0, 270.0,
                                  incidence_deg=30.0)["mask"])
    # 诚实披露：无 ray-casting 遮蔽 + range-only 简化
    assert "ray-casting" in res["meta"]["disclosure"]
    assert "无轨道元数据" in res["meta"]["convention"]


def test_layover_shadow_guards():
    dem = _wedge_dem()
    # 缺方位角 / cell_size → MissingRequiredField；方位角越界 → InvalidUnits
    with pytest.raises(MissingRequiredField):
        layover_shadow_mask(dem, 1.0, None, incidence_deg=30.0)
    with pytest.raises(MissingRequiredField):
        layover_shadow_mask(dem, None, 270.0, incidence_deg=30.0)
    with pytest.raises(InvalidUnits):
        layover_shadow_mask(dem, 1.0, -5.0, incidence_deg=30.0)
    with pytest.raises(MissingRequiredField):
        layover_shadow_mask(dem, 1.0, 270.0)
    # DEM NaN → nodata 类（3）+ 占比披露
    dem_nd = dem.copy()
    dem_nd[6, 2] = np.nan
    res_nd = layover_shadow_mask(dem_nd, 1.0, 270.0, incidence_deg=30.0)
    assert res_nd["mask"][6, 2] == 3.0
    assert res_nd["fractions"]["nodata"] > 0.0
    # 入射角 LUT parity（常数 LUT == 标量）
    lut = np.full(dem.shape, 30.0)
    res_lut = layover_shadow_mask(dem, 1.0, 270.0, incidence_map=lut)
    res_scalar = layover_shadow_mask(dem, 1.0, 270.0, incidence_deg=30.0)
    np.testing.assert_array_equal(res_lut["mask"], res_scalar["mask"])
    assert res_lut["meta"]["incidence_mode"] == "per_pixel"
    # 非 2D → ValueError
    with pytest.raises(ValueError, match="2D"):
        layover_shadow_mask(np.zeros(10), 1.0, 270.0, incidence_deg=30.0)


# ── 9. ENL 图 ────────────────────────────────────────────────────────

def test_enl_map_recovers_synthetic_enl():
    rng = np.random.RandomState(11)
    speckle = rng.gamma(shape=4.0, scale=25.0, size=(48, 48))
    res = enl_map(speckle, window=7)
    # 全局 ENL ≈ 4（矩估计无偏），滑窗 ENL 中位 ≈ 4（窗口容差内）
    assert 3.0 < res["global_enl"] < 5.5
    finite = res["enl_map"][np.isfinite(res["enl_map"])]
    assert finite.size > 0
    assert 2.5 < float(np.median(finite)) < 6.0
    assert res["meta"]["window"] == 7
    assert "低估" in res["meta"]["disclosure"]
    # 确定性重放
    np.testing.assert_array_equal(
        res["enl_map"], enl_map(speckle, window=7)["enl_map"])


def test_enl_map_guards():
    rng = np.random.RandomState(5)
    speckle = rng.gamma(4.0, 25.0, size=(24, 24))
    # 常数块 → 退化窗口 NaN + 计数披露
    mixed = speckle.copy()
    mixed[8:16, 8:16] = 100.0
    res = enl_map(mixed, window=3)
    assert bool(np.all(~np.isfinite(res["enl_map"][9:15, 9:15])))
    assert res["meta"]["degenerate_windows"] >= 36
    # 常数场 → DegenerateData（全局 ENL 无定义）
    with pytest.raises(DegenerateData):
        enl_map(np.full((9, 9), 3.0), window=3)
    # 负值（dB）→ UnsupportedMethod；窗口守卫 → ValueError
    with pytest.raises(UnsupportedMethod):
        enl_map(np.array([[1.0, -2.0], [3.0, 4.0]]))
    with pytest.raises(ValueError, match="window"):
        enl_map(speckle, window=6)
    # 全 nodata → DegenerateData
    with pytest.raises(DegenerateData):
        enl_map(np.full((8, 8), np.nan))


# ── 10. 注册表 / parity / 工具 schema / 证据块 ────────────────────────

def test_sar_v3_registry_validate_and_parity():
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.capability_registry import get_capability_registry
    from app.services.gis_harness.registry_validation import (
        validate_algorithm_tool_parameter_parity,
    )

    my_prefixes = (
        "sar.thermal", "sar.log_scaling", "sar.multitemporal",
        "sar.coherence", "sar.rtc", "sar.layover", "sar.enl_map",
        "sar.speckle_filter", "sar.radiometric_calibration",
        "capability sar_coherence", "capability sar_terrain",
    )
    issues = get_algorithm_registry().validate()
    mine = [i for i in issues if any(p in i for p in my_prefixes)]
    assert mine == [], mine

    parity_issues = validate_algorithm_tool_parameter_parity()
    assert parity_issues == [], parity_issues

    reg = get_algorithm_registry()
    caps = get_capability_registry()
    # 新能力：native 且至少绑定一个 native 算法
    for cap_id in ("sar_coherence", "sar_terrain_geometry_correction"):
        cap = caps.get(cap_id)
        assert cap is not None and cap.status == "native", cap_id
        algos = reg.algorithms_for_capability(cap_id)
        assert algos, cap_id
        assert all(a.runtime_status == "native" for a in algos), cap_id
    # 成熟度：EXPERIMENTAL 相干性 vs VALIDATED 其余
    assert reg.get("sar.coherence").scientific_status == "EXPERIMENTAL"
    for algo_id in ("sar.thermal_noise_removal", "sar.log_scaling",
                    "sar.multitemporal_speckle", "sar.rtc",
                    "sar.layover_shadow", "sar.enl_map"):
        algo = reg.get(algo_id)
        assert algo is not None and algo.runtime_status == "native"
        assert algo.scientific_status == "VALIDATED", algo_id
        assert algo.parameter_contract_ref, algo_id
    # 折入既有能力的 V3 算法（不另立新族）
    folded = {
        "sar.thermal_noise_removal": "sar_radiometric_calibration",
        "sar.log_scaling": "sar_radiometric_calibration",
        "sar.multitemporal_speckle": "sar_speckle_filtering",
        "sar.enl_map": "sar_speckle_filtering",
    }
    for algo_id, cap_id in folded.items():
        assert cap_id in reg.get(algo_id).capabilities, algo_id
    # 契约 v2：speckle 枚举扩到 5 方法 + gamma_map 迭代上限参数
    from app.lib.gis.parameter_contracts import (
        get_parameter_contract_registry,
    )

    contracts = get_parameter_contract_registry()
    speckle_contract = contracts.get("sar_speckle_filter_analysis")
    assert speckle_contract.version == 2
    filter_spec = speckle_contract.spec("filter")
    assert set(filter_spec.enum_values) == {
        "lee", "refined_lee", "frost", "gamma_map", "kuan"}
    assert speckle_contract.spec("max_iterations") is not None
    calib_contract = contracts.get("sar_calibration_analysis")
    assert calib_contract.version == 2
    assert calib_contract.spec("incidence_lut") is not None


def test_sar_v3_tools_schema_and_evidence():
    from app.tools.registry import ToolRegistry
    from app.tools.remote_sensing import register_rs_tools

    registry = ToolRegistry()
    register_rs_tools(registry)
    new_tools = {
        "sar_remove_thermal_noise", "sar_log_scale",
        "sar_multitemporal_speckle", "sar_coherence_estimate",
        "sar_radiometric_terrain_correction", "sar_layover_shadow_mask",
        "sar_enl_map",
    }
    assert new_tools <= set(registry.list_tools())

    def _schema(name):
        for s in registry.get_schemas():
            if s["function"]["name"] == name:
                return s["function"]
        return None

    # 契约必填参数必须在工具 schema properties（parity 门语义）
    fn = _schema("sar_log_scale")
    assert "mode" in fn["parameters"]["properties"]
    fn = _schema("sar_radiometric_terrain_correction")
    for req in ("cell_size", "radar_range_azimuth"):
        assert req in fn["parameters"]["properties"]
    fn = _schema("sar_calibrate")
    assert "incidence_lut" in fn["parameters"]["properties"]
    fn = _schema("sar_speckle_filter")
    assert "max_iterations" in fn["parameters"]["properties"]

    # 热噪声工具：payload + 证据块（sar.thermal_noise_removal）
    thermal_fn = registry._tools["sar_remove_thermal_noise"]
    payload = asyncio.run(thermal_fn([[5.0, 1.0], [0.5, 2.0]],
                                     noise_floor=1.0))
    assert payload["success"] is True
    assert payload["clamped_pixels"] == 1
    ev = payload["scientific_evidence"]
    assert ev["algorithm"] == "sar.thermal_noise_removal"
    assert ev["assumptions"] and ev["limitations"]

    # 相干性工具：re/im 双通道 JSON 通道 + EXPERIMENTAL 状态在场
    # （自相干 γ≈1；虚部全零会被类型化拒绝——见 test_coherence_guards）
    xs = np.arange(6)
    real = np.cos(0.7 * xs)[None, :] + np.zeros((6, 1))
    imag = np.sin(0.7 * xs)[None, :] + np.zeros((6, 1))
    coh_fn = registry._tools["sar_coherence_estimate"]
    coh = asyncio.run(coh_fn(real.tolist(), imag.tolist(),
                             real.tolist(), imag.tolist()))
    assert coh["success"] is True
    assert coh["scientific_evidence"]["algorithm"] == "sar.coherence"
    assert coh["stats"]["min"] == pytest.approx(1.0, abs=1e-9)

    # 定标工具：入射角 LUT 通道 + lut_pixels 披露
    calib_fn = registry._tools["sar_calibrate"]
    lut = [[30.0, 30.0], [30.0, 30.0]]
    calib = asyncio.run(calib_fn([[2.0, 4.0], [6.0, 8.0]],
                                 calibration_constant=2.0,
                                 incidence_lut=lut))
    assert calib["success"] is True
    assert calib["lut_pixels"] == 4
    assert calib["incidence_lut_source"] == "incidence_lut"
    assert calib["scientific_evidence"]["algorithm"] == \
        "sar.radiometric_calibration"

    # speckle 工具：gamma_map 新方法可用（iterations_used 披露）
    spk_fn = registry._tools["sar_speckle_filter"]
    img = np.random.RandomState(4).gamma(4.0, 25.0, size=(6, 6)).tolist()
    spk = asyncio.run(spk_fn(img, filter="gamma_map", window="3", enl=4.0))
    assert spk["success"] is True and spk["method"] == "gamma_map"
    assert 1 <= spk["iterations_used"] <= 10

    # ENL 图工具：全局 ENL ≈ 4 + 证据块
    enl_fn = registry._tools["sar_enl_map"]
    emap = asyncio.run(enl_fn(img, window="3"))
    assert emap["success"] is True
    assert 2.0 < emap["global_enl"] < 7.0
    assert emap["scientific_evidence"]["algorithm"] == "sar.enl_map"
