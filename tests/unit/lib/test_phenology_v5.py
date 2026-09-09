"""Temporal Cube + Phenology + Anomaly（science-v5 W8）单元测试。

覆盖（01-architecture.md D6 + 挑战 R0-#9/#14）：

- 正例：合成正弦 NDVI 的 SOS/EOS 手算锚；缺失切片报告；nodata/质量
  掩膜；气候态；z-score 手算锚；双谐波幅值锚。
- 负例：时间轴乱序/非有限、T>512、窗口 ≤ polyorder、threshold_frac
  越界、切片不足、质量掩膜越界。
- 边界：长缺口不外推（排除计数）、全 NaN 像元、偶数窗口 +1、
  baseline_slices 越界。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    ResourceScaleMismatch,
)
from app.lib.geo_analysis import temporal_cube as tc
from app.lib.geo_analysis.phenology import (
    phenology_features,
    temporal_anomaly,
)


def _sine_cube(t_len: int = 24, h: int = 6, w: int = 6, seed: int = 7,
               with_noise: float = 0.05):
    """合成正弦时序：峰值在切片 6（t=0.26 左右由相位决定——此处取整数锚）。

    values = 0.5 + 0.4·sin(2π·t/T) → 峰值 t=T/4、谷值 t=3T/4。
    """
    rng = np.random.default_rng(seed)
    times = np.arange(t_len, dtype=float) * 86400.0
    t_norm = np.arange(t_len) / t_len
    base = 0.5 + 0.4 * np.sin(2 * np.pi * t_norm)
    stack = np.broadcast_to(base[:, None, None], (t_len, h, w)).copy()
    stack += rng.normal(0, with_noise, stack.shape)
    return stack, times


class TestBuildCube:
    def test_nodata_and_quality_masking(self):
        stack, times = _sine_cube(t_len=10)
        stack[2, 0, 0] = -9999.0
        mask = np.ones_like(stack)
        mask[3, 1, 1] = 0.0                       # 云遮蔽
        cube = tc.build_cube(stack, times, nodata=-9999.0, quality=mask)
        assert np.isnan(cube.stack[2, 0, 0])
        # quality=0 只改有效掩膜（原始值保留——掩膜语义而非破坏性编辑）
        assert cube.stack[3, 1, 1] == stack[3, 1, 1]
        vm = cube.valid_mask()
        assert not vm[2, 0, 0] and not vm[3, 1, 1]
        assert any("nodata=-9999.0" in s for s in cube.disclosures)

    def test_unsorted_times_rejected(self):
        stack, times = _sine_cube(t_len=10)
        times[3], times[5] = times[5].copy(), times[3].copy()
        with pytest.raises(DegenerateData, match="非升序"):
            tc.build_cube(stack, times)

    def test_nonfinite_times_rejected(self):
        stack, times = _sine_cube(t_len=10)
        times[2] = np.nan
        with pytest.raises(DegenerateData, match="非有限"):
            tc.build_cube(stack, times)

    def test_over_512_slices_rejected(self):
        stack = np.zeros((513, 2, 2))
        times = np.arange(513.0)
        with pytest.raises(ResourceScaleMismatch, match="512"):
            tc.build_cube(stack, times)

    def test_shape_mismatches_rejected(self):
        stack, times = _sine_cube(t_len=10)
        with pytest.raises(DegenerateData, match="三维栈"):
            tc.build_cube(stack[0], times)          # 2D → 拒绝
        with pytest.raises(DegenerateData, match="等长"):
            tc.build_cube(stack, times[:-1])
        with pytest.raises(DegenerateData, match="不一致"):
            tc.build_cube(stack, times, quality=np.ones((10, 3, 3)))

    def test_quality_range_rejected(self):
        stack, times = _sine_cube(t_len=10)
        with pytest.raises(DegenerateData, match="\\[0, 1\\]"):
            tc.build_cube(stack, times, quality=np.full(stack.shape, 2.0))

    def test_duplicate_times_disclosed(self):
        stack, times = _sine_cube(t_len=10)
        times[3] = times[2]
        cube = tc.build_cube(stack, times)
        assert any("重复时间戳" in s for s in cube.disclosures)


class TestTemporalCube:
    def test_slice_spacing_report_missing_slices(self):
        # 逐日时间轴缺 2 切片 → missing_slices=2
        stack, times = _sine_cube(t_len=10)
        times = np.delete(times, [4, 7])
        stack = np.delete(stack, [4, 7], axis=0)
        cube = tc.build_cube(stack, times)
        rep = cube.slice_spacing_report()
        assert rep["n_slices"] == 8
        assert rep["missing_slices"] == 2
        assert rep["median_gap_sec"] == 86400.0

    def test_gap_lengths(self):
        stack, times = _sine_cube(t_len=12)
        stack[2:5, 0, 0] = np.nan                 # 连续 3
        stack[8, 1, 1] = np.nan                   # 孤立 1
        cube = tc.build_cube(stack, times)
        gl = cube.gap_lengths()
        assert gl[0, 0] == 3
        assert gl[1, 1] == 1
        assert gl[2, 2] == 0

    def test_climatology_nan_aware(self):
        stack, times = _sine_cube(t_len=12)
        stack[:, 0, 0] = np.nan                   # 全 NaN 像元
        stack[1:, 1, 1] = np.nan                  # 仅 1 个有效切片
        stack[:6, 2, 2] = np.nan                  # 半有效（n=6 → std 有效）
        cube = tc.build_cube(stack, times)
        clim = cube.climatology()
        assert np.isnan(clim["mean"][0, 0])
        assert np.isnan(clim["std"][0, 0])        # n=0
        assert np.isnan(clim["std"][1, 1])        # n=1 → std 诚实 NaN
        assert clim["n_valid"][1, 1] == 1
        assert np.isfinite(clim["std"][2, 2])     # n=6 → std 有效
        assert clim["n_valid"][2, 2] == 6


class TestPhenology:
    def test_synthetic_sine_sos_eos_anchor(self):
        # 纯正弦 0.1+0.2·sin(2πt/T)：峰值 t=T/4；thr=min+0.5·amp=0.1
        # → 越过区间 = [T/4 的相邻样本]——T=24 时 sin>0 于 t∈(0,T/2)
        t_len = 24
        t_norm = np.arange(t_len) / t_len
        base = 0.1 + 0.2 * np.sin(2 * np.pi * t_norm)
        stack = np.broadcast_to(base[:, None, None], (t_len, 4, 4)).copy()
        times = np.arange(t_len, dtype=float)
        cube = tc.build_cube(stack, times)
        res = phenology_features(cube, window=5, polyorder=2,
                                 threshold_frac=0.5, max_gap=0)
        f = res["features"]
        # 阈值=0.1=均值 → SOS=首次 sin≥0 的切片=1；t=12 恰在阈值上（含等
        # 号 ≥）→ EOS=12（对称边界含端点）
        assert f["sos_idx"][0, 0] == pytest.approx(1)
        assert f["eos_idx"][0, 0] == pytest.approx(12)
        assert f["los"][0, 0] == pytest.approx(12)
        assert f["peak_time"][0, 0] == pytest.approx(6)   # T/4=6
        assert f["peak_value"][0, 0] == pytest.approx(0.3, abs=1e-3)
        assert f["amplitude"][0, 0] == pytest.approx(0.4, abs=1e-3)

    def test_harmonic_amplitude_recovers_sine(self):
        t_len = 24
        t_norm = np.arange(t_len) / t_len
        base = 0.5 + 0.3 * np.sin(2 * np.pi * t_norm)
        stack = np.broadcast_to(base[:, None, None], (t_len, 3, 3)).copy()
        cube = tc.build_cube(stack, np.arange(t_len, dtype=float))
        f = phenology_features(cube, window=5)["features"]
        # 单一基波 → 双谐波幅值和 ≈ 0.3（SG 平滑后近似）
        assert f["harmonic_amplitude"][0, 0] == pytest.approx(0.3, abs=0.02)

    def test_long_gap_excluded_not_extrapolated(self):
        stack, times = _sine_cube(t_len=24, with_noise=0.0)
        stack[:, 0, 0] = np.nan                   # 整列无效 → 排除
        stack[8:16, 1, 1] = np.nan                # 8 切片长缺口
        cube = tc.build_cube(stack, times)
        res = phenology_features(cube, max_gap=2)
        meta = res["meta"]
        assert meta["n_excluded_pixels"] >= 2     # 全 NaN 列 + 长缺口列
        assert any("长缺口保持 NaN" in s for s in meta["disclosures"])
        f = res["features"]
        assert np.isnan(f["sos_idx"][0, 0])       # 排除像元诚实 NaN
        assert np.isnan(f["sos_idx"][1, 1])       # 长缺口不外推

    def test_gap_filled_count_disclosed(self):
        stack, times = _sine_cube(t_len=24, with_noise=0.0)
        stack[3, 2, 2] = np.nan                   # 单切片缺口 → 可填
        cube = tc.build_cube(stack, times)
        res = phenology_features(cube, max_gap=2)
        assert res["meta"]["n_gap_filled_cells"] >= 1
        assert not np.isnan(res["features"]["sos_idx"][2, 2])

    def test_too_few_slices_rejected(self):
        stack, times = _sine_cube(t_len=6)
        cube = tc.build_cube(stack, times)
        with pytest.raises(InsufficientSamples, match="至少需要 8"):
            phenology_features(cube)

    @pytest.mark.parametrize("kw, match", [
        ({"polyorder": 0}, "polyorder"),
        ({"polyorder": 5}, "polyorder"),
        ({"window": 2, "polyorder": 3}, "window"),
        ({"threshold_frac": 0.0}, "threshold_frac"),
        ({"threshold_frac": 1.2}, "threshold_frac"),
    ])
    def test_parameter_validation(self, kw, match):
        stack, times = _sine_cube(t_len=16)
        cube = tc.build_cube(stack, times)
        with pytest.raises(DegenerateData, match=match):
            phenology_features(cube, **kw)

    def test_even_window_bumped_with_disclosure(self):
        stack, times = _sine_cube(t_len=16, with_noise=0.0)
        cube = tc.build_cube(stack, times)
        res = phenology_features(cube, window=6, polyorder=2)
        assert res["meta"]["smooth_window"] == 7   # 偶数 +1


class TestAnomaly:
    def test_anomaly_z_hand_anchor(self):
        # 前 11 切片常数 1.0，最后切片 = 3：
        # mean=14/12、std(ddof=1)=√(3.6667/11)=0.57735 → z=1.8333/0.57735
        stack = np.ones((12, 2, 2))
        stack[-1] = 3.0                            # 最后切片 = 3
        times = np.arange(12.0)
        cube = tc.build_cube(stack, times)
        f = temporal_anomaly(cube)["features"]
        assert f["anomaly_last"][0, 0] == pytest.approx(3.175426, abs=1e-5)

    def test_change_direction_and_nan_guard(self):
        rng = np.random.default_rng(3)
        stack = np.concatenate([
            rng.normal(1.0, 0.1, (8, 2, 2)),
            rng.normal(3.0, 0.1, (8, 2, 2)),
        ])
        times = np.arange(16.0)
        cube = tc.build_cube(stack, times)
        res = temporal_anomaly(cube)
        f = res["features"]
        assert (f["change_mean"] > 1.5).all()      # 后段 − 前段 = +2 附近
        assert (np.abs(f["change_z"]) > 10).all()  # 低噪声强变化
        # 全 NaN 像元 → 诚实 NaN
        stack2 = stack.copy()
        stack2[:, 0, 0] = np.nan
        cube2 = tc.build_cube(stack2, times)
        f2 = temporal_anomaly(cube2)["features"]
        assert np.isnan(f2["change_mean"][0, 0])

    def test_baseline_slices_validation(self):
        stack, times = _sine_cube(t_len=12)
        cube = tc.build_cube(stack, times)
        with pytest.raises(DegenerateData, match="baseline_slices"):
            temporal_anomaly(cube, baseline_slices=12)
        with pytest.raises(DegenerateData, match="baseline_slices"):
            temporal_anomaly(cube, baseline_slices=-1)

    def test_too_few_slices_rejected(self):
        stack, times = _sine_cube(t_len=3)
        cube = tc.build_cube(stack, times)
        with pytest.raises(InsufficientSamples):
            temporal_anomaly(cube)
