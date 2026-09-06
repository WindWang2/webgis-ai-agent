"""SAR 斑点滤波 / 时序扩展 V2 conformance 测试（Foundation V2 · A6）。

覆盖（app/lib/geo_analysis/{sar_filter,sar_temporal}.py）：

- Lee 1980：窗口内手算精确一致（mean/var/k 全程独立复算）；
- 合成斑点（gamma 4 视，seed 42）：均匀块 std(after) < std(before)；
- Refined Lee（Lee 1981/Lopes 1990 近似）：纯阶梯边缘完全保持
  （MSE 代理子窗选择），lee 则跨边缘平滑（边界列锐度对比）；
- Frost 1982：常数场 + 显式 ENL → 恒等映射（cv=0 ⇒ k=0）golden；
- 类型化错误：负值/DB 输入（UnsupportedMethod）、常数场 ENL 估计
  （DegenerateData）、窗口/阻尼参数（ValueError）、规模闸
  （ResourceScaleMismatch，monkeypatched cap）；
- ENL 矩估计（ENL=mean²/var）与 enl_source 披露；
- nodata 感知：洞 → 周边有限值，全无效块 → NaN；
- sar_temporal 扩展（Foundation V2 #6 additive）：CV golden（|mean|≤eps
  → NaN）、percentiles == np.nanpercentile 参考（逐位）、默认参数输出
  与 v1 形状逐键一致、temporal_composite median == np.nanmedian（1e-12）、
  percentile 缺参 → MissingRequiredField、栈深闸；
- 工具 schema parity（新增工具在注册表 schema 中带必需参数）、证据块
  挂载（scientific_evidence + backend_selection 诊断）、registry
  validate()/parity() 为空。
"""
import asyncio
import math

import numpy as np
import pytest

from app.lib.gis.scientific_errors import (
    DegenerateData,
    MissingRequiredField,
    ResourceScaleMismatch,
    UnsupportedMethod,
)
from app.lib.geo_analysis import sar_filter
from app.lib.geo_analysis.sar_filter import speckle_filter
from app.lib.geo_analysis.sar_temporal import (
    temporal_composite,
    temporal_stack_statistics,
)

pytestmark = pytest.mark.unit


# ── 1. Lee 1980 ───────────────────────────────────────────────────────

def _fixed_image():
    """5×5 固定强度图（无随机成分；窗口统计手工可复算）。"""
    return np.array([
        [12.0, 18.0, 25.0, 31.0, 40.0],
        [15.0, 22.0, 28.0, 36.0, 44.0],
        [20.0, 27.0, 33.0, 42.0, 50.0],
        [26.0, 34.0, 41.0, 48.0, 57.0],
        [30.0, 38.0, 47.0, 55.0, 63.0],
    ])


def test_lee_hand_window_exact():
    img = _fixed_image()
    enl = 4.0
    res = speckle_filter(img, "lee", window=3, enl=enl)
    out = res["array"]

    # 独立参考：内点 (2,2) 的 3×3 窗口统计 + Lee 公式（纯循环复算）。
    win = img[1:4, 1:4]
    m = win.mean()
    v = win.var()                      # 总体方差（ddof=0）
    k = v / (v + m * m / enl)
    expected = m + k * (img[2, 2] - m)
    assert out[2, 2] == pytest.approx(expected, abs=1e-12)

    # 公式与披露在场；显式 ENL 不做整图估计
    assert res["enl_source"] == "explicit"
    assert res["meta"]["formula"].startswith("R = m + k·(x−m)")
    assert "Lee 1980" in res["meta"]["formula"]

    # 确定性
    again = speckle_filter(img, "lee", window=3, enl=enl)
    np.testing.assert_array_equal(out, again["array"])


def test_lee_noise_reduction_on_homogeneous_patch():
    rng = np.random.RandomState(42)
    # gamma(k=4, θ=25)：mean=100、CV²=1/4 —— 充分发育 4 视斑点模型
    noisy = rng.gamma(shape=4.0, scale=25.0, size=(32, 32))
    res = speckle_filter(noisy, "lee", window=5, enl=4.0)
    assert res["array"].std() < noisy.std()
    # 均值近似保持（斑点均值 1 → 滤波不引入系统偏移 > 5%）
    assert abs(res["array"].mean() - noisy.mean()) < 0.05 * noisy.mean()

    for method in ("refined_lee", "frost"):
        r = speckle_filter(noisy, method, window=5, enl=4.0)
        assert r["array"].std() < noisy.std(), method


# ── 2. Refined Lee（边缘方向子窗）────────────────────────────────────

def test_refined_lee_preserves_step_edge_better_than_lee():
    step = np.concatenate(
        [np.full((12, 6), 50.0), np.full((12, 6), 100.0)], axis=1)
    rl = speckle_filter(step, "refined_lee", window=5, enl=1.0)["array"]
    le = speckle_filter(step, "lee", window=5, enl=1.0)["array"]

    # golden：纯阶梯下 refined_lee 完全保持（每像元选中所在侧同质子窗，
    # var=0 ⇒ k=0 ⇒ 输出 = 侧均值）；lee 跨边缘平滑（不再等于原图）。
    np.testing.assert_allclose(rl, step, atol=1e-9)
    assert not np.allclose(le, step, atol=1e-6)

    # 边界列锐度（mean-gradient 属性）：refined_lee 严格更锐
    g_rl = np.abs(np.diff(rl, axis=1))[:, 5].mean()
    g_le = np.abs(np.diff(le, axis=1))[:, 5].mean()
    assert g_rl > g_le + 10.0

    # 近似披露在场（非完整 MAP 变体）
    disc = speckle_filter(step, "refined_lee")["meta"]["disclosure"]
    assert "MAP" in disc or "近似" in disc


# ── 3. Frost 1982 ─────────────────────────────────────────────────────

def test_frost_constant_identity_and_damping_guard():
    img = np.full((8, 8), 100.0)
    # 常数场（cv=0 ⇒ k=0 ⇒ w=1）+ 显式 ENL → 恒等映射 golden
    res = speckle_filter(img, "frost", window=3, enl=4.0)
    np.testing.assert_allclose(res["array"], img, atol=1e-12)
    assert res["meta"]["formula"].startswith("R = Σ exp(−k·d)·x")
    assert res["meta"]["damping"] == 1.0

    # 阻尼参数越界 → ValueError；有效范围照常
    with pytest.raises(ValueError, match="damping"):
        speckle_filter(img, "frost", window=3, enl=4.0, damping=0.4)
    with pytest.raises(ValueError, match="damping"):
        speckle_filter(img, "frost", window=3, enl=4.0, damping=6.0)
    rng = np.random.RandomState(7)
    noisy = rng.gamma(shape=4.0, scale=25.0, size=(24, 24))
    d2 = speckle_filter(noisy, "frost", window=5, enl=4.0, damping=2.0)
    assert d2["array"].std() < noisy.std()


# ── 4. ENL 估计 / 守卫 / nodata ──────────────────────────────────────

def test_speckle_scale_guard_enl_estimation_and_errors(monkeypatch):
    monkeypatch.setattr(sar_filter, "SPECKLE_SCALE_LIMIT_PIXELS", 9)
    with pytest.raises(ResourceScaleMismatch) as exc_info:
        speckle_filter(np.zeros((4, 4)))
    err = exc_info.value
    assert err.estimated is not None and "4×4" in err.estimated
    assert err.limit is not None
    monkeypatch.undo()

    # ENL 矩估计：gamma 4 视 → ENL ≈ 4，enl_source="estimated"
    rng = np.random.RandomState(42)
    noisy = rng.gamma(shape=4.0, scale=25.0, size=(32, 32))
    res = speckle_filter(noisy, "lee", window=3)
    assert res["enl_source"] == "estimated"
    assert 3.0 < res["enl"] < 5.5
    assert "矩估计" in res["meta"]["disclosure"]

    # 负值 + 缺省 ENL → UnsupportedMethod（斑点滤波假定线性强度）
    neg = np.array([[1.0, -2.0], [3.0, 4.0]])
    with pytest.raises(UnsupportedMethod) as neg_err:
        speckle_filter(neg, "lee")
    assert "线性强度" in str(neg_err.value)
    # 负值 + 显式 ENL：估计路径被跳过（显式 ENL 由调用方负责量纲）
    ok = speckle_filter(neg + 10.0, "lee", enl=4.0)
    assert np.isfinite(ok["array"]).all()

    # 常数场 + 缺省 ENL → DegenerateData（mean²/var 无定义）
    with pytest.raises(DegenerateData):
        speckle_filter(np.full((6, 6), 3.0))

    # 参数守卫
    with pytest.raises(ValueError, match="window"):
        speckle_filter(_fixed_image(), "lee", window=4)
    with pytest.raises(ValueError, match="unsupported speckle filter"):
        speckle_filter(_fixed_image(), "kuwahara")


def test_speckle_nodata_awareness():
    img = _fixed_image()
    masked = img.copy()
    masked[2, 2] = np.nan
    res = speckle_filter(masked, "lee", window=3, enl=4.0)
    # 无效像元本体 → NaN（不虚构数据）；周边有效像元 → 有限
    # （其窗口统计剔除 NaN 像元）
    assert np.isnan(res["array"][2, 2])
    assert np.isfinite(res["array"][1, 1])
    assert np.isfinite(res["array"][2, 3])
    # 全无效块 → 窗口统计无有效像元 → NaN（不伪造）
    masked2 = img.copy()
    masked2[1:4, 1:4] = np.nan
    res2 = speckle_filter(masked2, "lee", window=3, enl=4.0)
    assert np.isnan(res2["array"][2, 2])
    assert np.isfinite(res2["array"][0, 0])
    # 全 nodata → DegenerateData（ENL 不可估）
    with pytest.raises(DegenerateData):
        speckle_filter(np.full((4, 4), np.nan))
    # 披露在场
    assert "nodata" in res["meta"]["disclosure"]


# ── 5. sar_temporal 扩展（Foundation V2 #6 additive）─────────────────

def test_sar_stats_cv_and_percentiles():
    stack = np.array([
        [[2.0, -1.0], [5.0, 5.0]],
        [[4.0, 1.0], [5.0, 5.0]],
        [[6.0, -1.0], [5.0, 5.0]],
        [[8.0, 1.0], [5.0, 5.0]],
    ])
    res = temporal_stack_statistics(
        stack, product="mean", include_cv=True, percentiles=(10, 50, 90))

    # CV golden：像素 (0,0) 序列 [2,4,6,8] → mean=5、总体 std=√5 →
    # cv = √5/5；像素 (0,1) 序列 [−1,1,−1,1] → mean=0 → |mean|≤eps → NaN；
    # 常数像素 (1,·) → std=0 → cv=0。
    assert res["cv"][0, 0] == pytest.approx(math.sqrt(5.0) / 5.0, abs=1e-12)
    assert np.isnan(res["cv"][0, 1])
    assert res["cv"][1, 0] == 0.0
    assert "CV = std/mean" in res["meta"]["cv_convention"]
    assert "NaN" in res["meta"]["cv_convention"]

    # percentiles == np.nanpercentile 参考（同输入同调用，逐位一致）
    filled = np.where(np.isfinite(stack), stack, np.nan)
    for p, key in ((10.0, "p10"), (50.0, "p50"), (90.0, "p90")):
        ref = np.asarray(np.nanpercentile(filled, p, axis=0))
        np.testing.assert_array_equal(res["percentiles"][key], ref)
    assert res["meta"]["percentiles_requested"] == [10.0, 50.0, 90.0]

    # 参数守卫：>5 个值 / 越界分位数
    with pytest.raises(ValueError, match="最多 5"):
        temporal_stack_statistics(stack, percentiles=(1, 2, 3, 4, 5, 6))
    with pytest.raises(ValueError, match="0, 100"):
        temporal_stack_statistics(stack, percentiles=(150.0,))


def test_sar_stats_defaults_preserve_v1_shape():
    stack = np.array([
        [[1.0, 2.0], [3.0, 4.0]],
        [[3.0, 4.0], [5.0, 6.0]],
        [[5.0, 6.0], [7.0, 8.0]],
        [[7.0, 8.0], [9.0, 10.0]],
    ])
    res = temporal_stack_statistics(stack, product="std")
    # v1 输出形状逐键保持（无 cv / percentiles / 附加 meta 键）
    assert set(res.keys()) == {"array", "product", "meta"}
    assert set(res["meta"].keys()) == {
        "product", "time_slices", "pixels_all_slices_valid",
        "pixels_partially_valid", "pixels_no_valid_slice",
        "std_convention", "disclosure"}
    # std 总体（ddof=0）golden 不变：序列 [1,3,5,7] → √5
    assert res["array"][0, 0] == pytest.approx(math.sqrt(5.0), abs=1e-12)
    with pytest.raises(ValueError, match="unsupported SAR product"):
        temporal_stack_statistics(stack, product="median")


def test_sar_temporal_composite_median_exact():
    stack = np.array([
        [[1.0, np.nan], [3.0, 4.0]],
        [[2.0, 10.0], [-9999.0, 6.0]],
        [[9.0, 30.0], [7.0, 8.0]],
    ])
    res = temporal_composite(stack, method="median", nodata=-9999.0)
    filled = stack.copy()
    filled[filled == -9999.0] = np.nan
    ref = np.nanmedian(filled, axis=0)
    np.testing.assert_allclose(res["array"], ref, rtol=0, atol=1e-12)
    assert res["array"][0, 1] == pytest.approx(20.0, abs=1e-12)
    assert res["method"] == "median"
    # mean 合成 == temporal_stack_statistics mean（同语义复用同一 nan 通道）
    mean_res = temporal_composite(stack, method="mean", nodata=-9999.0)
    stat_mean = temporal_stack_statistics(stack, product="mean", nodata=-9999.0)
    np.testing.assert_allclose(mean_res["array"], stat_mean["array"], atol=1e-12)


def test_sar_temporal_composite_methods_and_guards():
    stack = np.arange(48, dtype=float).reshape(3, 4, 4)

    with pytest.raises(MissingRequiredField):
        temporal_composite(stack, method="percentile")
    with pytest.raises(ValueError, match="unsupported composite method"):
        temporal_composite(stack, method="max_ndvi")
    with pytest.raises(ValueError, match="0, 100"):
        temporal_composite(stack, method="percentile", percentile=101.0)

    p75 = temporal_composite(stack, method="percentile", percentile=75.0)
    ref = np.nanpercentile(stack, 75.0, axis=0)
    np.testing.assert_allclose(p75["array"], ref, rtol=0, atol=1e-12)

    with pytest.raises(ResourceScaleMismatch):
        temporal_composite(np.zeros((25, 2, 2)))


# ── 6. 工具 schema / 证据块 / 注册表门 ───────────────────────────────

def _make_tool_registry():
    from app.tools.registry import ToolRegistry
    from app.tools.remote_sensing import register_rs_tools

    registry = ToolRegistry()
    register_rs_tools(registry)
    return registry


def _schema_of(registry, name):
    for s in registry.get_schemas():
        if s["function"]["name"] == name:
            return s["function"]
    return None


def test_sar_tools_schema_and_evidence():
    registry = _make_tool_registry()
    new_tools = {
        "sar_speckle_filter", "sar_calibrate", "sar_glcm_texture",
        "raster_pca", "tasseled_cap", "sar_temporal_composite",
    }
    listed = set(registry.list_tools())
    assert new_tools <= listed

    # parity 面：契约必填参数必须出现在工具 schema properties
    fn = _schema_of(registry, "sar_calibrate")
    assert "calibration_constant" in fn["parameters"]["properties"]
    assert "calibration_constant" in fn["parameters"]["required"]
    fn = _schema_of(registry, "tasseled_cap")
    assert "sensor" in fn["parameters"]["properties"]
    assert "sensor" in fn["parameters"]["required"]

    # 证据块挂载 + backend_selection 诊断（默认路径诚实记录）
    img = np.random.RandomState(3).gamma(4.0, 25.0, size=(6, 6)).tolist()
    speckle_fn = registry._tools["sar_speckle_filter"]
    payload = asyncio.run(speckle_fn(img, filter="lee", window="3"))
    assert payload["success"] is True
    ev = payload["scientific_evidence"]
    assert ev["algorithm"] == "sar.speckle_filter"
    assert ev["assumptions"] and ev["limitations"]
    diag_names = {d["name"] for d in ev["diagnostics"]}
    assert "backend_selection" in diag_names

    # 定标缺必需常数 → 类型化错误（科学层 MissingRequiredField ⊂ ValueError；
    # 工具 schema 层缺失由 pydantic required 拦截，见 parity 断言）
    from app.lib.geo_analysis.sar_calibration import calibrate_sar

    with pytest.raises(MissingRequiredField):
        calibrate_sar(np.asarray(img), calibration_constant=None)

    # 合成工具证据块
    stack = [[[1.0, 2.0], [3.0, 4.0]], [[3.0, 4.0], [5.0, 6.0]]]
    comp_fn = registry._tools["sar_temporal_composite"]
    comp = asyncio.run(comp_fn(stack, method="median"))
    assert comp["scientific_evidence"]["algorithm"] == "sar.temporal_composite"


def test_a6_registry_validate_and_parity():
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.capability_registry import get_capability_registry
    from app.services.gis_harness.registry_validation import (
        validate_algorithm_tool_parameter_parity,
    )

    # A6 域内零 issue（worktree 基线上其他域存在预先存在的 conformance
    # 文件缺失——如 network.*，不在 A6 改动范围，另行报告）。
    my_prefixes = (
        "sar.speckle_filter", "sar.radiometric_calibration",
        "sar.glcm_texture", "sar.temporal", "sar.vh_ratio",
        "sar.log_ratio", "remote.pca", "remote.tasseled_cap",
        "remote.spectral_index", "remote.ndvi", "remote.cva",
        "remote.ratio_change", "capability sar_texture",
        "capability raster_dimensionality_reduction",
        "capability tasseled_cap_transformation",
        "capability sar_speckle_filtering",
        "capability sar_radiometric_calibration",
    )
    issues = get_algorithm_registry().validate()
    mine = [i for i in issues if any(p in i for p in my_prefixes)]
    assert mine == [], mine

    parity_issues = validate_algorithm_tool_parameter_parity()
    assert parity_issues == [], parity_issues

    # planned→native 翻转 + 新能力各 ≥1 native 算法
    reg = get_algorithm_registry()
    caps = get_capability_registry()
    for cap_id in ("sar_speckle_filtering", "sar_radiometric_calibration",
                   "sar_texture", "raster_dimensionality_reduction",
                   "tasseled_cap_transformation"):
        cap = caps.get(cap_id)
        assert cap is not None and cap.status == "native", cap_id
        algos = reg.algorithms_for_capability(cap_id)
        assert algos, cap_id
        assert all(a.runtime_status == "native" for a in algos), cap_id

    for algo_id in ("sar.speckle_filter", "sar.radiometric_calibration",
                    "sar.glcm_texture", "remote.pca", "remote.tasseled_cap",
                    "sar.temporal_composite"):
        algo = reg.get(algo_id)
        assert algo is not None and algo.runtime_status == "native", algo_id
        assert algo.scientific_status == "VALIDATED", algo_id
        assert algo.parameter_contract_ref, algo_id
