"""Temporal science VNext conformance tests (ADR-0099 temporal pack).

Trusted hand-computed fixtures for the nonparametric trend/change-point
family (app/services/temporal/trend.py) and the SAR extension
(app/lib/geo_analysis/sar_temporal.py):

- MK monotone 12-point increasing -> p < 0.01, direction increasing;
  white noise (RandomState(42), n=50) -> p > 0.05
- ties: S matches a brute-force double loop; p is close to (but not equal
  to) scipy.stats.kendalltau because of the INTENTIONAL continuity
  correction (|S| − 1) and tie handling — documented, not a bug
- seasonal MK: 3yr x 12mo seasonal+trend -> significant; pure seasonal ->
  not significant; seasons with < 3 observations skipped & disclosed
- CUSUM: n=60 shift at 30 (0 -> +2) -> index within ±3, p < 0.05;
  no-shift -> p > 0.05 / index None; determinism (same seed -> same p)
- n=2 / n=3 -> InsufficientSamples (n=2 gets the dedicated hint);
  4 <= n < 8 -> descriptive-only warning
- SAR stack: 4-slice known series -> mean/std/min/max exact at sample
  pixels; log-ratio exact; T=25 -> ResourceScaleMismatch with estimate
- SAR acquisition metadata validation + determinism across repeated calls.
"""
import math

import numpy as np
import pytest

from app.lib.geo_analysis.sar_temporal import (
    SAR_PRODUCTS,
    SARAcquisitionMeta,
    temporal_log_ratio_change,
    temporal_stack_statistics,
    vh_ratio,
)
from app.lib.gis.scientific_errors import (
    InsufficientSamples,
    ResourceScaleMismatch,
)
from app.services.temporal.trend import (
    TemporalTrendEngine,
    TemporalTrendResult,
    TemporalTrendResultWithSignificance,
    cusum_change_point,
    mann_kendall,
    seasonal_mann_kendall,
)

pytestmark = pytest.mark.unit


# ── 1. Mann-Kendall ───────────────────────────────────────────────────

def test_mk_monotone_increasing_and_white_noise():
    up = mann_kendall(list(np.arange(1, 13, dtype=float)))
    # S = C(12,2) = 66（全部对子同号）
    assert up["S"] == 66
    assert up["p_value"] < 0.01
    assert up["direction"] == "increasing"
    assert up["significant"] is True
    # 单调序列自身强序列相关 → 警告在场
    assert any("序列相关" in w for w in up["warnings"])

    # 白噪声（RandomState(42)，n=50）→ 不显著
    noise = np.random.RandomState(42).randn(50)
    ns = mann_kendall(list(noise))
    assert ns["p_value"] > 0.05
    assert ns["significant"] is False


def test_mk_ties_vs_scipy_and_bruteforce():
    vals = [3.0, 1.0, 2.0, 2.0, 5.0, 4.0, 4.0, 6.0]
    res = mann_kendall(vals)

    # S 用暴力双循环复核：Σ_{i<j} sign(x_j − x_i)
    s_brute = sum(
        np.sign(vals[j] - vals[i])
        for i in range(len(vals)) for j in range(i + 1, len(vals))
    )
    assert res["S"] == int(s_brute)

    # tie 校正方差暴力复核：Var = (n(n-1)(2n+5) − Σ t(t-1)(2t+5)) / 18
    n = len(vals)
    _, counts = np.unique(vals, return_counts=True)
    tie_term = sum(int(t) * (t - 1) * (2 * t + 5) for t in counts if t > 1)
    var_expected = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0
    assert res["var_s"] == pytest.approx(var_expected, rel=1e-12)

    # 连续性校正 z = (S−1)/√Var（S>0），p = erfc(|z|/√2) 手算复核
    import math as _math

    z_expected = (int(s_brute) - 1) / _math.sqrt(var_expected)
    p_expected = _math.erfc(abs(z_expected) / _math.sqrt(2.0))
    assert res["z"] == pytest.approx(z_expected, rel=1e-9)
    assert res["p_value"] == pytest.approx(p_expected, rel=1e-9)

    # 与 scipy.stats.kendalltau 的有意差异：连续性校正（z 用 |S|−1 折算）
    # 与 tau-b 的并列处理不同——p 接近但不必相等（文档化的实现选择）。
    from scipy import stats

    _, p_scipy = stats.kendalltau(range(n), vals)
    assert abs(res["p_value"] - p_scipy) < 0.05
    assert (res["p_value"] < 0.5) == (p_scipy < 0.5)


def test_mk_sample_size_gates():
    # n=2：专属提示「两个时间点无法定义趋势统计量」
    with pytest.raises(InsufficientSamples, match="两个时间点"):
        mann_kendall([1.0, 2.0])
    # n=3：n < 4 拒绝
    with pytest.raises(InsufficientSamples, match="n=3"):
        mann_kendall([1.0, 3.0, 2.0])
    # n=5（4 ≤ n < 8）：照常计算 + 描述性解读警告
    vals5 = [1.0, 5.0, 2.0, 6.0, 3.0]
    r5 = mann_kendall(vals5)
    assert any("样本过少" in w for w in r5["warnings"])
    assert r5["n"] == 5
    # 手算：S = 4（4 对升 + 3 对降 → 净 +4），无并列 Var = 5·4·15/18
    assert r5["S"] == 4
    assert r5["var_s"] == pytest.approx(50.0 / 3.0, rel=1e-12)
    assert r5["p_value"] == pytest.approx(
        math.erfc(((4 - 1) / math.sqrt(50.0 / 3.0)) / math.sqrt(2.0)), rel=1e-9)


# ── 2. Seasonal Mann-Kendall ──────────────────────────────────────────

def _seasonal_series(with_trend=True):
    """3 年 × 12 月：年循环 + 可选线性趋势。"""
    base = {m: 10.0 + 3.0 * np.sin(2 * np.pi * m / 12.0) for m in range(1, 13)}
    dates, values = [], []
    for yr in range(2021, 2024):
        for m in range(1, 13):
            dates.append(f"{yr}-{m:02d}-15")
            i = (yr - 2021) * 12 + m
            values.append(base[m] + (0.05 * i if with_trend else 0.0))
    return values, dates


def test_seasonal_mk_trend_significant_pure_seasonal_not():
    values, dates = _seasonal_series(with_trend=True)
    res = seasonal_mann_kendall(values, dates)
    assert res["seasons_used"] == 12
    assert res["season_mode"] == "monthly"
    assert res["p_value"] < 0.05
    assert res["direction"] == "increasing"
    # 池化 S = Σ 逐季 S（每年同月递增 → 每季 S=3，总 S=36）
    assert res["S"] == sum(g["S"] for g in res["per_season"])
    assert res["var_s"] == pytest.approx(
        sum(g["var_s"] for g in res["per_season"]), rel=1e-9)
    assert any("预白化" in lim for lim in res["limitations"])

    pure_values, pure_dates = _seasonal_series(with_trend=False)
    pure = seasonal_mann_kendall(pure_values, pure_dates)
    # 纯季节循环：每年同月值相同 → 每季 S=0 → 池化 p=1
    assert pure["S"] == 0
    assert pure["p_value"] == 1.0
    assert not pure["significant"]


def test_seasonal_mk_skips_thin_seasons_and_discloses():
    # 2021-2022 全年 + 2023 仅 1-4 月：月 1-4 各 3 观测（用），
    # 月 5-12 各 2 观测（< 3 → 跳过并披露）。
    values, dates = _seasonal_series(with_trend=True)
    values = values[:28]
    dates = dates[:28]
    res = seasonal_mann_kendall(values, dates)
    assert res["seasons_used"] == 4
    assert len(res["skipped_seasons"]) == 8
    assert all(sk["observations"] == 2 for sk in res["skipped_seasons"])
    assert any("跳过" in lim for lim in res["limitations"])
    assert res["n"] == 12   # 4 个合格季节 × 3 观测

    # quarterly 模式：4 组
    q = seasonal_mann_kendall(values, dates, season="quarterly")
    assert q["seasons_used"] == 4
    assert q["season_mode"] == "quarterly"

    with pytest.raises(ValueError, match="unsupported season mode"):
        seasonal_mann_kendall(values, dates, season="weekly")

    # 全部季节 < 3 观测 → 类型化拒绝
    with pytest.raises(InsufficientSamples):
        seasonal_mann_kendall([1.0, 2.0, 1.5, 2.5],
                              ["2021-01-01", "2021-01-02", "2021-02-01", "2021-02-02"])


# ── 3. CUSUM change point ─────────────────────────────────────────────

def test_cusum_shift_detected_and_deterministic():
    x = np.concatenate([np.zeros(30), np.full(30, 2.0)])   # n=60，漂移在第 30 点
    res = cusum_change_point(x, bootstrap_draws=200, seed=42)
    assert 27 <= res["candidate_index"] <= 33
    assert res["change_point_index"] == res["candidate_index"]
    assert res["p_value"] < 0.05
    assert res["magnitude"] == pytest.approx(2.0, abs=1e-9)
    assert res["bootstrap_draws"] == 200
    assert res["seed"] == 42

    # 确定性：同 seed 同输入 → 逐位同 p
    again = cusum_change_point(x, bootstrap_draws=200, seed=42)
    assert again["p_value"] == res["p_value"]
    assert again["candidate_index"] == res["candidate_index"]
    # 不同 seed 结果同量级（不逐位相同——种子是显式参数）
    other = cusum_change_point(x, bootstrap_draws=200, seed=7)
    assert other["p_value"] < 0.05


def test_cusum_no_shift_and_insufficient():
    noise = list(np.random.RandomState(42).randn(60))
    res = cusum_change_point(noise)
    assert (res["p_value"] > 0.05) or (res["change_point_index"] is None)
    assert res["change_point_index"] is None

    with pytest.raises(InsufficientSamples):
        cusum_change_point([1.0, 2.0])
    with pytest.raises(ValueError, match="bootstrap_draws"):
        cusum_change_point(list(np.arange(20.0)), bootstrap_draws=10)

    # n < 10 → 警告在场
    small = cusum_change_point([0.0, 0.1, 0.2, 2.0, 2.1, 2.2])
    assert any("n=6" in w or "样本过少" in w for w in small["warnings"])


# ── 4. analyze_trend method 分支 ──────────────────────────────────────

def test_analyze_trend_method_branches():
    eng = TemporalTrendEngine()
    values = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0]

    # 缺省与显式 ols_sen 逐位一致，且是基类（历史形状不变）
    default = eng.analyze_trend(values)
    explicit = eng.analyze_trend(values, method="ols_sen")
    assert type(default) is TemporalTrendResult
    assert type(explicit) is TemporalTrendResult
    assert default.model_dump() == explicit.model_dump()

    # mann_kendall：子类 + 显著性证据
    mk = eng.analyze_trend(values, method="mann_kendall")
    assert isinstance(mk, TemporalTrendResult)
    assert isinstance(mk, TemporalTrendResultWithSignificance)
    assert mk.trend_method == "mann_kendall"
    sig = mk.significance_evidence[0]
    assert sig["target"] == "mann_kendall"
    assert sig["p_value"] == pytest.approx(mann_kendall(values)["p_value"], rel=1e-9)
    assert mk.direction == "increasing"
    # slope/intercept 等基类字段仍然在（Sen/OLS 同缺省路径计算）
    assert mk.slope == default.slope

    # seasonal_mann_kendall 无日期 → 类型化拒绝
    from app.lib.gis.scientific_errors import MissingRequiredField

    with pytest.raises(MissingRequiredField):
        eng.analyze_trend(values, method="seasonal_mann_kendall")

    seasonal_values, seasonal_dates = _seasonal_series(with_trend=True)
    smk = eng.analyze_trend(seasonal_values, timestamps=seasonal_dates,
                            method="seasonal_mann_kendall")
    assert smk.trend_method == "seasonal_mann_kendall"
    assert smk.significance_evidence[0]["p_value"] < 0.05
    assert smk.direction == "increasing"
    assert any("预白化" in w for w in smk.method_warnings)

    with pytest.raises(ValueError, match="unsupported trend method"):
        eng.analyze_trend(values, method="theil_sen_2049")


# ── 5. SAR 时序栈 ─────────────────────────────────────────────────────

def _sar_stack():
    """4 切片 (2x2)：每像元的时序手工可算。"""
    return np.array([
        [[1.0, 2.0], [3.0, 4.0]],
        [[3.0, 4.0], [5.0, 6.0]],
        [[5.0, 6.0], [7.0, 8.0]],
        [[7.0, 8.0], [9.0, 10.0]],
    ])


def test_sar_stack_statistics_hand_exact():
    stack = _sar_stack()
    series = {
        (0, 0): [1.0, 3.0, 5.0, 7.0],
        (0, 1): [2.0, 4.0, 6.0, 8.0],
        (1, 1): [4.0, 6.0, 8.0, 10.0],
    }

    mean = temporal_stack_statistics(stack, product="mean")
    std = temporal_stack_statistics(stack, product="std")
    mn = temporal_stack_statistics(stack, product="min")
    mx = temporal_stack_statistics(stack, product="max")
    rng_ = temporal_stack_statistics(stack, product="range")

    for (i, j), s in series.items():
        assert mean["array"][i, j] == pytest.approx(sum(s) / 4.0, abs=1e-12)
        assert std["array"][i, j] == pytest.approx(
            math.sqrt(sum((v - sum(s) / 4.0) ** 2 for v in s) / 4.0), abs=1e-12)
        assert mn["array"][i, j] == min(s)
        assert mx["array"][i, j] == max(s)
        assert rng_["array"][i, j] == max(s) - min(s)

    # 总体标准差（ddof=0）披露在场
    assert "ddof=0" in std["meta"]["std_convention"]
    # 诚实边界披露
    assert "斑点滤波" in mean["meta"]["disclosure"]
    assert "辐射定标" in mean["meta"]["disclosure"]
    assert set(SAR_PRODUCTS) == {"mean", "std", "min", "max", "range"}

    # nodata：哨兵值逐切片剔除，部分有效像元在剩余有效切片上统计
    stack_nan = stack.copy()
    stack_nan[0, 0, 0] = np.nan          # (0,0) 首切片无效
    stack_nan[2, 0, 0] = -9999.0         # (0,0) 第三切片哨兵
    part = temporal_stack_statistics(stack_nan, product="mean", nodata=-9999.0)
    assert part["array"][0, 0] == pytest.approx((3.0 + 7.0) / 2.0, abs=1e-12)
    assert part["meta"]["pixels_partially_valid"] >= 1
    # 全切片无效 → NaN
    allbad = stack.copy()
    allbad[:, 1, 0] = np.nan
    bad = temporal_stack_statistics(allbad, product="mean")
    assert np.isnan(bad["array"][1, 0])

    with pytest.raises(ValueError, match="unsupported SAR product"):
        temporal_stack_statistics(stack, product="median")


def test_sar_stack_scale_guard():
    with pytest.raises(ResourceScaleMismatch) as exc_info:
        temporal_stack_statistics(np.zeros((25, 4, 4)))
    err = exc_info.value
    assert err.estimated is not None and "25×4×4" in err.estimated
    assert err.limit is not None
    # T ≤ 24 / H·W ≤ 4096² 均为独立维度闸
    with pytest.raises(ResourceScaleMismatch):
        temporal_stack_statistics(np.zeros((2, 4097, 4097)))
    # 顶格合法规模通过守卫（(24, 1, 1) 远低于像素上界）
    ok = temporal_stack_statistics(np.zeros((24, 1, 1)))
    assert ok["meta"]["time_slices"] == 24


def test_sar_vh_ratio_and_log_ratio_exact():
    res = vh_ratio(np.array([[2.0, 4.0]]), np.array([[4.0, 8.0]]))
    np.testing.assert_allclose(res["array"], np.array([[0.5, 0.5]]), rtol=0, atol=1e-15)
    # VH=0 → NaN
    z = vh_ratio(np.array([[2.0]]), np.array([[0.0]]))
    assert np.isnan(z["array"][0, 0])
    assert "vv / vh" in z["meta"]["formula"]

    # 对数比值精确：log(4/2) = ln 2
    lr = temporal_log_ratio_change(np.array([[4.0]]), np.array([[2.0]]),
                                   t1_date="2024-06-01", t2_date="2025-06-01")
    assert lr["array"][0, 0] == pytest.approx(math.log(2.0), abs=1e-15)
    assert lr["method"] == "log_ratio"
    assert lr["meta"]["t1_date"] == "2024-06-01"


def test_sar_acquisition_meta_validation():
    meta = SARAcquisitionMeta(
        polarization="VV", acquisition_date="2024-06-01",
        incidence_angle_deg=35.2, orbit_direction="ascending")
    assert meta.polarization == "vv"

    import pydantic

    with pytest.raises(pydantic.ValidationError):
        SARAcquisitionMeta(polarization="HV_cross", acquisition_date="2024-06-01")
    with pytest.raises(pydantic.ValidationError):
        SARAcquisitionMeta(polarization="vv", acquisition_date="2024/06/01")
    with pytest.raises(pydantic.ValidationError):
        SARAcquisitionMeta(polarization="vv", acquisition_date="2024-06-01",
                           incidence_angle_deg=95.0)
    with pytest.raises(pydantic.ValidationError):
        SARAcquisitionMeta(polarization="vv", acquisition_date="2024-06-01",
                           orbit_direction="polar")

    # 可比较性检查：入射角差 > 5° / 轨道混搭 → 警告
    from app.lib.geo_analysis.sar_temporal import acquisition_comparability

    m1 = SARAcquisitionMeta(polarization="vv", acquisition_date="2024-06-01",
                            incidence_angle_deg=30.0, orbit_direction="ascending")
    m2 = SARAcquisitionMeta(polarization="vv", acquisition_date="2025-06-01",
                            incidence_angle_deg=38.0, orbit_direction="descending")
    comp = acquisition_comparability([m1, m2])
    assert comp["comparable"] is False
    assert len(comp["warnings"]) == 2


# ── 6. 确定性 ─────────────────────────────────────────────────────────

def test_temporal_science_determinism():
    values, dates = _seasonal_series(with_trend=True)
    assert mann_kendall(values[:20]) == mann_kendall(values[:20])
    assert seasonal_mann_kendall(values, dates) == seasonal_mann_kendall(values, dates)

    x = list(np.arange(20.0))
    assert cusum_change_point(x) == cusum_change_point(x)
    stack = _sar_stack()
    a = temporal_stack_statistics(stack, product="std")
    b = temporal_stack_statistics(stack, product="std")
    np.testing.assert_array_equal(a["array"], b["array"])
    assert a["meta"] == b["meta"]
