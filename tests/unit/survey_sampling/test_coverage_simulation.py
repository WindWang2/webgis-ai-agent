"""CI 覆盖模拟与无偏性（WP4：固定 seed、预定义试验规模、依据披露）。

设计：固定小有限总体（N=4000，3 层，W=(0.5,0.3,0.2)），每单元的参考
标签由已知误差过程生成 → 重复 SRSnwr 抽样 400 次（master seed 固定），
检验：
1. **无偏性**：OA/面积估计的 MC 均值 ≈ 总体真值（容差 = 3×MC 误差）；
2. **覆盖**：真值落在 95% CI 内的比例 ≈ 0.95 ± 0.06（400 次的 MC
   σ ≈ 0.0109，3σ ≈ 0.033；取 0.06 兼容有限总体非正态小样本）；
3. **SE 校准**：平均 SE ≈ 经验 SD（比率 ∈ [0.85, 1.15]）。

400 次试验规模依据：coverage MC σ≈0.011 → 检出 0.06 偏差功效充分；
总体规模与层数按 Olofsson 数值例子量级缩小，运行 <30s。
"""
from __future__ import annotations

import numpy as np

from app.lib.survey_sampling import estimation as est

REPS = 400
MASTER_SEED = 20261127
N_STRATUM = (2000, 1200, 800)          # W = 0.5 / 0.3 / 0.2
#: 真实参考类概率矩阵 P(ref=j | stratum=h)（行=层，列=参考类）
TRUE_P = np.array([
    [0.80, 0.15, 0.05],
    [0.20, 0.70, 0.10],
    [0.10, 0.30, 0.60],
])
N_SAMPLE = (60, 40, 30)
STRATA = ["s1", "s2", "s3"]
TOTAL_AREA = 4000.0                    # 单位面积像元 ⇒ 面积=单元数


def _make_population(rng: np.random.Generator) -> np.ndarray:
    """固定总体：每层按 TRUE_P 生成参考标签（0/1/2）。"""
    labels = np.zeros(sum(N_STRATUM), dtype=np.int64)
    offset = 0
    for h, n in enumerate(N_STRATUM):
        labels[offset:offset + n] = rng.choice(
            3, size=n, p=TRUE_P[h])
        offset += n
    return labels


def test_ci_coverage_simulation() -> None:
    rng = np.random.default_rng(MASTER_SEED)
    population = _make_population(rng)

    # 总体真值（从生成总体统计——模拟的"地面真值"）
    bounds = np.cumsum([0] + list(N_STRATUM))
    col_counts = np.zeros(3)
    correct_true = 0.0
    for h, n in enumerate(N_STRATUM):
        seg = population[bounds[h]:bounds[h + 1]]
        correct_true += np.mean(seg == h) * (n / sum(N_STRATUM))
        for j in range(3):
            col_counts[j] += np.mean(seg == j) * (n / sum(N_STRATUM))
    oa_true = correct_true

    oa_estimates = np.zeros(REPS)
    oa_covered = np.zeros(REPS, dtype=bool)
    se_estimates = np.zeros(REPS)
    area0_estimates = np.zeros(REPS)
    area0_covered = np.zeros(REPS, dtype=bool)

    W = np.array([n / sum(N_STRATUM) for n in N_STRATUM])
    N = np.array([float(n) for n in N_STRATUM])

    for rep in range(REPS):
        counts = np.zeros((3, 3), dtype=np.int64)
        for h, n_h in enumerate(N_SAMPLE):
            seg = population[bounds[h]:bounds[h + 1]]
            picked = rng.choice(seg.size, size=n_h, replace=False)
            for u in picked:
                counts[h, seg[u]] += 1
        cin = est.StratifiedCounts(
            strata=STRATA, ref_classes=STRATA,
            W=W, N=N, counts=counts,
            n_planned=np.array(N_SAMPLE, dtype=np.int64),
            excluded=np.zeros(3, dtype=np.int64))
        res = est.stratified_estimate(cin, total_area=TOTAL_AREA)
        oa_estimates[rep] = res.overall_accuracy.estimate
        se_estimates[rep] = res.overall_accuracy.se or np.nan
        oa_covered[rep] = (res.overall_accuracy.ci_low <= oa_true
                           <= res.overall_accuracy.ci_high)
        mv0 = res.area_fraction[STRATA[0]]
        area0_estimates[rep] = mv0.estimate
        area0_covered[rep] = (mv0.ci_low * TOTAL_AREA <= col_counts[0] * TOTAL_AREA
                              <= mv0.ci_high * TOTAL_AREA)

    # 1) 无偏性（|MC 均值 − 真值| ≤ 3×SD/√REPS，附加绝对下限防退化）
    mc_se = np.std(oa_estimates) / np.sqrt(REPS)
    assert abs(np.mean(oa_estimates) - oa_true) <= max(3 * mc_se, 0.01), (
        f"OA bias {np.mean(oa_estimates) - oa_true:.4f}")
    mc_se0 = np.std(area0_estimates) / np.sqrt(REPS)
    assert abs(np.mean(area0_estimates) - col_counts[0]) <= max(3 * mc_se0, 0.01)

    # 2) 覆盖 ≈ 95%（容差 0.06 ≈ 3×MCσ + 小样本余量）
    oa_coverage = float(np.mean(oa_covered))
    area0_coverage = float(np.mean(area0_covered))
    assert abs(oa_coverage - 0.95) <= 0.06, f"OA coverage {oa_coverage:.3f}"
    assert abs(area0_coverage - 0.95) <= 0.06, (
        f"area coverage {area0_coverage:.3f}")

    # 3) SE 校准：mean(SE) ≈ 经验 SD（比率 ∈ [0.85, 1.15]）
    empirical_sd = np.std(oa_estimates, ddof=1)
    ratio = float(np.nanmean(se_estimates)) / empirical_sd
    assert 0.85 <= ratio <= 1.15, f"SE/SD ratio {ratio:.3f}"

    # 披露：打印实测证据（进 pytest -s 输出；不作为断言）
    print(f"[survey coverage] OA true={oa_true:.4f} "
          f"mean={np.mean(oa_estimates):.4f} coverage={oa_coverage:.3f} "
          f"SE/SD={ratio:.3f} area0 coverage={area0_coverage:.3f}")


def test_substitution_backfill_uses_backup() -> None:
    """替补顶替：主样本非响应 → 预抽替补顶位（服务层策略的内核前提）。"""
    # 直接构造：层 a 主样本 1 个不可用（拒绝），替补 1 个可用
    counts = est.StratifiedCounts(
        strata=["a"], ref_classes=["a"],
        W=np.array([1.0]), N=np.array([10.0]),
        counts=np.array([[5]], dtype=np.int64),   # 主 5 可用 + 替补 1 → 6
        n_planned=np.array([5], dtype=np.int64),
        excluded=np.zeros(1, dtype=np.int64),
        substitution_rate={"a": 0.2},
        shortfall_rate={"a": 0.0},
        max_nonresponse_rate=0.2,
    )
    res = est.stratified_estimate(counts, total_area=10.0)
    # 顶替率恰等于上限（不越限）→ 可估但降级 partial（顶替事实已披露）
    assert res.status == "partial"
    assert res.overall_accuracy.evaluable
    assert any("substituted units" in lim for lim in res.limitations)
