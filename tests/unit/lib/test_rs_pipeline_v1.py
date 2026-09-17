"""端到端 pipeline v1 契约测试（Oracle 核心）。

**tiny SAR+optical 时序可完成 cube→feature→fusion→MapProduct**；
缺测/质量差不作为 0 或有效观测；leakage 测试贯通到 product 表；全程
refs-only 语义（摘要有界，payload 只在图层通道）。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.geo_analysis import rs_cube_descriptor as rcd
from app.lib.geo_analysis import rs_cube_pipeline as rcp
from app.lib.gis.scientific_errors import DegenerateData


def _grid():
    return {"crs": "EPSG:32650", "width": 4, "height": 4,
            "transform": (10.0, 0.0, 500000.0, 0.0, -10.0, 4000000.0)}


def _optical_descriptor(gap_codes=None):
    times = [f"2024-0{m}-15" for m in range(3, 9)]          # 3-8 月
    assets = []
    for i, t in enumerate(times):
        a = {"ref": f"ref:raster/s2-{t}", "time_iso": t,
             "role": "optical", "band": "nir"}
        if gap_codes and i < len(gap_codes) and gap_codes[i]:
            a["gap_code"] = gap_codes[i]
        assets.append(a)
    # 多波段：同刻 red 资产
    for i, t in enumerate(times):
        a = {"ref": f"ref:raster/s2-red-{t}", "time_iso": t,
             "role": "optical", "band": "red"}
        if gap_codes and i < len(gap_codes) and gap_codes[i]:
            a["gap_code"] = gap_codes[i]
        assets.append(a)
    return rcd.build_cube_descriptor(
        cube_id="cube-opt-e2e", grid=_grid(), assets=assets,
        source_version="S2_L2A_TEST")


def _sar_descriptor(skip_month=None):
    months = [m for m in range(3, 9) if m != skip_month]
    assets = [{"ref": f"ref:raster/s1-vv-2024-0{m}-17",
               "time_iso": f"2024-0{m}-17", "role": "sar",
               "polarization": "vv"} for m in months]
    return rcd.build_cube_descriptor(
        cube_id="cube-sar-e2e", grid=_grid(), assets=assets,
        source_version="S1_GRD_TEST")


def _stacks():
    rng = np.random.default_rng(11)
    n_opt = 6
    ndvi = np.clip(rng.normal(0.5, 0.1, (n_opt, 4, 4)), 0, 1)
    opt_times = np.array([
        _ts(2024, m, 15) for m in range(3, 9)])
    n_sar = 6
    vv = rng.normal(-12.0, 1.0, (n_sar, 4, 4))
    sar_times = np.array([_ts(2024, m, 17) for m in range(3, 9)])
    return ndvi, opt_times, vv, sar_times


def _ts(y, m, d):
    import datetime as dt

    return dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp()


class TestEndToEnd:
    def _run(self, **kw):
        ndvi, opt_times, vv, sar_times = _stacks()
        kw.setdefault("optical_gap_codes", [None] * 6)
        return rcp.run_temporal_cube_pipeline(
            _optical_descriptor(kw.pop("gap_codes", None)),
            _sar_descriptor(),
            optical_stack=ndvi, optical_times_sec=opt_times,
            sar_stack=vv, sar_times_sec=sar_times,
            tolerance_days=5, **kw)

    def test_full_pipeline_happy_path(self):
        out = self._run()
        # ① 对齐：全部配对
        assert out["alignment"].coverage["n_pairs"] == 6
        # ② 特征：两模态都有 p50 与 sen_slope
        assert "p50" in out["optical_features"]["features"]
        assert "sen_slope" in out["sar_features"]["features"]
        # ③ 融合：联合栈含两个命名空间 + fused trend 图层
        names = out["fusion"]["feature_names"]
        assert any(n.startswith("optical::") for n in names)
        assert any(n.startswith("sar::") for n in names)
        assert "fused_trend" in out["product"]["layers"]
        # ④ product：图/表/工件链
        p = out["product"]
        assert p["charts"] and p["tables"]
        types = [a.artifact_type for a in p["artifacts"]]
        assert "rs_cube_descriptor" in types
        assert "raster_surface" in types
        assert "stats_table" in types
        assert "chart_spec" in types
        # lineage 贯通两 cube
        joined = [a for a in p["artifacts"] if a.lineage]
        assert any(opt in a.lineage for a in joined
                   for opt in ("cube-opt-e2e", "cube-sar-e2e"))

    def test_missing_acquisition_is_typed_slot_not_zero(self):
        ndvi, opt_times, vv, sar_times = _stacks()
        # 6 月无 SAR：descriptor 与栈一致缺席（missing_acquisition 语义）
        out = rcp.run_temporal_cube_pipeline(
            _optical_descriptor(), _sar_descriptor(skip_month=6),
            optical_stack=ndvi, optical_times_sec=opt_times,
            sar_stack=np.delete(vv, 3, axis=0),
            sar_times_sec=np.delete(sar_times, 3), tolerance_days=5)
        slots = out["slot_table"]
        missing = [s for s in slots if s["code"] == "missing_acquisition"]
        assert len(missing) == 1
        assert missing[0]["time"].startswith("2024-06")
        # 缺口不污染 fused trend：像元不被 0 填充（NaN 传播语义在 meta）
        assert out["product"]["layers"]["fused_trend"]["valid_ratio"] > 0

    def test_payload_without_ref_rejected(self):
        ndvi, opt_times, vv, sar_times = _stacks()
        # 栈里多出 descriptor 没有的时刻 = payload/refs 漂移 → typed 拒绝
        import datetime as dt

        extra_t = np.append(sar_times, dt.datetime(
            2024, 9, 17, tzinfo=dt.timezone.utc).timestamp())
        with pytest.raises(ValueError, match="漂移"):
            rcp.run_temporal_cube_pipeline(
                _optical_descriptor(), _sar_descriptor(),
                optical_stack=ndvi, optical_times_sec=opt_times,
                sar_stack=np.concatenate([vv, vv[:1]]),
                sar_times_sec=extra_t, tolerance_days=5)

    def test_cloud_gap_optical_not_used_as_observation(self):
        ndvi, opt_times, vv, sar_times = _stacks()
        quality = np.ones((6, 4, 4))
        quality[2] = 0.0                       # 5 月整片质量 0
        out = rcp.run_temporal_cube_pipeline(
            _optical_descriptor(
                gap_codes=[None, None, "cloud", None, None, None]),
            _sar_descriptor(skip_month=6),
            optical_stack=ndvi, optical_times_sec=opt_times,
            optical_quality=quality,
            sar_stack=np.delete(vv, 3, axis=0),
            sar_times_sec=np.delete(sar_times, 3), tolerance_days=5)
        # 槽位表：5 月是 cloud（获取存在但不可用），非 missing
        slots = {s["time"][:10]: s["code"] for s in out["slot_table"]}
        assert slots.get("2024-05-15") == "cloud"
        assert slots.get("2024-06-15") == "missing_acquisition"
        # 覆盖卡：cloud 缺口计数 2（nir+red 双波段资产同刻声明）
        assert out["coverage_card"]["gap_counts"].get("cloud") == 2
        # 光学特征不把质量 0 切片当观测：feature pack 输入已按 quality 置 NaN
        assert out["optical_features"]["meta"]["n_invalid_pixel_slices"] >= 16

    def test_stack_time_mismatch_typed_rejection(self):
        ndvi, opt_times, vv, sar_times = _stacks()
        with pytest.raises(ValueError, match="等长|align"):
            rcp.run_temporal_cube_pipeline(
                _optical_descriptor(), _sar_descriptor(),
                optical_stack=ndvi, optical_times_sec=opt_times,
                sar_stack=vv, sar_times_sec=sar_times[:-1],
                tolerance_days=5)

    def test_grid_mismatch_rejected_before_compute(self):
        ndvi, opt_times, vv, sar_times = _stacks()
        bad = rcd.build_cube_descriptor(
            cube_id="cube-sar-bad",
            grid={"crs": "EPSG:32650", "width": 8, "height": 8,
                  "transform": (10.0, 0.0, 500000.0, 0.0, -10.0, 4000000.0)},
            assets=[{"ref": "r", "time_iso": "2024-03-17", "role": "sar",
                     "polarization": "vv"}])
        with pytest.raises(DegenerateData):
            rcp.run_temporal_cube_pipeline(
                _optical_descriptor(), bad,
                optical_stack=ndvi, optical_times_sec=opt_times,
                sar_stack=vv[:, :1, :1] if False else vv,
                sar_times_sec=sar_times, tolerance_days=5)

    def test_product_summary_bounded(self):
        out = self._run()
        summary = out["summary"]
        import json
        blob = json.dumps(summary)
        assert len(blob) < 6000
        assert summary["n_pairs"] == 6

    def test_deterministic_replay(self):
        o1 = self._run()
        o2 = self._run()
        assert o1["coverage_card"] == o2["coverage_card"]
        assert (o1["fusion"]["coverage"] == o2["fusion"]["coverage"]).all()
        assert o1["slot_table"] == o2["slot_table"]


class TestPipelineSamples:
    def test_polygon_samples_with_split(self):
        ndvi, opt_times, vv, sar_times = _stacks()

        def rect(x0, y0, x1, y1, label):
            xs = [500000 + x * 10 for x in (x0, x1, x1, x0, x0)]
            ys = [4000000 - y * 10 for y in (y0, y0, y1, y1, y0)]
            return {"type": "Polygon", "coordinates": [list(zip(xs, ys))],
                    "properties": {"label": label, "id": label}}

        polys = [rect(0, 0, 1, 1, "crop"), rect(2, 2, 3, 3, "water"),
                 rect(0, 2, 1, 3, "crop2"), rect(2, 0, 3, 1, "water2")]
        out = rcp.run_temporal_cube_pipeline(
            _optical_descriptor(), _sar_descriptor(),
            optical_stack=ndvi, optical_times_sec=opt_times,
            sar_stack=vv, sar_times_sec=sar_times, tolerance_days=5,
            polygons=polys, grid=_grid())
        samples = out["samples"]
        assert samples is not None
        assert samples["matrix"]["X"].shape[0] == 4
        assert samples["matrix"]["y"] == ["crop", "water", "crop2", "water2"]
        # leakage 不变量在 product 表中披露
        split_table = [t for t in out["product"]["tables"]
                       if t["id"] == "split_report"]
        assert split_table
        assert split_table[0]["rows"][0]["invariant"] == "block_disjoint_folds"

    def test_samples_require_grid(self):
        ndvi, opt_times, vv, sar_times = _stacks()
        with pytest.raises(ValueError, match="grid"):
            rcp.run_temporal_cube_pipeline(
                _optical_descriptor(), _sar_descriptor(),
                optical_stack=ndvi, optical_times_sec=opt_times,
                sar_stack=vv, sar_times_sec=sar_times, tolerance_days=5,
                polygons=[{"type": "Polygon",
                           "coordinates": [[(0, 0), (1, 0), (1, 1), (0, 1),
                                            (0, 0)]]}])


class TestAdversarialReviewFixes:
    """Review R1 修复锁：可选样本通道不足 4 样本时降级不炸管线。"""

    def test_few_polygons_degrade_split_not_fail_pipeline(self):
        ndvi, opt_times, vv, sar_times = _stacks()

        def rect(x0, y0, x1, y1, label):
            xs = [500000 + x * 10 for x in (x0, x1, x1, x0, x0)]
            ys = [4000000 - y * 10 for y in (y0, y0, y1, y1, y0)]
            return {"type": "Polygon", "coordinates": [list(zip(xs, ys))],
                    "properties": {"label": label, "id": label}}

        out = rcp.run_temporal_cube_pipeline(
            _optical_descriptor(), _sar_descriptor(),
            optical_stack=ndvi, optical_times_sec=opt_times,
            sar_stack=vv, sar_times_sec=sar_times, tolerance_days=5,
            polygons=[rect(0, 0, 1, 1, "a"), rect(2, 2, 3, 3, "b")],
            grid=_grid())
        assert out["samples"] is not None
        assert out["samples"]["split"] is None     # 2 样本 → split 降级
        assert out["summary"]["n_pairs"] == 6      # 主链路不受影响
