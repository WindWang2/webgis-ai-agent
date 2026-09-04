"""Change-detection science VNext conformance tests (ADR-0099).

Trusted hand-computed fixtures for the in-memory change science layer
(app/lib/geo_analysis/raster_change.py: CVA / ratio_change / threshold_change):

- CVA 2-band hand arrays: magnitude = sqrt(Δred² + Δnir²) exact,
  angle = atan2(Δnir, Δred) exact (canonical role order red < nir)
- identical scenes -> magnitude 0; nodata in either scene -> NaN
- role order is the documented, asserted contract (spectral.ROLE_ORDER)
- ratio / log-ratio hand-exact; log(a/b) == −log(b/a) symmetry; zeros -> NaN
- MAD threshold: ~5% spikes -> changed_fraction ≈ 0.05 (MAD=0 degenerate
  path disclosed); percentile exact vs np.percentile; disclosure meta present.
"""
import math

import numpy as np
import pytest

from app.lib.geo_analysis.raster_change import (
    change_vector_analysis,
    ratio_change,
    threshold_change,
)
from app.lib.geo_analysis.spectral import ROLE_ORDER
from app.lib.gis.scientific_errors import (
    NoValidObservations,
    UnsupportedBandSemantics,
)

pytestmark = pytest.mark.unit


def _two_band_scenes():
    t1 = {"red": np.array([[0.2, 0.1], [0.3, 0.4]]),
          "nir": np.array([[0.5, 0.6], [0.7, 0.8]])}
    # 像元 (0,0)：Δred = +0.2，Δnir = −0.2；其余像元不变
    t2 = {"red": np.array([[0.4, 0.1], [0.3, 0.4]]),
          "nir": np.array([[0.3, 0.6], [0.7, 0.8]])}
    return t1, t2


# ── 1. CVA hand-computed exact ────────────────────────────────────────

def test_cva_two_band_hand_computed_exact():
    t1, t2 = _two_band_scenes()
    res = change_vector_analysis(t1, t2, t1_date="2024-06-01", t2_date="2025-06-01")

    d_red, d_nir = 0.2, -0.2
    np.testing.assert_allclose(
        res["magnitude"],
        np.array([[math.hypot(d_red, d_nir), 0.0], [0.0, 0.0]]),
        rtol=0, atol=1e-15,
    )
    np.testing.assert_allclose(
        res["angle"],
        np.array([[math.atan2(d_nir, d_red), 0.0], [0.0, 0.0]]),
        rtol=0, atol=1e-15,
    )
    assert res["meta"]["t1_date"] == "2024-06-01"
    assert res["meta"]["t2_date"] == "2025-06-01"
    # 反目标披露：CVA 不做土地覆盖语义分类
    assert "不构成土地覆盖语义变化" in res["meta"]["disclosure"]

    # 3 角色也符合欧氏范数定义：Δ = (0.3, 0.4, 0.0) -> 0.5
    t1b = {"blue": np.array([[0.1]]), "red": np.array([[0.1]]), "nir": np.array([[0.1]])}
    t2b = {"blue": np.array([[0.4]]), "red": np.array([[0.5]]), "nir": np.array([[0.1]])}
    res3 = change_vector_analysis(t1b, t2b)
    assert res3["magnitude"][0, 0] == pytest.approx(0.5, abs=1e-15)


def test_cva_identical_scenes_zero_and_nodata():
    t1, t2 = _two_band_scenes()
    res = change_vector_analysis(t1, t1)
    assert float(np.nanmax(res["magnitude"])) == 0.0
    assert float(np.nanmax(np.abs(res["angle"]))) == 0.0

    # 任一角色任一期 NaN → 该像元幅度/角度 NaN（有效 = 双方都有效）
    t1_nan = {k: v.copy() for k, v in t1.items()}
    t1_nan["red"][1, 1] = np.nan
    res2 = change_vector_analysis(t1_nan, t2)
    assert np.isnan(res2["magnitude"][1, 1])
    assert np.isnan(res2["angle"][1, 1])
    assert np.isfinite(res2["magnitude"][0, 0])

    # 显式 nodata 掩膜同样成立
    nodata = np.zeros((2, 2), dtype=bool)
    nodata[0, 1] = True
    res3 = change_vector_analysis(t1, t2, nodata=nodata)
    assert np.isnan(res3["magnitude"][0, 1])


def test_cva_role_order_documented_and_asserted():
    """角色序 = spectral.ROLE_ORDER（固定语义序），文档化并断言。"""
    t1, t2 = _two_band_scenes()
    res = change_vector_analysis(t1, t2)
    assert res["roles_used"] == ["red", "nir"]
    assert ROLE_ORDER.index("red") < ROLE_ORDER.index("nir")
    assert "atan2" in res["meta"]["role_order_contract"]

    # 加入 blue 后角色序变 [blue, red, nir]——角度改由 blue/red 平面定义：
    # Δblue = 0.1，Δred = 0.2 → angle = atan2(0.2, 0.1)
    t1b = dict(t1, blue=np.array([[0.0, 0.1], [0.1, 0.1]]))
    t2b = dict(t2, blue=np.array([[0.1, 0.1], [0.1, 0.1]]))
    res_b = change_vector_analysis(t1b, t2b)
    assert res_b["roles_used"] == ["blue", "red", "nir"]
    assert res_b["angle"][0, 0] == pytest.approx(math.atan2(0.2, 0.1), abs=1e-15)

    # 角色集不一致 → 类型化拒绝（绝不按位置对齐）
    with pytest.raises(UnsupportedBandSemantics):
        change_vector_analysis(t1, {"red": t2["red"]})


# ── 2. Ratio / log-ratio hand exact + symmetry + zeros ────────────────

def test_ratio_and_log_ratio_hand_exact():
    a = np.array([[4.0, 9.0]])
    b = np.array([[2.0, 3.0]])
    res = ratio_change(a, b, method="ratio")
    np.testing.assert_allclose(res["array"], np.array([[2.0, 3.0]]), rtol=0, atol=1e-15)

    lr = ratio_change(a, b, method="log_ratio")
    np.testing.assert_allclose(
        lr["array"], np.log(a / b), rtol=0, atol=1e-15)
    assert lr["array"][0, 0] == pytest.approx(math.log(2.0), abs=1e-15)
    assert "−log_ratio(b, a)" in lr["meta"]["symmetry"]
    assert "对数域对称" in lr["meta"]["formula"]

    # 日期透传
    dated = ratio_change(a, b, method="log_ratio",
                         t1_date="2024-01-01", t2_date="2025-01-01")
    assert dated["meta"]["t1_date"] == "2024-01-01"
    assert dated["meta"]["t2_date"] == "2025-01-01"

    with pytest.raises(ValueError, match="unsupported ratio method"):
        ratio_change(a, b, method="nope")


def test_log_ratio_symmetry_and_zeros():
    a = np.array([[4.0]])
    b = np.array([[2.0]])
    forward = ratio_change(a, b, method="log_ratio")["array"]
    backward = ratio_change(b, a, method="log_ratio")["array"]
    # log(a/b) == −log(b/a)（对数域对称：增强 = 衰减的镜像）
    assert forward[0, 0] == pytest.approx(-backward[0, 0], rel=1e-12)

    # 零分母 → NaN（ratio）；零/负输入 log 无定义 → NaN（log_ratio）
    z = ratio_change(np.array([[4.0]]), np.array([[0.0]]), method="ratio")
    assert np.isnan(z["array"][0, 0])
    lz = ratio_change(np.array([[0.0]]), np.array([[2.0]]), method="log_ratio")
    assert np.isnan(lz["array"][0, 0])

    # SAR 域门面：temporal_log_ratio_change 是 ratio_change(method=log_ratio)
    from app.lib.geo_analysis.sar_temporal import temporal_log_ratio_change

    facade = temporal_log_ratio_change(a, b)
    direct = ratio_change(a, b, method="log_ratio")
    np.testing.assert_array_equal(facade["array"], direct["array"])


# ── 3. Threshold classification ───────────────────────────────────────

def test_threshold_mad_percentile_and_disclosure():
    rng = np.random.RandomState(7)

    # 退化尺度：常量背景（MAD=0）+ 精确 5% 尖峰 → 高于中位数即变化，
    # 退化路径披露在场（阈值 = 最小超中位值）。
    degenerate = np.zeros(4000)
    degenerate[rng.choice(4000, 200, replace=False)] = 5.0
    mad0 = threshold_change(degenerate, method="mad")
    assert mad0["changed_fraction"] == pytest.approx(0.05, abs=1e-12)
    assert "退化" in "".join(mad0["meta"]["warnings"])
    assert mad0["threshold_value"] == pytest.approx(5.0, abs=1e-12)

    # 有尺度（噪声背景）：median + k·1.4826·MAD 阈值，≈ 5% 被判变化
    noisy = rng.normal(0.0, 1.0, 4000)
    noisy[rng.choice(4000, 200, replace=False)] = 25.0
    mad = threshold_change(noisy, method="mad", k=3.0)
    med = float(np.median(noisy))
    mad_val = float(np.median(np.abs(noisy - med)))
    assert mad["method"] == "mad"
    assert mad["threshold_value"] == pytest.approx(
        med + 3.0 * 1.4826 * mad_val, rel=1e-12)
    assert mad["changed_fraction"] == pytest.approx(0.05, abs=0.01)
    # 诚实披露：空间独立假设是近似
    assert "空间独立" in mad["meta"]["disclosure"]

    # percentile：阈值精确等于 np.percentile；changed_fraction 精确（连续分布）
    pct = threshold_change(noisy, method="percentile", percentile=95)
    assert pct["threshold_value"] == pytest.approx(
        float(np.percentile(noisy, 95)), rel=1e-12)
    assert pct["changed_fraction"] == pytest.approx(0.05, abs=1e-12)
    assert pct["meta"]["percentile"] == 95.0
    assert "空间独立" in pct["meta"]["disclosure"]

    with pytest.raises(ValueError, match="unsupported threshold method"):
        threshold_change(noisy, method="nope")
    with pytest.raises(NoValidObservations):
        threshold_change(np.full(10, np.nan))


# ── 4. Determinism ────────────────────────────────────────────────────

def test_change_science_determinism():
    t1, t2 = _two_band_scenes()
    r1 = change_vector_analysis(t1, t2)
    r2 = change_vector_analysis(t1, t2)
    np.testing.assert_array_equal(r1["magnitude"], r2["magnitude"])
    np.testing.assert_array_equal(r1["angle"], r2["angle"])

    a, b = np.array([[4.0, 1.0]]), np.array([[2.0, 2.0]])
    q1 = ratio_change(a, b, method="log_ratio")
    q2 = ratio_change(a, b, method="log_ratio")
    np.testing.assert_array_equal(q1["array"], q2["array"])

    rng = np.random.RandomState(11)
    noisy = rng.normal(0.0, 1.0, 500)
    t1 = threshold_change(noisy, method="mad")
    t2 = threshold_change(noisy, method="mad")
    np.testing.assert_array_equal(t1["mask"], t2["mask"])
    assert t1["threshold_value"] == t2["threshold_value"]
