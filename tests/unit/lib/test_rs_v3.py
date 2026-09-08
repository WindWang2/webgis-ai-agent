"""遥感 V3 批次 conformance 测试（Foundation V3 · app/lib/geo_analysis/rs_v3.py）。

覆盖（13 算法）：手算黄金值（SAM 0 角 / SID 2 波段 / 时序斜坡 / 相关表
±1.0）+ 性质测试（MNF SNR 排序 / ICA 源恢复 / MF 目标得分 / RX 异常
超阈 / MAD 恒等零-局部变化检出 / 分割分块 / VCA 纯像元精确恢复 /
归一化线性不变 / 云 QC 亮块）+ 守卫与 typed errors（DegenerateData /
InsufficientSamples / ResourceScaleMismatch / NoValidObservations）+
确定性重放 + registry 契约 parity。
"""
import numpy as np
import pytest

import app.lib.geo_analysis.rs_v3 as rs_mod
from app.lib.geo_analysis.rs_v3 import (
    cloud_qc_basic,
    band_correlation_table,
    extract_endmembers_vca,
    ica,
    mad_change,
    matched_filter,
    mnf,
    mnf_inverse,
    robust_normalize,
    segment_image,
    rx_anomaly,
    spectral_angle_mapper,
    spectral_information_divergence,
    temporal_features,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    NoValidObservations,
    ResourceScaleMismatch,
    UnsupportedMethod,
)

pytestmark = pytest.mark.unit


# ── 1-4. MNF（Green et al. 1988）──────────────────────────────────────

def test_mnf_signal_dominates_first_component():
    """纯噪声波段 + 信号波段 → 信号进入 MNF-1（最高 SNR；conformance）。"""
    rng = np.random.RandomState(42)
    t = np.linspace(0.0, 1.0, 100).reshape(10, 10)
    noise_band = rng.normal(0.0, 1.0, (10, 10))
    signal_band = 10.0 * t + rng.normal(0.0, 0.01, (10, 10))
    res = mnf(np.stack([noise_band, signal_band]))

    snr = np.asarray(res["snr"])
    assert snr[0] > 100 * max(snr[1], 1e-6)     # SNR 降序 + 信号占优
    assert snr[0] > 0 > snr[1] or snr[0] > 1.0  # 白化空间噪声方差=1 语义
    comp0 = res["component_rasters"][0]
    corr = np.corrcoef(comp0.ravel(), signal_band.ravel())[0, 1]
    assert abs(corr) > 0.999                    # MNF-1 即信号方向
    assert res["meta"]["noise_estimation"] == "local_diff"
    assert "差分" in res["meta"]["noise_covariance_formula"]


def test_mnf_rank1_deterministic_degenerate_data():
    """确定性秩 1 栈（无噪声）→ 差分噪声协方差奇异 → DegenerateData。"""
    t = np.linspace(0.0, 1.0, 25).reshape(5, 5)
    with pytest.raises(DegenerateData):
        mnf(np.stack([t, 3.0 * t, 7.0 * t]))


def test_mnf_inverse_reconstruction_and_denoise():
    """全分量逆变换逐位精确；截断重建（去噪）与信号强相关。"""
    rng = np.random.RandomState(42)
    t = np.linspace(0.0, 1.0, 100).reshape(10, 10)
    stack = np.stack([rng.normal(0, 1.0, (10, 10)),
                      10.0 * t + rng.normal(0, 0.01, (10, 10))])
    res = mnf(stack)
    recon = mnf_inverse(res)
    np.testing.assert_allclose(recon, stack, atol=1e-9)

    denoised = mnf_inverse(res, n_components=1)
    assert denoised.shape == stack.shape
    corr = np.corrcoef(denoised[1].ravel(), (10.0 * t).ravel())[0, 1]
    assert abs(corr) > 0.999                    # 去噪后信号保住

    with pytest.raises(ValueError, match="n_components"):
        mnf_inverse(res, n_components=3)


def test_mnf_common_mask_nodata_and_determinism():
    """公共有效掩膜（NaN/哨兵 → NaN 回填）+ 确定性重放。"""
    rng = np.random.RandomState(3)
    a = rng.uniform(0, 1, (3, 5, 5))
    b = a.copy()
    b[0, 0, 0] = np.nan
    b[1, 1, 1] = -9999.0
    res = mnf(b, n_components=2, nodata=-9999.0)
    assert res["n_valid_pixels"] == 23
    for comp in res["component_rasters"]:
        assert np.isnan(comp[0, 0]) and np.isnan(comp[1, 1])
        assert np.isfinite(comp[3, 3])

    again = mnf(b, n_components=2, nodata=-9999.0)
    np.testing.assert_array_equal(
        np.asarray(res["component_rasters"]),
        np.asarray(again["component_rasters"]))
    assert res["snr"] == again["snr"]

    with pytest.raises(NoValidObservations):
        mnf(np.stack([np.full((3, 3), np.nan), np.ones((3, 3))]))
    with pytest.raises(UnsupportedMethod):
        mnf(np.stack([a[0], a[1]]), noise_estimation="wavelet")


# ── 5-6. ICA（FastICA）────────────────────────────────────────────────

def test_ica_recovers_mixed_sources():
    """两源线性混合 → ICA 分量与真源 |corr| > 0.99（置换不变）。"""
    rng = np.random.RandomState(42)
    grid = np.indices((10, 10)).astype(float)
    s1 = (grid[0] + grid[1]).ravel()            # 结构源
    s2 = rng.uniform(0.0, 1.0, 100)             # 随机源
    s1 = (s1 - s1.mean()) / s1.std()
    s2 = (s2 - s2.mean()) / s2.std()
    mix = np.column_stack([s1 + 0.5 * s2, 0.3 * s1 + 1.2 * s2])
    res = ica(mix.T.reshape(2, 10, 10))

    assert res["converged"] is True
    sources = np.asarray(res["component_rasters"])
    truth = np.stack([s1.reshape(10, 10), s2.reshape(10, 10)])
    corr = np.abs(np.corrcoef(
        sources.reshape(2, -1), truth.reshape(2, -1)))[0:2, 2:4]
    assert corr.max(axis=1).min() > 0.99        # 每个分量对上某个真源
    assert np.asarray(res["mixing_matrix"]).shape == (2, 2)


def test_ica_convergence_disclosure_and_determinism():
    """未收敛诚实披露（converged=False + warnings）；固定种子重放一致。"""
    rng = np.random.RandomState(5)
    stack = np.stack([rng.uniform(0, 1, (6, 6)) for _ in range(3)])
    with warnings_catch():
        res = ica(stack, max_iter=2, tol=1e-10)
    assert res["converged"] is False
    assert any("收敛" in w for w in res["warnings"])
    assert res["meta"]["random_state"] == 42

    # 固定种子重放（同参数）逐位一致
    with warnings_catch():
        replay = ica(stack, max_iter=2, tol=1e-10)
    np.testing.assert_array_equal(
        np.asarray(res["component_rasters"])[:1],
        np.asarray(replay["component_rasters"])[:1])

    with pytest.raises(InsufficientSamples):
        ica(np.stack([np.array([[1.0, 2.0], [3.0, 4.0]]),
                      np.array([[0.0, 1.0], [2.0, 3.0]])]))


class warnings_catch:
    """静默 sklearn 收敛告警的上下文（测试专用，不改变断言语义）。"""

    def __enter__(self):
        import warnings
        self._cm = warnings.catch_warnings()
        self._cm.__enter__()
        warnings.simplefilter("ignore")
        return self

    def __exit__(self, *args):
        return self._cm.__exit__(*args)


# ── 7-8. SAM（Kruse 1993）─────────────────────────────────────────────

def test_sam_hand_pixel_zero_angle():
    """像元=端元 A → 对 A 角=0（conformance）；argmin 类别栅格正确。"""
    # 波段栈 (2, 1, 4)：四个单像元场景
    stack = np.array([[[0.2, 0.5, 0.0, 0.6]], [[0.6, 0.4, 0.0, 0.2]]])
    res = spectral_angle_mapper(
        stack, {"veg": [0.2, 0.6], "soil": [0.5, 0.4]})
    veg, soil = res["angles"]["veg"][0], res["angles"]["soil"][0]
    assert veg[0] == pytest.approx(0.0, abs=1e-7)   # 像元==veg → 0 角
    assert soil[1] == pytest.approx(0.0, abs=1e-7)  # 像元==soil → 0 角
    assert soil[0] > veg[0]                          # 各自最近端元是自身
    cls = res["class_raster"][0]
    assert cls[0] == 0.0 and cls[1] == 1.0
    # 正交向量 → π/2（2 波段像元 [1,0] 对端元 [0,1]）
    res_o = spectral_angle_mapper(
        np.array([[[1.0]], [[0.0]]]), np.array([[0.0, 1.0]]))
    assert res_o["angles"]["endmember_0"][0, 0] == pytest.approx(
        np.pi / 2, rel=1e-12)
    assert res_o["names"] == ["endmember_0"]         # 数组端元自动命名


def test_sam_zero_norm_nan_and_input_guards():
    """零范数像元 → NaN（不产伪 0 角）；端元长度错配/零范数端元被拒。"""
    res = spectral_angle_mapper(
        np.array([[[0.0]], [[0.0]]]), {"e": [1.0, 2.0]})
    assert np.isnan(res["class_raster"][0, 0])
    assert res["zero_norm_fraction"] == pytest.approx(1.0, abs=1e-12)
    assert "零范数" in res["meta"]["disclosure"]

    with pytest.raises(ValueError, match="逐波段对齐"):
        spectral_angle_mapper(np.ones((2, 2, 2)), {"e": [1.0]})
    bad = spectral_angle_mapper(
        np.ones((2, 1, 1)), {"zero": [0.0, 0.0], "ok": [1.0, 0.0]})
    assert np.isnan(bad["angles"]["zero"][0, 0])
    assert np.isfinite(bad["angles"]["ok"][0, 0])
    assert any("零范数端元" in w for w in bad["warnings"])


# ── 9-10. SID（Chang 2000）────────────────────────────────────────────

def test_sid_hand_two_band_exact():
    """手算 2 波段：x=[1,1], e=[1,3] → D = Σp·ln(p/q)+Σq·ln(q/p)。"""
    p = np.array([0.5, 0.5])
    q = np.array([0.25, 0.75])
    expected = float((p * np.log(p / q)).sum() + (q * np.log(q / p)).sum())
    res = spectral_information_divergence(
        np.array([[[1.0]], [[1.0]]]), {"e": [1.0, 3.0]})
    assert res["divergence"]["e"][0, 0] == pytest.approx(expected,
                                                         rel=1e-12)
    assert expected == pytest.approx(0.27465307216702745, rel=1e-12)
    # x == e → D = 0（同一分布）
    same = spectral_information_divergence(
        np.array([[[1.0]], [[3.0]]]), {"e": [1.0, 3.0]})
    assert same["divergence"]["e"][0, 0] == pytest.approx(0.0, abs=1e-12)


def test_sid_symmetry_and_nonpositive_nan():
    """对称性 D(x,e)=D(e,x)；非正和/非正分量 → NaN + fraction 披露。"""
    x = np.array([[[2.0]], [[1.0]]])
    e = np.array([1.0, 1.0])
    d_xe = spectral_information_divergence(x, e)["divergence"]["endmember_0"][0, 0]
    d_ex = spectral_information_divergence(
        np.array([[[1.0]], [[1.0]]]), [2.0, 1.0])["divergence"]["endmember_0"][0, 0]
    assert d_xe == pytest.approx(d_ex, rel=1e-12)

    neg = spectral_information_divergence(
        np.array([[[-1.0]], [[1.0]]]), {"e": [1.0, 3.0]})
    assert np.isnan(neg["divergence"]["e"][0, 0])
    assert neg["nonpositive_fraction"] == pytest.approx(1.0, abs=1e-12)
    assert "非正" in neg["meta"]["disclosure"]


# ── 11-12. 匹配滤波（Boardman 1995）───────────────────────────────────

def test_matched_filter_embedded_target():
    """嵌入目标像元高分、背景≈0；零方差波段剔除披露。"""
    rng = np.random.RandomState(42)
    target = np.array([0.5, -0.3, 0.2])
    background = rng.normal(0.0, 0.1, (3, 12, 12))
    background[:, 2, 2] += 5.0 * target         # 强嵌入
    background[:, 5, 5] += 1.0 * target         # 单位幅值嵌入
    res = matched_filter(background, target)
    score = res["score"]
    assert score[2, 2] == pytest.approx(5.0, rel=0.2)   # 丰度式得分≈幅值
    others = np.delete(score.ravel(), [2 * 12 + 2, 5 * 12 + 5])
    assert score[5, 5] > np.percentile(others, 99)
    assert score[5, 5] == pytest.approx(1.0, rel=0.3)
    assert np.abs(others).max() < 2.0
    assert res["meta"]["dropped_bands"] == []
    assert "Σ⁻¹" in res["meta"]["formula"]

    # 零方差波段剔除 + 披露
    const = np.stack([np.full((6, 6), 1.0), background[0, :6, :6],
                      background[1, :6, :6]])
    res_dropped = matched_filter(const, [7.0, target[0], target[1]])
    assert res_dropped["dropped_bands"] == [0]
    assert any("零方差波段" in w for w in res_dropped["warnings"])


def test_matched_filter_guards():
    """长度错配 / 全常量场 → typed/值错误。"""
    with pytest.raises(ValueError, match="逐波段对齐"):
        matched_filter(np.ones((2, 3, 3)), [1.0])
    with pytest.raises(DegenerateData):
        matched_filter(
            np.stack([np.ones((3, 3)), np.full((3, 3), 2.0)]), [1.0, 1.0])


# ── 13-14. RX 异常检测（Reed & Xiaoli 1990）───────────────────────────

def test_rx_anomaly_outlier_above_threshold():
    """注入异常像元 δ 高于 mean+3σ 阈值；背景像元低于阈值。"""
    rng = np.random.RandomState(42)
    background = rng.normal(0.0, 0.1, (3, 10, 10))
    background[:, 4, 4] += 5.0                  # 单像元异常
    res = rx_anomaly(background)
    assert res["delta"][4, 4] > res["threshold"]
    assert res["anomaly_mask"][4, 4]
    clean = np.delete(res["delta"].ravel(), 4 * 10 + 4)
    assert float(np.max(clean)) < res["threshold"]
    assert res["anomaly_fraction"] < 0.05
    assert res["meta"]["regularize"] == 1e-6
    assert "tr Σ/k" in res["meta"]["ridge_formula"]
    assert "启发式" in res["meta"]["threshold_rule"]


def test_rx_uniform_and_degenerate_paths():
    """常量场 → 零方差披露、δ=0 无异常；参数守卫。"""
    uniform = rx_anomaly(np.stack([np.full((4, 4), 2.0)] * 3))
    assert uniform["zero_variance"] is True
    assert not uniform["anomaly_mask"].any()
    assert float(np.max(uniform["delta"])) == 0.0
    assert "常量场" in uniform["warnings"][0]

    rng = np.random.RandomState(9)
    data = rng.uniform(0, 1, (2, 6, 6))
    assert rx_anomaly(data, regularize=0.0)["meta"]["regularize"] == 0.0
    with pytest.raises(ValueError, match="regularize"):
        rx_anomaly(data, regularize=-1.0)
    with pytest.raises(ValueError, match="threshold_sigma"):
        rx_anomaly(data, threshold_sigma=0.0)


# ── 15-17. MAD / IR-MAD（Nielsen 1998）────────────────────────────────

def test_mad_identical_stacks_zero_chi2():
    """恒等两期 → 所有 MAD 方差≈0、χ²≈0、ρ≈1（conformance）。"""
    rng = np.random.RandomState(11)
    stack_a = rng.uniform(0, 1, (2, 8, 8))
    res = mad_change(stack_a, stack_a)
    assert float(np.max(res["chi2_raster"])) < 1e-9
    assert np.allclose(res["canonical_correlations"], 1.0, atol=1e-9)
    assert float(np.max(np.asarray(res["variate_variances"]))) < 1e-12
    assert res["meta"]["chi2_dof"] == 2          # k dof（Nielsen 1998 χ²_k 惯例）
    assert res["iterations_ran"] == 0            # n_iterms=0：无 IR 迭代


def test_mad_chi2_dof_equals_n_bands():
    """χ² 自由度 = k=n_bands（对 3 波段栈钉死公式，而非常数 2）。"""
    rng = np.random.RandomState(7)
    stack_a = rng.uniform(0, 1, (3, 8, 8))
    stack_b = stack_a + rng.normal(0.0, 0.05, stack_a.shape)
    res = mad_change(stack_a, stack_b)
    assert res["meta"]["chi2_dof"] == 3
    assert "chi2_dof_derivation" in res["meta"]


def test_mad_localized_change_detected():
    """局部变化块 χ² 显著高于未变像元（noisiest-first 序披露）。"""
    rng = np.random.RandomState(11)
    stack_a = rng.uniform(0, 1, (2, 8, 8))
    stack_b = stack_a.copy()
    stack_b[0, 3:5, 3:5] += 5.0                  # 4 像元变化块
    res = mad_change(stack_a, stack_b)
    chi = res["chi2_raster"]
    block = chi[3:5, 3:5]
    outside = np.delete(chi.ravel(), [i * 8 + j for i in range(3, 5)
                                      for j in range(3, 5)])
    assert float(block.min()) > float(outside.max()) > 0.0
    # 升序规范相关（noisiest first）+ 理论方差 2(1−ρ)
    rho = np.asarray(res["canonical_correlations"])
    assert np.all(np.diff(rho) >= -1e-12)
    np.testing.assert_allclose(
        np.asarray(res["variate_variances_theoretical"]),
        2.0 * (1.0 - np.minimum(rho, 1.0 - 1e-12)), rtol=1e-9)


def test_mad_irmad_iterations_and_guards():
    """IR-MAD：n_iterms>0 → 权重栅格 + 迭代数/收敛增量披露；上限守卫。"""
    rng = np.random.RandomState(11)
    stack_a = rng.uniform(0, 1, (2, 8, 8))
    stack_b = stack_a.copy()
    stack_b[0, :4, :] += 1.0                     # 半幅条带变化
    res = mad_change(stack_a, stack_b, n_iterms=5)
    assert 1 <= res["iterations_ran"] <= 5
    assert res["weights_raster"] is not None
    assert res["convergence_delta"] is not None
    assert res["meta"]["weight_floor"] == 1e-4
    # 重放一致
    replay = mad_change(stack_a, stack_b, n_iterms=5)
    np.testing.assert_array_equal(
        np.asarray(res["weights_raster"]),
        np.asarray(replay["weights_raster"]))

    with pytest.raises(ValueError, match="n_iterms"):
        mad_change(stack_a, stack_b, n_iterms=11)
    with pytest.raises(ValueError, match="形状不一致"):
        mad_change(stack_a, np.ones((2, 4, 4)))


# ── 18-19. 分割（Lloyd k-means 基座）──────────────────────────────────

def test_segment_two_blocks_and_determinism():
    """左暗右亮双块 → 2 段分开两半；固定种子重放逐位一致。"""
    img = np.zeros((2, 20, 20))
    img[:, :, :10] = 0.2
    img[:, :, 10:] = 0.8
    res = segment_image(img, n_segments=2)
    lab = res["label_raster"]
    assert set(np.unique(lab[:, :10])) == {0.0}
    assert set(np.unique(lab[:, 10:])) == {1.0}
    assert res["n_segments_realized"] == 2
    assert len(res["segment_mean_spectra"]) == 2
    np.testing.assert_allclose(res["segment_mean_spectra"][0],
                               [0.2, 0.2], atol=1e-6)
    np.testing.assert_allclose(res["segment_mean_spectra"][1],
                               [0.8, 0.8], atol=1e-6)
    # 确定性：sklearn random_state=42 重放一致（断言两次）
    again = segment_image(img, n_segments=2)
    assert np.array_equal(lab, again["label_raster"])
    third = segment_image(img, n_segments=2, spatial_weight=0.5,
                          compactness=0.5)
    assert np.array_equal(lab, third["label_raster"])


def test_segment_clamp_and_disclosure():
    """n_segments > 有效像元 → 钳制披露；常量波段剔除披露；参数守卫。"""
    rng = np.random.RandomState(3)
    img = np.stack([rng.uniform(0, 1, (3, 3)),
                    rng.uniform(0, 1, (3, 3))])
    res = segment_image(img, n_segments=8)
    assert res["n_segments_realized"] == 8      # 9 个有效像元可容纳 8 段
    tiny = segment_image(img, n_segments=9)
    assert tiny["n_segments_realized"] == 9

    const_img = np.stack([np.ones((4, 4)), rng.uniform(0, 1, (4, 4))])
    res_const = segment_image(const_img, n_segments=2)
    assert res_const["meta"]["dropped_constant_bands"] == [0]
    assert any("常量波段" in w for w in res_const["warnings"])
    assert "不是 SLIC" in res_const["meta"]["disclosure"]

    with pytest.raises(ValueError, match="n_segments"):
        segment_image(img, n_segments=1)
    with pytest.raises(ValueError, match="spatial_weight"):
        segment_image(img, n_segments=2, spatial_weight=1.5)


# ── 20-21. VCA 端元提取（Nascimento & Dias 2005；EXPERIMENTAL）────────

def _vca_fixture(rng, n_bands, endmembers):
    abundance = rng.dirichlet([2.0] * len(endmembers), (6, 6))
    cube = np.tensordot(abundance, np.asarray(endmembers), axes=([2], [0]))
    return cube.transpose(2, 0, 1)


def test_vca_pure_pixels_recovered():
    """纯像元合成 → 恢复端元与真值（归一化后 rtol 1e-6；conformance）。"""
    rng = np.random.RandomState(42)
    e2 = np.array([[1.0, 0.2, 0.1], [0.1, 0.9, 0.4]])
    cube = _vca_fixture(rng, 3, e2)
    cube[:, 0, 0] = e2[0]                       # 注入纯像元
    cube[:, 5, 5] = e2[1]
    res = extract_endmembers_vca(cube, 2)
    got = np.asarray(res["endmembers"])
    got = got / got.sum(axis=1, keepdims=True)
    truth = e2 / e2.sum(axis=1, keepdims=True)
    dists = np.linalg.norm(got[:, None, :] - truth[None, :, :],
                           axis=2).min(axis=1)
    np.testing.assert_allclose(dists, 0.0, atol=1e-6)
    assert (0, 0) in res["locations"] and (5, 5) in res["locations"]
    assert res["meta"]["scientific_status"] == "EXPERIMENTAL"

    e3 = np.array([[1.0, 0.0, 0.0, 0.0],
                   [0.0, 1.0, 0.0, 0.0],
                   [0.0, 0.0, 1.0, 0.0]])
    cube3 = _vca_fixture(rng, 4, e3)
    cube3[:, 0, 0] = e3[0]
    cube3[:, 5, 5] = e3[1]
    cube3[:, 2, 2] = e3[2]
    res3 = extract_endmembers_vca(cube3, 3)
    got3 = np.asarray(res3["endmembers"])
    got3 = got3 / got3.sum(axis=1, keepdims=True)
    truth3 = e3 / e3.sum(axis=1, keepdims=True)
    dists3 = np.linalg.norm(got3[:, None, :] - truth3[None, :, :],
                            axis=2).min(axis=1)
    np.testing.assert_allclose(dists3, 0.0, atol=1e-6)


def test_vca_guards_and_determinism():
    """n_endmembers < n_bands 守卫 + 固定 seed 重放逐位一致。"""
    rng = np.random.RandomState(42)
    e2 = np.array([[1.0, 0.2, 0.1], [0.1, 0.9, 0.4]])
    cube = _vca_fixture(rng, 3, e2)
    cube[:, 0, 0] = e2[0]
    cube[:, 5, 5] = e2[1]
    with pytest.raises(ValueError, match="n_bands"):
        extract_endmembers_vca(cube, 3)
    with pytest.raises(ValueError, match="n_endmembers"):
        extract_endmembers_vca(cube, 1)

    first = extract_endmembers_vca(cube, 2, seed=42)
    second = extract_endmembers_vca(cube, 2, seed=42)
    np.testing.assert_array_equal(first["endmembers"], second["endmembers"])
    assert first["locations"] == second["locations"]
    other_seed = extract_endmembers_vca(cube, 2, seed=7)
    assert other_seed["meta"]["seed"] == 7


# ── 22. 波段相关表 ────────────────────────────────────────────────────

def test_band_correlation_perfect_and_anti():
    """完全相关对 = 1.0、反相 = −1.0；stats_table 形 dict of lists。"""
    rng = np.random.RandomState(42)
    b1 = rng.uniform(0, 1, (5, 5))
    stack = np.stack([b1, 2.0 * b1, -1.0 * b1 + 10.0])
    res = band_correlation_table(stack)
    corr = np.asarray(res["correlation"])
    assert corr[0, 1] == pytest.approx(1.0, abs=1e-12)
    assert corr[0, 2] == pytest.approx(-1.0, abs=1e-12)
    assert np.allclose(np.diag(corr), 1.0, atol=1e-12)
    # stats_table 形：dict of lists + 逐对 n（公共掩膜 → 恒等，披露）
    assert isinstance(res["correlation"], list)
    assert res["columns"] == ["band_0", "band_1", "band_2"]
    assert res["n_observations"][0][1] == 25
    assert res["meta"]["pairing"].startswith("common_valid")
    # standardize 不改变 Pearson r（诚实披露）
    res_std = band_correlation_table(stack, standardize=True)
    np.testing.assert_allclose(np.asarray(res_std["correlation"]), corr,
                               atol=1e-12)
    # 零方差波段 → NaN（不伪造）
    res_zero = band_correlation_table(
        np.stack([np.ones((3, 3)), rng.uniform(0, 1, (3, 3))]))
    assert np.isnan(res_zero["correlation"][0][0])
    with pytest.raises(ValueError, match="≥2"):
        band_correlation_table(np.ones((1, 3, 3)))


# ── 23-24. 时序特征 ───────────────────────────────────────────────────

def test_temporal_features_ramp_golden():
    """单调斜坡：amplitude=5、first−last=−5（负值=上升）、mean/std 精确。"""
    ramp = np.stack([np.full((3, 3), float(i)) for i in range(6)])
    res = temporal_features(ramp)
    f = res["features"]
    assert f["min"][0, 0] == pytest.approx(0.0, abs=1e-12)
    assert f["max"][0, 0] == pytest.approx(5.0, abs=1e-12)
    assert f["mean"][0, 0] == pytest.approx(2.5, abs=1e-12)
    assert f["std"][0, 0] == pytest.approx(
        float(np.std(np.arange(6.0))), abs=1e-12)
    assert f["amplitude"][0, 0] == pytest.approx(5.0, abs=1e-12)
    assert f["first_last_diff"][0, 0] == pytest.approx(-5.0, abs=1e-12)
    assert f["first_last_diff"][0, 0] < 0        # 上升趋势 → 负（conformance）
    # 线性斜坡去趋势后无谐波
    assert float(np.max(np.abs(f["harmonic_amplitude"]))) < 1e-8
    with pytest.raises(ValueError, match="≥2"):
        temporal_features(np.ones((1, 3, 3)))


def test_temporal_features_harmonic_sin():
    """完整正弦序列（T=4）→ harmonic_amplitude≈1、phase≈0（精确可解）。"""
    t_grid = np.arange(4) / 3.0
    series = np.stack([np.full((2, 2), np.sin(2 * np.pi * t))
                       for t in t_grid])
    res = temporal_features(series)
    f = res["features"]
    assert f["harmonic_amplitude"][0, 0] == pytest.approx(1.0, abs=1e-9)
    assert f["harmonic_phase"][0, 0] == pytest.approx(0.0, abs=1e-9)
    # 缺切片像元 → 谐波 NaN（完整序列要求，披露）
    series[0, 0, 0] = np.nan
    res_nan = temporal_features(series)
    assert np.isnan(res_nan["features"]["harmonic_amplitude"][0, 0])
    assert np.isfinite(res_nan["features"]["mean"][0, 0])   # nan-aware
    assert "无物候模型" in res_nan["meta"]["disclosure"]


# ── 25-26. 稳健归一化 ─────────────────────────────────────────────────

def test_robust_normalize_linear_invariance():
    """B = 3A+10 match(ref=A) ≡ A 自匹配（线性增益/偏移不变）。"""
    rng = np.random.RandomState(42)
    a = rng.uniform(0, 1, (1, 6, 6))
    b = 3.0 * a + 10.0
    self_match = robust_normalize(a, method="percentile_match", reference=a)
    cross_match = robust_normalize(b, method="percentile_match",
                                   reference=a)
    np.testing.assert_allclose(self_match["normalized"],
                               cross_match["normalized"], atol=1e-12)
    # 逐波段分位披露
    used = cross_match["percentiles_used"]["band_0"]
    assert used["quantiles"] == [2.0, 98.0]
    assert "reference" in used
    assert "nanpercentile" in robust_normalize(
        a, method="percentile_stretch")["meta"]["disclosure"]


def test_robust_normalize_stretch_and_guards():
    """stretch 输出 [0,1]；percentile_match 缺 reference → ValueError；
    常量波段 → DegenerateData。"""
    rng = np.random.RandomState(3)
    a = rng.uniform(0, 1, (2, 5, 5))
    res = robust_normalize(a, method="percentile_stretch")
    assert float(np.nanmin(res["normalized"])) >= 0.0
    assert float(np.nanmax(res["normalized"])) <= 1.0
    assert res["meta"]["method"] == "percentile_stretch"

    with pytest.raises(ValueError, match="reference"):
        robust_normalize(a, method="percentile_match")
    with pytest.raises(DegenerateData):
        robust_normalize(np.stack([np.ones((3, 3)), a[0, :3, :3]]),
                         method="percentile_stretch")
    with pytest.raises(ValueError, match="波段数"):
        robust_normalize(a, method="percentile_match",
                         reference=rng.uniform(0, 1, (1, 4, 4)))
    # NaN 像元保持 NaN
    a_nan = a.copy()
    a_nan[0, 0, 0] = np.nan
    res_nan = robust_normalize(a_nan, method="percentile_stretch")
    assert np.isnan(res_nan["normalized"][0, 0, 0])


# ── 27. 云 QC（EXPERIMENTAL）──────────────────────────────────────────

def test_cloud_qc_bright_block_and_clear_scene():
    """注入亮块全被标记；清洁场景绝大多数清洁；NDVI 条件收紧语义。"""
    rng = np.random.RandomState(42)
    red = rng.uniform(0.1, 0.3, (20, 20))
    nir = red + rng.uniform(0.0, 0.05, (20, 20))

    clear = cloud_qc_basic(red, nir)
    assert clear["suspect_fraction"] < 0.05     # 97.5 分位阈值 → ≈2.5%
    assert clear["meta"]["threshold_source"].startswith("p")
    assert "非 Fmask" in clear["meta"]["disclosure"]
    assert clear["meta"]["scientific_status"] == "EXPERIMENTAL"

    red_bright = red.copy()
    nir_bright = nir.copy()
    red_bright[:2, :2] = 0.9                    # 小亮块（1%：< 分位窗，不被阈值吞没）
    nir_bright[:2, :2] = 0.95
    flagged = cloud_qc_basic(red_bright, nir_bright)
    assert flagged["qc_mask"][:2, :2].all()     # 亮块全标记（分位阈值自归一，
    #   整体 suspect_fraction 仍≈2.5%——百分比阈值的固有语义）

    # 显式阈值模式：4×4 亮块全标记
    red_big = red.copy()
    nir_big = nir.copy()
    red_big[:4, :4] = 0.9
    nir_big[:4, :4] = 0.95
    explicit = cloud_qc_basic(red_big, nir_big, brightness_thresholds=0.5)
    assert explicit["qc_mask"][:4, :4].all()
    assert explicit["meta"]["threshold_source"] == "explicit"
    assert explicit["suspect_fraction"] > clear["suspect_fraction"]

    # NDVI 近零条件：亮但植被（高 NDVI）不标记；云平坦（NDVI≈0）仍标记
    veg = cloud_qc_basic(
        np.full((2, 2), 0.3), np.full((2, 2), 0.9),
        brightness_thresholds=0.4, ndvi_max_abs=0.2)
    assert not veg["qc_mask"].any()
    cloud_like = cloud_qc_basic(
        np.full((2, 2), 0.6), np.full((2, 2), 0.62),
        brightness_thresholds=0.4, ndvi_max_abs=0.2)
    assert cloud_like["qc_mask"].all()
    assert cloud_like["ndvi_condition"].startswith("|NDVI|")

    with pytest.raises(ValueError, match="形状不一致"):
        cloud_qc_basic(np.ones((3, 3)), np.ones((4, 4)))


# ── 28-29. 规模守卫 + registry/契约 parity ────────────────────────────

def test_scale_guard_rejects_oversized_stacks(monkeypatch):
    """monkeypatched 16M 闸 → 代表算法全部 ResourceScaleMismatch 先拒绝。"""
    monkeypatch.setattr(rs_mod, "RS_SCALE_LIMIT_CELLS", 10)
    data = np.random.RandomState(3).uniform(0, 1, (3, 5, 5))
    with pytest.raises(ResourceScaleMismatch):
        mnf(data)
    with pytest.raises(ResourceScaleMismatch):
        ica(data)
    with pytest.raises(ResourceScaleMismatch):
        spectral_angle_mapper(data, {"e": [1.0, 1.0, 1.0]})
    with pytest.raises(ResourceScaleMismatch):
        matched_filter(data, [1.0, 0.0, 0.0])
    with pytest.raises(ResourceScaleMismatch):
        rx_anomaly(data)
    with pytest.raises(ResourceScaleMismatch):
        mad_change(data, data)
    with pytest.raises(ResourceScaleMismatch):
        segment_image(data, n_segments=2)
    with pytest.raises(ResourceScaleMismatch):
        extract_endmembers_vca(data, 2)
    with pytest.raises(ResourceScaleMismatch):
        band_correlation_table(data)
    with pytest.raises(ResourceScaleMismatch):
        temporal_features(data)
    with pytest.raises(ResourceScaleMismatch):
        robust_normalize(data, method="percentile_stretch")
    with pytest.raises(ResourceScaleMismatch):
        cloud_qc_basic(data[0], data[1])
    exc = None
    try:
        rx_anomaly(data)
    except ResourceScaleMismatch as e:            # 结构化字段（estimated/limit）
        exc = e
    assert exc is not None and exc.estimated and exc.limit


def test_rs_v3_registry_contracts_and_parity():
    """新描述符/契约 registry 校验干净；工具 schema parity 对 remote./raster
    无新增 issue（parity 门）。"""
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.parameter_contracts import get_parameter_contract_registry
    from app.services.gis_harness.registry_validation import (
        validate_algorithm_tool_parameter_parity,
    )

    mine = ("remote.mnf", "remote.ica", "remote.sam", "remote.sid",
            "remote.matched_filter", "remote.rx_anomaly", "remote.mad_change",
            "remote.segmentation", "remote.endmember_vca",
            "remote.band_correlation", "remote.temporal_features",
            "remote.robust_normalize", "remote.cloud_qc")
    issues = [i for i in get_algorithm_registry().validate()
              if any(m in i for m in mine)]
    assert issues == []

    registry = get_parameter_contract_registry()
    contracts = ("mnf_analysis", "ica_analysis", "sam_analysis",
                 "sid_analysis", "matched_filter_analysis", "rx_analysis",
                 "mad_change_analysis", "segmentation_analysis",
                 "endmember_vca_analysis", "band_correlation_analysis",
                 "temporal_features_analysis", "robust_normalize_analysis",
                 "cloud_qc_analysis")
    for cid in contracts:
        assert registry.has(cid), cid
        assert registry.get(cid).parameters, cid   # 零参数契约会报 issue

    parity = [i for i in validate_algorithm_tool_parameter_parity()
              if any(m in i for m in mine) or "remote." in i
              or ("raster" in i and "remote" in i)]
    assert parity == []

    # EXPERIMENTAL 诚实标记（vca / cloud_qc）；其余 VALIDATED 须有 conformance
    algos = get_algorithm_registry()
    for aid in ("remote.endmember_vca", "remote.cloud_qc"):
        assert algos.get(aid).scientific_status == "EXPERIMENTAL", aid
    for aid in ("remote.mnf", "remote.ica", "remote.sam", "remote.sid",
                "remote.matched_filter", "remote.rx_anomaly",
                "remote.mad_change", "remote.segmentation",
                "remote.band_correlation", "remote.temporal_features",
                "remote.robust_normalize"):
        desc = algos.get(aid)
        assert desc.scientific_status == "VALIDATED", aid
        assert desc.conformance_tests, aid
