"""typed gap model v1 契约测试：槽位缺口账 + 逐像元 GapMask + 覆盖卡。

红线：缺测/质量差**绝不**被当作 0 或有效观测——缺口是类型化语义
（uint8 码平面），比率只计数披露，不做静默插值填充。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.geo_analysis import rs_alignment as ral
from app.lib.geo_analysis import rs_cube_descriptor as rcd
from app.lib.geo_analysis import rs_gaps as rg


def _grid():
    return {"crs": "EPSG:32650", "width": 4, "height": 4,
            "transform": (10.0, 0.0, 500000.0, 0.0, -10.0, 4000000.0)}


def _desc(times, role="optical", band="nir", gap_codes=None):
    assets = []
    for i, t in enumerate(times):
        a = {"ref": f"ref:raster/{role}-{t}", "time_iso": t, "role": role}
        if role == "optical":
            a["band"] = band
        else:
            a["polarization"] = "vv"
        if gap_codes and i < len(gap_codes) and gap_codes[i]:
            a["gap_code"] = gap_codes[i]
        assets.append(a)
    return rcd.build_cube_descriptor(
        cube_id=f"cube-{role}", grid=_grid(), assets=assets)


class TestSlotLedger:
    def test_aligned_plan_joint_slot_table(self):
        optical = _desc([f"2024-{m:02d}-01" for m in range(1, 7)])
        sar = _desc([f"2024-{m:02d}-02" for m in range(1, 5)], role="sar")
        plan = ral.align_acquisitions(optical, sar, tolerance_days=5)
        table = rg.joint_slot_table(plan)
        codes = {s["code"] for s in table}
        assert "paired" in codes
        assert "missing_acquisition" in codes   # 5-6 月无 SAR → joint 缺口
        assert len(table) == 6                  # 光学时刻数为槽位轴
        missing = [s for s in table if s["code"] == "missing_acquisition"]
        assert len(missing) == 2

    def test_declared_gaps_are_slots_not_observations(self):
        optical = _desc(["2024-03-01", "2024-04-01"],
                        gap_codes=[None, "cloud"])
        plan = ral.align_acquisitions(
            optical, _desc(["2024-03-02"], role="sar"), tolerance_days=5)
        table = rg.joint_slot_table(plan)
        april = [s for s in table if s["time"].startswith("2024-04")]
        assert april[0]["code"] == "cloud"      # 声明缺口优先于配对语义

    def test_expected_cadence_missing_acquisitions(self):
        d = _desc(["2024-01-01", "2024-02-01", "2024-04-01"])  # 3 月缺测
        expected = ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]
        report = rg.cadence_gap_report(d, expected_times=expected)
        assert len(report["missing_times"]) == 1
        assert report["missing_times"][0].startswith("2024-03-01")
        assert report["n_expected"] == 4
        assert report["n_observed_valid"] == 3
        assert report["missing_ratio"] == pytest.approx(0.25)

    def test_cadence_report_unexpected_times_disclosed(self):
        d = _desc(["2024-01-01", "2024-06-01"])
        expected = ["2024-01-01"]
        report = rg.cadence_gap_report(d, expected_times=expected)
        assert len(report["unexpected_times"]) == 1
        assert report["unexpected_times"][0].startswith("2024-06-01")


class TestPixelGapMask:
    def test_gap_code_ids_stable_and_disjoint(self):
        ids = rg.GAP_CODE_IDS
        assert ids["valid"] == 0
        assert sorted(ids.values()) == list(range(len(ids)))
        assert set(ids) - {"valid"} == set(rg.GAP_CODES)

    def test_nan_and_declared_gaps_typed_not_zero(self):
        t = np.arange(4) * 86400.0
        stack = np.ones((4, 4, 4))
        stack[1, 0, 0] = np.nan          # 有效切片内的无效像元 → nodata
        declared = [None, None, "cloud", None]   # 切片 2 整片云
        mask = rg.build_gap_mask(stack, declared_codes=declared)
        assert mask.dtype == np.uint8
        assert mask[0, 1, 2] == rg.GAP_CODE_IDS["valid"]
        assert mask[1, 0, 0] == rg.GAP_CODE_IDS["nodata"]
        assert (mask[2] == rg.GAP_CODE_IDS["cloud"]).all()
        # 缺口平面与值平面解耦：cloud 切片的原始值不被改写为 0
        assert stack[2, 1, 1] == 1.0

    def test_quality_mask_zero_maps_below_quality(self):
        t = np.arange(3) * 86400.0
        stack = np.ones((3, 2, 2))
        quality = np.ones((3, 2, 2))
        quality[0, 1, 1] = 0.0
        mask = rg.build_gap_mask(stack, quality=quality)
        assert mask[0, 1, 1] == rg.GAP_CODE_IDS["below_quality"]
        assert mask[0, 0, 0] == rg.GAP_CODE_IDS["valid"]

    def test_declared_code_priority_over_nodata(self):
        t = np.arange(2) * 86400.0
        stack = np.ones((2, 2, 2))
        stack[1, 0, 0] = np.nan
        mask = rg.build_gap_mask(stack, declared_codes=[None, "layover"])
        # 整片 layover 优先：像元级 nodata 在声明缺口切片内仍记 layover
        assert (mask[1] == rg.GAP_CODE_IDS["layover"]).all()

    def test_gap_report_ratios(self):
        t = np.arange(3) * 86400.0
        stack = np.ones((3, 2, 2))
        declared = [None, "cloud", None]
        mask = rg.build_gap_mask(stack, declared_codes=declared)
        report = rg.gap_report(mask)
        assert report["total_pixels"] == 3 * 2 * 2
        assert report["counts"]["cloud"] == 2 * 2
        assert report["ratios"]["cloud"] == pytest.approx(4 / 12)
        assert report["valid_ratio"] == pytest.approx(8 / 12)

    def test_mask_shape_mismatch_rejected(self):
        stack = np.ones((3, 2, 2))
        with pytest.raises(ValueError, match="形状|shape"):
            rg.build_gap_mask(stack, declared_codes=[None, "cloud"])

    def test_unknown_declared_code_rejected(self):
        stack = np.ones((2, 2, 2))
        with pytest.raises(ValueError, match="GAP_CODES"):
            rg.build_gap_mask(stack, declared_codes=[None, "haze"])


class TestCoverageCard:
    def test_coverage_card_aggregates_alignment_and_versions(self):
        optical = _desc([f"2024-0{m}-01" for m in range(1, 5)],
                        gap_codes=[None, "cloud", None, None])
        sar = _desc([f"2024-0{m}-02" for m in range(1, 4)], role="sar")
        plan = ral.align_acquisitions(optical, sar, tolerance_days=5)
        card = rg.build_coverage_card(plan)
        assert card["cube_id"] == plan.cube_id
        assert card["alignment"]["pair_rate"] == plan.coverage["pair_rate"]
        assert card["gap_counts"]["cloud"] == 1
        assert card["joint_missing_slots"] == plan.coverage[
            "joint_missing_slots"]
        # 有界 JSON 安全：无数组 payload
        import json
        blob = json.dumps(card)
        assert len(blob) < 4000

    def test_coverage_card_discloses_source_versions(self):
        optical = rcd.build_cube_descriptor(
            cube_id="cube-o", grid=_grid(),
            assets=[{"ref": "r1", "time_iso": "2024-03-01",
                     "role": "optical", "band": "nir"}],
            source_version="S2_L2A_v1")
        sar = rcd.build_cube_descriptor(
            cube_id="cube-s", grid=_grid(),
            assets=[{"ref": "r2", "time_iso": "2024-03-02", "role": "sar",
                     "polarization": "vv"}],
            source_version="S1_GRD_v2")
        plan = ral.align_acquisitions(optical, sar, tolerance_days=5)
        card = rg.build_coverage_card(plan)
        assert "S2_L2A_v1" in card["source_versions"]
        assert "S1_GRD_v2" in card["source_versions"]
