"""Conformance tests for ``temporal.smooth_gapfill`` (Science V6 Phase G).

Contract bullets under test (descriptor in app/lib/gis/algorithms/
temporal.py; numerics in app/lib/geo_analysis/ts_smoothing.py):

- savgol preserves linear signals to machine precision (polynomial
  reproduction property) and suppresses white noise (RMSE reduction);
- gap semantics: filled values participate in smoothing and are flagged
  in ``filled_mask``; ``fill="none"`` keeps NaN positions NaN;
- even window_length is auto-corrected to odd and disclosed in meta;
- gap-dominated series (≥90%) and tiny series are typed rejections;
- determinism: identical payload on repeated runs.
"""
import numpy as np
import pytest

from app.lib.geo_analysis.ts_smoothing import smooth_gapfill
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    NoValidObservations,
)

pytestmark = pytest.mark.unit


def test_savgol_linear_identity_and_noise_suppression():
    """多项式再现性：线性信号逐位保持（机器精度）。"""
    lin = np.linspace(3.0, 9.0, 20)
    smoothed, mask, meta = smooth_gapfill(lin, method="savgol",
                                          window_length=5, polyorder=2)
    assert np.abs(smoothed - lin).max() < 1e-12
    assert meta["method"] == "savgol"
    assert meta["filled_count"] == 0

    # 噪声抑制：正弦 + 白噪声的平滑 RMSE 低于原始 RMSE
    rng = np.random.default_rng(0)
    t = np.linspace(0, 2 * np.pi, 60)
    clean = np.sin(t)
    noisy = clean + rng.normal(0, 0.2, 60)
    smoothed_n, _, _ = smooth_gapfill(noisy, method="savgol",
                                      window_length=7, polyorder=2)
    rmse_raw = float(np.sqrt(np.mean((noisy - clean) ** 2)))
    rmse_smooth = float(np.sqrt(np.mean((smoothed_n - clean) ** 2)))
    assert rmse_smooth < rmse_raw


def test_gap_fill_semantics():
    """缺口参与平滑并被标记；fill=none 保持 NaN；gap 占比披露。"""
    rng = np.random.default_rng(0)
    t = np.linspace(0, 2 * np.pi, 50)
    signal = np.sin(t) + rng.normal(0, 0.15, 50)
    signal[10:14] = np.nan

    smoothed, mask, meta = smooth_gapfill(signal, window_length=7,
                                          polyorder=2, fill="linear")
    assert mask.sum() == 4
    assert meta["filled_count"] == 4
    assert np.isfinite(smoothed).all()

    s_none, mask_none, meta_none = smooth_gapfill(signal, window_length=7,
                                                  polyorder=2, fill="none")
    assert np.isnan(s_none[10:14]).all(), "fill=none must keep gaps NaN"
    # 窗口扩散（保守口径）：缺口 ±(wl//2) 邻域同为 NaN
    assert np.isnan(s_none[7:17]).all()
    assert mask_none[7:17].sum() == 0
    # fill=none 不做填补：filled_mask 全 False，缺口由 filled_count=0 +
    # gap_fraction 披露；nan_window_bleed=True 披露扩散语义
    assert mask_none.sum() == 0
    assert meta_none["filled_count"] == 0
    assert meta_none["nan_window_bleed"] is True

    assert meta["gap_fraction"] == pytest.approx(4 / 50, abs=1e-6)


def test_savgol_even_window_autocorrected():
    """偶数窗口自动 +1 取奇（meta 披露修正后的窗口）。"""
    smoothed, _, meta = smooth_gapfill(np.arange(30.0), method="savgol",
                                       window_length=6, polyorder=2)
    assert meta["window_length"] == 7
    assert smoothed.shape == (30,)


def test_smooth_gapfill_determinism():
    rng = np.random.default_rng(3)
    v = rng.normal(5, 1, 40)
    v[5] = np.nan
    a, ma, ma_ = smooth_gapfill(v, window_length=5, polyorder=2)
    b, mb, mb_ = smooth_gapfill(v, window_length=5, polyorder=2)
    assert np.array_equal(a, b, equal_nan=True)
    assert np.array_equal(ma, mb)
    assert ma_ == mb_


def test_smooth_gapfill_adversarial_inputs():
    with pytest.raises(InsufficientSamples, match="3 observations"):
        smooth_gapfill([1.0, np.nan])
    with pytest.raises(NoValidObservations, match="1D"):
        smooth_gapfill(np.ones((3, 3)))
    with pytest.raises(DegenerateData, match="gaps"):
        smooth_gapfill(np.array([np.nan] * 10))
    with pytest.raises(ValueError, match="polyorder"):
        # 奇数窗口 5 ≤ polyorder 5 → 类型化拒绝（偶数会先 +1 再判）
        smooth_gapfill(np.arange(30.0), window_length=5, polyorder=5)
    with pytest.raises(ValueError, match="method"):
        smooth_gapfill(np.arange(30.0), method="wavelet")
    with pytest.raises(ValueError, match="fill"):
        smooth_gapfill(np.arange(30.0), fill="kriging")
    with pytest.raises(InsufficientSamples, match="window_length"):
        smooth_gapfill(np.arange(6.0), window_length=9, polyorder=2)
