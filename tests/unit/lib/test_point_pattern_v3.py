"""点格局 Foundation V3 测试：时空 K / Mantel / 双变量 g12 / G-F 边缘校正。

全部确定性（固定种子 42 置换；fixture 生成种子固定）；无 wall-clock 断言。
"""
import numpy as np
import pytest

from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    ResourceScaleMismatch,
    UnsupportedMethod,
)
from app.lib.geo_analysis.point_pattern import (
    cross_pair_correlation,
    g_f_j_functions,
    mantel_test,
    space_time_k,
)


def _clustered_space_time_fixture():
    """3 个空间簇 × 3 个时间窗，簇-窗绑定（时空聚集）。"""
    rng = np.random.default_rng(7)
    centers = np.array([[0.0, 0.0], [1000.0, 0.0], [500.0, 800.0]])
    windows = [(0, 200), (3000, 3200), (6000, 6200)]
    xy, times = [], []
    for c, (t0, t1) in zip(centers, windows):
        k = 40
        xy.append(c + rng.normal(0, 60, (k, 2)))
        times.append(rng.uniform(t0, t1, k))
    return np.vstack(xy), np.concatenate(times)


def test_space_time_k_clusters_and_shuffled():
    xy, times = _clustered_space_time_fixture()
    res = space_time_k(xy, times, permutations=99)
    assert res["p_value"] < 0.05
    assert res["sup_exceedance"] > 0
    # 网格形状与参考曲线键完整
    assert len(res["K"]) == 8 and len(res["K"][0]) == 8
    assert len(res["reference_independent"]) == 8
    # 时间标签洗牌（确定性置换）后应不再显著
    shuffled = times[np.random.default_rng(42).permutation(len(times))]
    res2 = space_time_k(xy, shuffled, permutations=99)
    assert res2["p_value"] > 0.05
    # 独立参考矩阵 = πr²·2t（手算锚点；输出为 4/6 位舍入值 → 相对容差）
    r = res["r"][0]
    t = res["t"][0]
    rel = abs(res["reference_independent"][0][0]
              - np.pi * r * r * 2 * t) / (np.pi * r * r * 2 * t)
    assert rel < 1e-4


def test_space_time_k_determinism_and_guards():
    xy, times = _clustered_space_time_fixture()
    a = space_time_k(xy, times, permutations=49)
    b = space_time_k(xy, times, permutations=49)
    assert a == b  # 同种子重放逐位一致
    # 描述模式（permutations=0）无 p 值
    desc = space_time_k(xy, times, permutations=0)
    assert "p_value" not in desc
    # 非有限时间行剔除并披露
    times_bad = times.copy()
    times_bad[:3] = np.nan
    res = space_time_k(xy, times_bad, permutations=0)
    assert res["n_time_dropped"] == 3
    # 样本不足 / 时间退化 / 步数越界 → 类型化错误
    with pytest.raises(InsufficientSamples):
        space_time_k(xy[:5], times[:5])
    with pytest.raises(DegenerateData):
        space_time_k(xy, np.full(len(xy), 5.0))
    with pytest.raises(ValueError):
        space_time_k(xy, times, n_steps_r=2)
    with pytest.raises(ValueError):
        space_time_k(xy, times, permutations=10_000)


def test_mantel_association_and_shuffle():
    xy, times = _clustered_space_time_fixture()
    res = mantel_test(xy, times, permutations=199)
    assert res["mantel_r"] > 0.5
    assert res["p_value"] < 0.05
    assert res["alternative"] == "greater"
    # 洗牌后关联消失
    shuffled = times[np.random.default_rng(42).permutation(len(times))]
    res2 = mantel_test(xy, shuffled, permutations=199)
    assert abs(res2["mantel_r"]) < 0.2
    assert res2["p_value"] > 0.05
    # two-sided 选项可运行且 p ≥ 单侧
    res3 = mantel_test(xy, times, permutations=199, alternative="two-sided")
    assert res3["p_value"] >= res["p_value"] - 1e-9


def test_mantel_determinism_and_guards():
    xy, times = _clustered_space_time_fixture()
    assert mantel_test(xy, times, permutations=99) == mantel_test(
        xy, times, permutations=99)
    with pytest.raises(InsufficientSamples):
        mantel_test(xy[:4], times[:4])
    with pytest.raises(ResourceScaleMismatch):
        # 密集矩阵上限：>2000 点结构化拒绝
        big_xy = np.zeros((2001, 2))
        big_t = np.arange(2001.0)
        mantel_test(big_xy, big_t, permutations=0)
    with pytest.raises(ValueError):
        mantel_test(xy, times, alternative="bogus")
    with pytest.raises(ValueError):
        mantel_test(xy, times, permutations=5000)
    with pytest.raises(DegenerateData):
        mantel_test(xy, np.full(len(xy), 3.0))


def test_cross_pcf_runs_and_labels():
    xy, _ = _clustered_space_time_fixture()
    rng = np.random.default_rng(11)
    types = np.array(["a", "b"])[rng.integers(0, 2, len(xy))]
    res = cross_pair_correlation(xy, types, permutations=49)
    assert res["type_values"] == ["a", "b"]
    assert len(res["g12"]) == 10 and len(res["K12"]) == 10
    assert res["bandwidth_auto"] is True
    assert 0 <= res["p_value"] <= 1
    # 显式带宽生效（输出为 4 位舍入值 → 容差 1e-3）
    r_max = res["r_max"]
    res2 = cross_pair_correlation(xy, types, bandwidth=r_max / 5, permutations=0)
    assert res2["bandwidth_auto"] is False
    assert abs(res2["bandwidth"] - r_max / 5) < 1e-3


def test_cross_pcf_determinism_and_guards():
    xy, _ = _clustered_space_time_fixture()
    types = np.array(["a", "b"] * (len(xy) // 2))
    assert cross_pair_correlation(xy, types, permutations=49) == \
        cross_pair_correlation(xy, types, permutations=49)
    with pytest.raises(UnsupportedMethod):
        cross_pair_correlation(xy, np.array(["a"] * len(xy)))
    with pytest.raises(InsufficientSamples):
        minority = np.array(["a"] * 4 + ["b"] * (len(xy) - 4))
        cross_pair_correlation(xy, minority)
    with pytest.raises(InsufficientSamples):
        cross_pair_correlation(xy[:6], types[:6])
    with pytest.raises(ValueError):
        cross_pair_correlation(xy, types, bandwidth=1e9)
    with pytest.raises(ValueError):
        cross_pair_correlation(xy, types, permutations=10_000)


def test_gfj_edge_corrections():
    rng = np.random.default_rng(3)
    # 贴边数据：isotropic 加权必须激活（与 none 产生差异）
    n = 60
    edge_xy = np.column_stack([rng.uniform(0, 20, n), rng.uniform(0, 1000, n)])
    win = (0, 0, 1000, 1000)
    res_none = g_f_j_functions(edge_xy, window=win)
    res_iso = g_f_j_functions(edge_xy, window=win, edge_correction="isotropic")
    diff = max(abs(a - b) for a, b in zip(res_none["G"], res_iso["G"]))
    assert diff > 1e-6
    # 开阔窗内 border 校正可运行且 CDF 单调非降
    xy = rng.uniform(0, 1000, (120, 2))
    big_win = (-200, -200, 1200, 1200)
    res_b = g_f_j_functions(xy, window=big_win, edge_correction="border")
    g_vals = res_b["G"]
    assert all(g_vals[i] <= g_vals[i + 1] + 1e-12 for i in range(len(g_vals) - 1))
    assert 0.0 <= g_vals[0] <= 1.0
    # 输出披露 edge_correction 语义
    assert res_b["edge_correction"].startswith("border")
    assert res_iso["edge_correction"].startswith("isotropic")
    assert res_none["edge_correction"].startswith("none")
    # 缺省（none）与显式 edge_correction="none" 输出逐位一致（回归锚点）
    assert res_none["G"] == g_f_j_functions(
        edge_xy, window=win, edge_correction="none")["G"]
    # 数据贴窗边时 border 诚实拒绝
    with pytest.raises(DegenerateData):
        g_f_j_functions(edge_xy, window=(0, 0, 1000, 1000), edge_correction="border")
    # 非法校正名
    with pytest.raises(ValueError):
        g_f_j_functions(xy, edge_correction="bogus")


def test_gfj_edge_correction_determinism():
    rng = np.random.default_rng(5)
    xy = rng.uniform(0, 800, (80, 2))
    win = (-100, -100, 900, 900)
    assert g_f_j_functions(xy, window=win, edge_correction="isotropic",
                           envelopes=19) == \
        g_f_j_functions(xy, window=win, edge_correction="isotropic", envelopes=19)
    assert g_f_j_functions(xy, window=win, edge_correction="border",
                           envelopes=19)["G_p_value"] == \
        g_f_j_functions(xy, window=win, edge_correction="border",
                        envelopes=19)["G_p_value"]
