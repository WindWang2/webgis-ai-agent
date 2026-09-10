"""Temporal Cube 基础测试中与 cube_stats descriptor 对应的补充锚（W8）。

主测试体在 test_phenology_v5.py；本文件承载 descriptor conformance
引用的类名（TestTemporalCube/TestBuildCube 的部分用例拆分放置——
保持单一语义一个文件内的可发现性）。
"""
from __future__ import annotations

import numpy as np

from app.lib.geo_analysis import temporal_cube as tc


class TestBuildCube:
    def test_nodata_and_quality_masking(self):
        t = np.arange(10.0) * 86400.0
        stack = np.ones((10, 3, 3))
        stack[1, 0, 0] = -9999.0
        quality = np.ones((10, 3, 3))
        quality[2, 1, 1] = 0.0
        cube = tc.build_cube(stack, t, nodata=-9999.0, quality=quality)
        # nodata → 值置 NaN；quality 只改有效掩膜（不改原始值）
        assert np.isnan(cube.stack[1, 0, 0])
        assert cube.stack[2, 1, 1] == 1.0
        assert not cube.valid_mask()[1, 0, 0]
        assert not cube.valid_mask()[2, 1, 1]
        assert cube.valid_mask()[0, 0, 0]
        assert any("nodata=-9999.0" in s for s in cube.disclosures)

    def test_adapter_labels(self):
        t = np.arange(8.0)
        stack = np.ones((8, 2, 2))
        sar = tc.from_sar_stack(stack, t)
        opt = tc.from_optical_stack(stack, t)
        assert sar.label == "sar" and opt.label == "optical"
        assert any("SAR" in s for s in sar.disclosures)


class TestTemporalCube:
    def test_slice_spacing_report_missing_slices(self):
        t = np.array([0, 1, 2, 3, 5, 6, 7, 9], dtype=float) * 86400.0
        stack = np.ones((len(t), 2, 2))
        cube = tc.build_cube(stack, t)
        rep = cube.slice_spacing_report()
        assert rep["missing_slices"] == 2
        assert rep["unique_gaps"] == [86400.0, 172800.0]
