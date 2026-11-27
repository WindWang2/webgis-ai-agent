"""设计一致估计 oracle 测试（WP4 数学正确性核心）。

三个独立证据层：
1. **手算 oracle**：2 层小例的全部矩阵/OA/UA/PA/SE 由测试内独立公式
   复算（不 import 被测实现路径的任何中间量），逐项对齐 1e-9；
2. **全查归零**：n_h = N_h ⇒ fpc = 0 ⇒ SE 恰为 0、CI 退化；
3. **独立性交叉核对**：估计器结果 == 用定义直接重算的第二实现
   （tests 内自写的独立 numpy 路径）。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.survey_sampling import estimation as est


def _counts_2strata() -> est.StratifiedCounts:
    """手算 oracle 输入（模块 docstring 的推导用例）：

    strata A(forest, W=0.6, N=600), B(crop, W=0.4, N=400)
    n_A=5 → 参考标签 [A,A,A,B,A]；n_B=4 → [B,B,A,B]
    """
    return est.StratifiedCounts(
        strata=["forest", "crop"],
        ref_classes=["forest", "crop"],
        W=np.array([0.6, 0.4]),
        N=np.array([600.0, 400.0]),
        counts=np.array([[4, 1], [1, 3]], dtype=np.int64),
        n_planned=np.array([5, 4], dtype=np.int64),
        excluded=np.array([0, 0], dtype=np.int64),
    )


def test_hand_computed_oracle() -> None:
    res = est.stratified_estimate(_counts_2strata(), total_area=10000.0)

    # 矩阵（手算：0.48/0.12/0.10/0.30）
    np.testing.assert_allclose(
        res.matrix, [[0.48, 0.12], [0.10, 0.30]], rtol=0, atol=1e-12)
    # 行和 = 层权（strata==地图类的已知行和性质）
    np.testing.assert_allclose(res.matrix.sum(axis=1), [0.6, 0.4], atol=1e-12)
    # OA = 0.78；UA = q = 0.8/0.75；PA = 0.48/0.58 与 0.30/0.42
    assert res.overall_accuracy.estimate == pytest.approx(0.78, abs=1e-12)
    assert res.user_accuracy["forest"].estimate == pytest.approx(0.8, abs=1e-12)
    assert res.user_accuracy["crop"].estimate == pytest.approx(0.75, abs=1e-12)
    assert res.producer_accuracy["forest"].estimate == pytest.approx(
        0.48 / 0.58, abs=1e-12)
    assert res.producer_accuracy["crop"].estimate == pytest.approx(
        0.30 / 0.42, abs=1e-12)
    assert res.status == "supported"

    # ── SE 手算（独立公式，测试内复算）───────────────────────────
    fpcA = 1 - 5 / 600
    fpcB = 1 - 4 / 400
    vA = fpcA * (0.8 * 0.2) / 4          # UA_A 与 OA 的 A 层分量
    vB = fpcB * (0.75 * 0.25) / 3        # UA_B 与 OA 的 B 层分量
    var_oa = 0.6**2 * vA + 0.4**2 * vB
    assert res.overall_accuracy.se == pytest.approx(np.sqrt(var_oa), rel=1e-9)
    assert res.user_accuracy["forest"].se == pytest.approx(np.sqrt(vA), rel=1e-9)
    assert res.user_accuracy["crop"].se == pytest.approx(np.sqrt(vB), rel=1e-9)
    # PA_A：V = (1/B²)[(1−f)²V(A) + f²·Σ_{h≠A}W_h²v_h]，A=W_Aq_A, B=p̂_·A
    f_a = 0.48 / 0.58
    v_rest_b = 0.4**2 * fpcB * (0.25 * 0.75) / 3
    var_pa_a = ((1 - f_a) ** 2 * (0.6**2 * vA) + f_a**2 * v_rest_b) / 0.58**2
    assert res.producer_accuracy["forest"].se == pytest.approx(
        np.sqrt(var_pa_a), rel=1e-9)
    # PA_B 同构
    f_b = 0.30 / 0.42
    v_rest_a = 0.6**2 * fpcA * ((1 / 5) * (4 / 5)) / 4   # 层 A 的 (h,j=colB) 比例 0.2
    var_pa_b = ((1 - f_b) ** 2 * (0.4**2 * vB) + f_b**2 * v_rest_a) / 0.42**2
    assert res.producer_accuracy["crop"].se == pytest.approx(
        np.sqrt(var_pa_b), rel=1e-9)

    # 面积 = 比例 × total_area（10000）
    assert res.area_estimate["forest"].estimate == pytest.approx(5800.0, abs=1e-9)
    assert res.area_estimate["crop"].estimate == pytest.approx(4200.0, abs=1e-9)
    assert res.area_estimate["forest"].se == pytest.approx(
        res.area_fraction["forest"].se * 10000.0, rel=1e-12)


def test_matrix_sample_counts_two_facts() -> None:
    """样本数（事实）与估计比例（推断）并存不混用。"""
    res = est.stratified_estimate(_counts_2strata(), total_area=10000.0)
    np.testing.assert_array_equal(res.sample_counts, [[4, 1], [1, 3]])
    # 序列化：NaN→null、样本数 int、两套事实各自独立输出
    d = res.as_dict()
    assert d["sample_counts"] == [[4, 1], [1, 3]]
    assert d["matrix"][0][0] == pytest.approx(0.48)
    assert d["estimator"] == "stratified_srs_wo_replacement_weighted"


def test_full_census_zero_variance() -> None:
    """全查（n_h = N_h）：fpc=0 ⇒ SE=0、CI 退化；且 == 全量像元混淆矩阵口径。"""
    counts = est.StratifiedCounts(
        strata=["a", "b"],
        ref_classes=["a", "b"],
        W=np.array([0.7, 0.3]),
        N=np.array([70.0, 30.0]),
        counts=np.array([[60, 10], [5, 25]], dtype=np.int64),  # n=N 全查
        n_planned=np.array([70, 30], dtype=np.int64),
        excluded=np.array([0, 0], dtype=np.int64),
    )
    res = est.stratified_estimate(counts, total_area=1000.0)
    assert res.overall_accuracy.estimate == pytest.approx((60 + 25) / 100)
    assert res.overall_accuracy.se == pytest.approx(0.0, abs=1e-15)
    assert res.overall_accuracy.ci_low == pytest.approx(
        res.overall_accuracy.estimate, abs=1e-15)
    assert res.overall_accuracy.ci_high == pytest.approx(
        res.overall_accuracy.estimate, abs=1e-15)
    # 全查 ⇒ 估计 == 直接总体混淆矩阵（modelops 全量口径的对照锚）
    np.testing.assert_allclose(
        res.matrix, [[0.7 * 60 / 70, 0.7 * 10 / 70],
                     [0.3 * 5 / 30, 0.3 * 25 / 30]], atol=1e-12)
    assert res.producer_accuracy["a"].se == pytest.approx(0.0, abs=1e-15)


def test_independent_crosscheck_random_population() -> None:
    """随机总体：估计器 vs 测试内独立第二实现（定义直算）。"""
    rng = np.random.default_rng(20261127)
    strata = ["s1", "s2", "s3"]
    refs = ["s1", "s2", "s3"]
    W = np.array([0.5, 0.3, 0.2])
    N = np.array([500.0, 300.0, 200.0])
    counts = rng.multinomial(20, [0.6, 0.3, 0.1], size=3).astype(np.int64)
    counts[2] = [14, 4, 2]  # 保证各 cell >0（避免零 cell 特例干扰）
    cin = est.StratifiedCounts(
        strata=strata, ref_classes=refs, W=W, N=N, counts=counts,
        n_planned=np.array([20, 20, 20], dtype=np.int64),
        excluded=np.zeros(3, dtype=np.int64))
    res = est.stratified_estimate(cin, total_area=1000.0)

    # 第二实现（独立 numpy 路径，从定义直算）
    n_h = counts.sum(axis=1)
    p_hat = W[:, None] * counts / n_h[:, None]
    oa = sum(p_hat[i, i] for i in range(3))
    var_oa = sum(
        W[h] ** 2 * (1 - n_h[h] / N[h]) * (counts[h, h] / n_h[h])
        * (1 - counts[h, h] / n_h[h]) / (n_h[h] - 1)
        for h in range(3))
    assert res.overall_accuracy.estimate == pytest.approx(oa, abs=1e-12)
    assert res.overall_accuracy.se == pytest.approx(np.sqrt(var_oa), rel=1e-9)
    for j, cname in enumerate(refs):
        col = p_hat[:, j].sum()
        assert res.area_fraction[cname].estimate == pytest.approx(
            col, abs=1e-12)
        pa = p_hat[j, j] / col
        assert res.producer_accuracy[cname].estimate == pytest.approx(
            pa, abs=1e-12)


def test_zero_response_stratum_not_evaluated() -> None:
    """任一层零响应 ⇒ 总体级指标全部 not_evaluated（不伪造总体估计）。"""
    counts = est.StratifiedCounts(
        strata=["a", "b"], ref_classes=["a", "b"],
        W=np.array([0.5, 0.5]), N=np.array([100.0, 100.0]),
        counts=np.array([[8, 2], [0, 0]], dtype=np.int64),
        n_planned=np.array([10, 10], dtype=np.int64),
        excluded=np.array([0, 10], dtype=np.int64),
        excluded_reasons={"not_visited": 10},
    )
    res = est.stratified_estimate(counts, total_area=2000.0)
    assert res.status == "not_evaluated"
    assert not res.overall_accuracy.evaluable
    for mv in res.area_fraction.values():
        assert not mv.evaluable
    assert any("not estimable" in lim for lim in res.limitations)
    # 矩阵事实仍显示可用层（NaN 序列化为 null）
    d = res.as_dict()
    assert d["matrix"][1][0] is None
    assert d["excluded_total"] == 10


def test_n1_stratum_variance_partial() -> None:
    """n_h=1 层：点估计保留、方差分量缺失 → partial + evaluable=false。"""
    counts = est.StratifiedCounts(
        strata=["a", "b"], ref_classes=["a", "b"],
        W=np.array([0.5, 0.5]), N=np.array([100.0, 100.0]),
        counts=np.array([[10, 0], [0, 1]], dtype=np.int64),
        n_planned=np.array([10, 10], dtype=np.int64),
        excluded=np.zeros(2, dtype=np.int64),
    )
    res = est.stratified_estimate(counts, total_area=2000.0)
    assert res.status == "partial"
    assert res.overall_accuracy.estimate == pytest.approx(0.5 * 1.0 + 0.5 * 1.0)
    assert not res.overall_accuracy.evaluable
    assert res.user_accuracy["a"].evaluable          # n=10 可估
    assert not res.user_accuracy["b"].evaluable      # n=1 不可估


def test_all_strata_below_two_raises() -> None:
    """所有层 n_h<2 ⇒ typed 拒绝（无任何可估方差）。"""
    counts = est.StratifiedCounts(
        strata=["a", "b"], ref_classes=["a", "b"],
        W=np.array([0.5, 0.5]), N=np.array([100.0, 100.0]),
        counts=np.array([[1, 0], [0, 1]], dtype=np.int64),
        n_planned=np.array([10, 10], dtype=np.int64),
        excluded=np.zeros(2, dtype=np.int64),
    )
    with pytest.raises(est.EstimationNotEvaluable):
        est.stratified_estimate(counts, total_area=100.0)


def test_class_dictionary_drift_blocked() -> None:
    """层名不在参考字典 ⇒ 类字典漂移 typed 阻断（journey 2 语义）。"""
    with pytest.raises(Exception, match="dictionary drift"):
        est.StratifiedCounts(
            strata=["a", "new_class"], ref_classes=["a", "b"],
            W=np.array([0.5, 0.5]), N=np.array([100.0, 100.0]),
            counts=np.array([[5, 5], [5, 5]], dtype=np.int64),
            n_planned=np.array([10, 10], dtype=np.int64),
            excluded=np.zeros(2, dtype=np.int64),
        )


def test_ci_out_of_bounds_disclosed_not_silently_clipped() -> None:
    """稀有类小 n：CI 越界 [0,1] ⇒ 原始区间保留 + 裁剪副本披露。"""
    counts = est.StratifiedCounts(
        strata=["a", "rare"], ref_classes=["a", "rare"],
        W=np.array([0.9, 0.1]), N=np.array([9000.0, 1000.0]),
        counts=np.array([[100, 0], [4, 1]], dtype=np.int64),
        n_planned=np.array([100, 5], dtype=np.int64),
        excluded=np.zeros(2, dtype=np.int64),
    )
    res = est.stratified_estimate(counts, total_area=10000.0)
    mv = res.area_fraction["rare"]
    # p̂ = 0.1 × 1/5 = 0.02；SE ≈ 0.01995 → CI low 越界 <0
    assert mv.estimate == pytest.approx(0.02, abs=1e-12)
    assert mv.ci_low is not None and mv.ci_low < 0.0        # 原始越界保留
    assert mv.ci_high > 0.0
    assert mv.ci_low_clipped == pytest.approx(0.0, abs=1e-15)  # 裁剪副本
    assert mv.ci_high_clipped is not None and mv.ci_high_clipped <= 1.0
    # 稀有层 n=5 → 正态近似披露
    assert res.normal_approx_ok[1] is np.False_ or bool(res.normal_approx_ok[1]) is False


def test_nonresponse_policy_refusal() -> None:
    """顶替率超设计上限 ⇒ not_evaluated + 机制限制披露（goal WP2）。"""
    counts = est.StratifiedCounts(
        strata=["a", "b"], ref_classes=["a", "b"],
        W=np.array([0.5, 0.5]), N=np.array([1000.0, 1000.0]),
        counts=np.array([[9, 1], [10, 0]], dtype=np.int64),
        n_planned=np.array([10, 10], dtype=np.int64),
        excluded=np.zeros(2, dtype=np.int64),
        substitution_rate={"a": 0.5, "b": 0.0},   # 层 a 一半主样本被顶替
        shortfall_rate={"a": 0.0, "b": 0.0},
        max_nonresponse_rate=0.2,
    )
    res = est.stratified_estimate(counts, total_area=2000.0)
    assert res.status == "not_evaluated"
    assert any("no longer satisfies" in lim for lim in res.limitations)
    assert not res.overall_accuracy.evaluable


def test_zero_weights_rejected() -> None:
    """层权和 ≠1 ⇒ typed 拒绝。"""
    with pytest.raises(Exception, match="must sum to 1"):
        est.StratifiedCounts(
            strata=["a"], ref_classes=["a"],
            W=np.array([0.5]), N=np.array([10.0]),
            counts=np.array([[5]], dtype=np.int64),
            n_planned=np.array([5], dtype=np.int64),
            excluded=np.zeros(1, dtype=np.int64),
        )
