"""optical/SAR acquisition alignment v1 契约测试。

红线：对齐是**计划**（AlignmentPlan），绝不静默重采样；网格恒等不一致
typed 拒绝（与 lakehouse require_aligned 同口径）；配不上的获取以类型化
缺口披露，绝不伪造资产 ref。
"""
from __future__ import annotations

import pytest

from app.lib.geo_analysis import rs_alignment as ral
from app.lib.geo_analysis import rs_cube_descriptor as rcd
from app.lib.gis.scientific_errors import DegenerateData


def _grid(crs="EPSG:32650", width=8, height=8):
    return {"crs": crs, "width": width, "height": height,
            "transform": (10.0, 0.0, 500000.0, 0.0, -10.0, 4000000.0)}


def _optical_desc(times, grid=None, **kw) -> rcd.TemporalRasterCubeDescriptor:
    assets = [{"ref": f"ref:raster/o-{t}", "time_iso": t,
               "role": "optical", "band": "nir", **kw.get("asset_kw", {})}
              for t in times]
    return rcd.build_cube_descriptor(
        cube_id=kw.get("cube_id", "cube-opt"), grid=grid or _grid(),
        assets=assets)


def _sar_desc(times, grid=None, pol="vv", **kw) -> rcd.TemporalRasterCubeDescriptor:
    assets = [{"ref": f"ref:raster/s-{pol}-{t}", "time_iso": t,
               "role": "sar", "polarization": pol}
              for t in times]
    return rcd.build_cube_descriptor(
        cube_id=kw.get("cube_id", "cube-sar"), grid=grid or _grid(),
        assets=assets)


class TestHappyPath:
    def test_monthly_optical_paired_with_sar_within_tolerance(self):
        optical = _optical_desc([f"2024-{m:02d}-01" for m in range(1, 7)])
        sar = _sar_desc([f"2024-{m:02d}-03" for m in range(1, 7)])
        plan = ral.align_acquisitions(optical, sar, tolerance_days=5)
        assert len(plan.pairs) == 6
        assert plan.unpaired_optical_times == ()
        assert plan.unpaired_sar_times == ()
        assert all(p.dt_days <= 5.0 for p in plan.pairs)
        # 配对率 1.0 且进 coverage
        assert plan.coverage["pair_rate"] == pytest.approx(1.0)

    def test_aligned_descriptor_carries_both_modalities_and_lineage(self):
        optical = _optical_desc(["2024-03-01", "2024-04-01"])
        sar = _sar_desc(["2024-03-03", "2024-04-03"])
        plan = ral.align_acquisitions(optical, sar, tolerance_days=5)
        d = plan.aligned_descriptor
        assert d.modalities() == {"optical", "sar"}
        assert d.n_observation_assets == 4
        assert set(optical.cube_id) & set(d.lineage[0])
        assert optical.cube_id in d.lineage and sar.cube_id in d.lineage
        # 时间轴 = 两模态并集（升序唯一）
        assert len(d.times_sec) == 4

    def test_input_gap_codes_preserved_through_alignment(self):
        optical = _optical_desc(
            ["2024-03-01"], asset_kw={"gap_code": "cloud",
                                      "quality_fraction": 0.1})
        sar = _sar_desc(["2024-03-02"])
        plan = ral.align_acquisitions(optical, sar, tolerance_days=5)
        aligned = plan.aligned_descriptor
        opt_assets = [a for a in aligned.assets if a.role == "optical"]
        assert opt_assets[0].gap_code == "cloud"
        # 声明为缺口的观测不参与有效配对率（诚实口径）
        assert plan.coverage["valid_optical_assets"] == 0


class TestTypedRefusals:
    def test_grid_crs_mismatch_rejected_no_silent_resample(self):
        optical = _optical_desc(["2024-03-01"], grid=_grid(crs="EPSG:32650"))
        sar = _sar_desc(["2024-03-02"], grid=_grid(crs="EPSG:4326"))
        with pytest.raises(DegenerateData, match="crs"):
            ral.align_acquisitions(optical, sar, tolerance_days=5)

    def test_grid_shape_mismatch_rejected(self):
        optical = _optical_desc(["2024-03-01"], grid=_grid(width=8))
        sar = _sar_desc(["2024-03-02"], grid=_grid(width=16))
        with pytest.raises(DegenerateData, match="width"):
            ral.align_acquisitions(optical, sar, tolerance_days=5)

    def test_transform_mismatch_rejected(self):
        g1 = _grid()
        g2 = _grid()
        g2["transform"] = (20.0, 0.0, 500000.0, 0.0, -10.0, 4000000.0)
        optical = _optical_desc(["2024-03-01"], grid=g1)
        sar = _sar_desc(["2024-03-02"], grid=g2)
        with pytest.raises(DegenerateData, match="transform"):
            ral.align_acquisitions(optical, sar, tolerance_days=5)

    def test_tolerance_bounds(self):
        optical = _optical_desc(["2024-03-01"])
        sar = _sar_desc(["2024-03-02"])
        for bad in (-1, 46):
            with pytest.raises(ValueError, match="tolerance"):
                ral.align_acquisitions(optical, sar, tolerance_days=bad)


class TestGapSemantics:
    def test_sar_beyond_tolerance_unpaired_disclosed(self):
        optical = _optical_desc(["2024-03-01"])
        sar = _sar_desc(["2024-03-20"])          # 19 天后，超容差
        plan = ral.align_acquisitions(optical, sar, tolerance_days=5)
        assert plan.pairs == ()
        assert len(plan.unpaired_optical_times) == 1
        assert plan.unpaired_optical_times[0].startswith("2024-03-01")
        assert plan.unpaired_sar_times[0].startswith("2024-03-20")
        assert plan.coverage["pair_rate"] == pytest.approx(0.0)
        # 未配对时段进 joint 缺口账（不影响 SAR-only 可用性——诚实区分）
        assert plan.coverage["joint_missing_slots"] == 1

    def test_sar_scene_not_reused_across_optical_dates(self):
        optical = _optical_desc(["2024-03-01", "2024-03-03"])
        sar = _sar_desc(["2024-03-02"])          # 一景 SAR，两期光学都在容差内
        plan = ral.align_acquisitions(optical, sar, tolerance_days=3)
        assert len(plan.pairs) == 1              # 一景只配最近的一期
        assert plan.pairs[0].optical_ref.endswith("2024-03-01")
        assert plan.coverage["joint_missing_slots"] == 1

    def test_single_sar_far_away_all_optical_unpaired(self):
        optical = _optical_desc(["2024-03-01", "2024-04-01"])
        sar = _sar_desc(["2025-01-01"])
        plan = ral.align_acquisitions(optical, sar, tolerance_days=5)
        assert plan.pairs == ()
        assert len(plan.unpaired_optical_times) == 2

    def test_pair_dt_sign_preserved_for_direction_disclosure(self):
        optical = _optical_desc(["2024-03-01"])
        sar_before = _sar_desc(["2024-02-28"])   # 2024 闰年：02-28 → 03-01 = 2 天
        plan = ral.align_acquisitions(optical, sar_before, tolerance_days=5)
        assert plan.pairs[0].dt_days == pytest.approx(-2.0)


class TestDeterminism:
    def test_same_inputs_same_plan(self):
        optical = _optical_desc([f"2024-0{m}-01" for m in range(1, 5)])
        sar = _sar_desc([f"2024-0{m}-02" for m in range(1, 5)])
        p1 = ral.align_acquisitions(optical, sar, tolerance_days=5)
        p2 = ral.align_acquisitions(optical, sar, tolerance_days=5)
        assert p1.pairs == p2.pairs
        assert p1.coverage == p2.coverage
        assert p1.aligned_descriptor.assets == p2.aligned_descriptor.assets


class TestAdversarialReviewFixes:
    """Review R1 修复锁：配对 ref 必须优先有效观测 + 大表对齐可达。"""

    def test_pair_prefers_valid_asset_over_declared_gap(self):
        assets = [
            {"ref": "ref:raster/o-cloud", "time_iso": "2024-01-01",
             "role": "optical", "band": "nir", "gap_code": "cloud"},
            {"ref": "ref:raster/o-ok", "time_iso": "2024-01-01",
             "role": "optical", "band": "nir"},
        ]
        optical = rcd.build_cube_descriptor(
            cube_id="cube-o2", grid=_grid(), assets=assets)
        sar = _sar_desc(["2024-01-02"])
        plan = ral.align_acquisitions(optical, sar, tolerance_days=5)
        assert plan.pairs[0].optical_ref == "ref:raster/o-ok"

    def test_two_large_descriptors_align_beyond_single_cap(self):
        from app.lib.gis.scientific_errors import ResourceScaleMismatch

        # 各 300 资产（各自 ≤512 单表上限），合并 600 > 512：
        # 对齐输出表不得被单表上限二次拒绝
        times_a = [f"2023-{m:02d}-{d:02d}" for m in range(1, 13)
                   for d in range(1, 26)][:300]
        times_b = [f"2025-{m:02d}-{d:02d}" for m in range(1, 13)
                   for d in range(1, 26)][:300]
        optical = rcd.build_cube_descriptor(
            cube_id="cube-big-a", grid=_grid(),
            assets=[{"ref": f"ref:raster/a-{t}", "time_iso": t,
                     "role": "optical", "band": "nir"} for t in times_a])
        sar = _sar_desc(times_b)
        plan = ral.align_acquisitions(optical, sar, tolerance_days=5)
        # 合并表（56 资产）合法：两输入各自 ≤ 单表上限
        assert plan.aligned_descriptor.n_observation_assets > 0
        # 单表超限仍然 typed 拒绝（不因合并路径放宽输入纪律）
        too_big = [f"2023-{m:02d}-01" for m in range(1, 13)] * 43  # 516
        with pytest.raises(Exception):
            rcd.build_cube_descriptor(
                cube_id="cube-over", grid=_grid(),
                assets=[{"ref": f"r{i}", "time_iso": t, "role": "sar",
                         "polarization": "vv"}
                        for i, t in enumerate(too_big)])
        del ResourceScaleMismatch
