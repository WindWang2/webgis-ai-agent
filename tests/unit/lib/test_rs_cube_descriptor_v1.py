"""TemporalRasterCubeDescriptor（refs-only 时序立方体描述符）v1 契约测试。

诚实边界（任务书红线）：
- descriptor 只携带 ref + 元数据，绝不含栅格 payload；
- 缺测/质量差必须带类型化 gap 语义，不得当作 0 或有效观测；
- 时间轴不静默重排（与 temporal_cube.build_cube 同红线）。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.lib.geo_analysis import rs_cube_descriptor as rcd


def _optical(time: str, band: str = "nir", ref: str = "", **kw) -> dict:
    return {
        "ref": ref or f"ref:raster/opt-{band}-{time}",
        "time_iso": time, "role": "optical", "band": band, **kw,
    }


def _sar(time: str, pol: str = "vv", ref: str = "", **kw) -> dict:
    return {
        "ref": ref or f"ref:raster/sar-{pol}-{time}",
        "time_iso": time, "role": "sar", "polarization": pol, **kw,
    }


def _grid(**kw) -> dict:
    base = {"crs": "EPSG:32650", "width": 8, "height": 8,
            "transform": (10.0, 0.0, 500000.0, 0.0, -10.0, 4000000.0)}
    base.update(kw)
    return base


class TestDescriptorHappyPath:
    def test_build_minimal_optical_cube(self):
        d = rcd.build_cube_descriptor(
            cube_id="cube-demo-1",
            grid=_grid(),
            assets=[_optical(f"2024-0{m}-15") for m in range(4, 8)],
        )
        assert d.cube_id == "cube-demo-1"
        assert d.n_observation_assets == 4
        # times_sec 升序且确定性（date-only → UTC 午夜）
        ts = d.times_sec
        assert ts == tuple(sorted(ts))
        assert all(isinstance(t, float) for t in ts)
        # 2024-04-15 UTC midnight
        import datetime as _dt
        expect = _dt.datetime(2024, 4, 15, tzinfo=_dt.timezone.utc).timestamp()
        assert ts[0] == pytest.approx(expect)

    def test_mixed_modality_and_masks(self):
        assets = [
            _optical("2024-03-01", band="red", ref="ref:raster/o-red"),
            _optical("2024-03-01", band="nir", ref="ref:raster/o-nir"),
            _sar("2024-03-04", pol="vv"),
            _sar("2024-03-04", pol="vh"),
            {"ref": "ref:raster/scl", "time_iso": "2024-03-01",
             "role": "cloud_mask"},
        ]
        d = rcd.build_cube_descriptor(cube_id="cube-mix", grid=_grid(), assets=assets)
        assert d.modalities() == {"optical", "sar", "cloud_mask"}
        assert d.n_observation_assets == 4  # masks 不算观测
        # 同一时刻同角色不同 band 是合法多波段；时间步是唯一时刻集合
        assert len(d.times_sec) == 2

    def test_coverage_summary_counts_gaps_by_code(self):
        assets = [
            _optical("2024-03-01", gap_code=None),
            _optical("2024-04-01", gap_code="cloud",
                     quality_fraction=0.2),
            _sar("2024-03-03", pol="vv", gap_code="layover"),
            _sar("2024-04-03", pol="vv"),
        ]
        d = rcd.build_cube_descriptor(cube_id="cube-gap", grid=_grid(), assets=assets)
        s = d.coverage_summary()
        assert s["gap_counts"]["cloud"] == 1
        assert s["gap_counts"]["layover"] == 1
        assert s["n_assets_by_role"] == {"optical": 2, "sar": 2}
        assert s["n_valid_observation_assets"] == 2
        assert s["gap_ratio"] == pytest.approx(2 / 4)

    def test_to_context_summary_is_bounded_scalar_payload(self):
        assets = [_optical(f"2024-{m:02d}-01") for m in range(1, 13)]
        assets += [_sar(f"2024-{m:02d}-05") for m in range(1, 13)]
        d = rcd.build_cube_descriptor(cube_id="cube-big", grid=_grid(), assets=assets)
        ctx = d.to_context_summary(max_assets=6)
        # 有界：资产样本 ≤ max_assets；无任何嵌套数组/矩阵 payload
        assert len(ctx["assets_sample"]) == 6
        assert ctx["n_assets"] == 24
        assert ctx["time_start"].startswith("2024-01-01")
        assert ctx["time_end"].startswith("2024-12-05")
        def _scalar_only(x):
            if x is None or isinstance(x, (str, int, float, bool)):
                return True
            if isinstance(x, (list, tuple)):
                return all(
                    y is None or isinstance(y, (str, int, float, bool))
                    for y in x)
            if isinstance(x, dict):
                return all(_scalar_only(v) for v in x.values())
            return False

        for k, v in ctx.items():
            if isinstance(v, list):
                assert all(_scalar_only(x) for x in v), k
            else:
                assert _scalar_only(v), k


class TestDescriptorValidation:
    def test_empty_assets_rejected(self):
        with pytest.raises(ValidationError):
            rcd.build_cube_descriptor(cube_id="c", grid=_grid(), assets=[])

    def test_unsorted_assets_canonicalized_with_disclosure(self):
        # 描述符是元数据（无行序数据语义）：乱序资产规范化排序 + 显式披露；
        # 数据栈层的"不静默重排"红线仍在 temporal_cube.build_cube。
        assets = [_optical("2024-05-01"), _optical("2024-03-01")]
        d = rcd.build_cube_descriptor(cube_id="c", grid=_grid(), assets=assets)
        assert d.times_sec == tuple(sorted(d.times_sec))
        assert any("排序" in s for s in d.disclosures)

    def test_duplicate_observation_rejected(self):
        assets = [_optical("2024-03-01", band="nir", ref="ref:raster/a"),
                  _optical("2024-03-01", band="nir", ref="ref:raster/b")]
        with pytest.raises(ValidationError, match="重复|duplicate"):
            rcd.build_cube_descriptor(cube_id="c", grid=_grid(), assets=assets)

    def test_duplicate_ref_rejected(self):
        assets = [_optical("2024-03-01", ref="ref:raster/same"),
                  _optical("2024-04-01", ref="ref:raster/same")]
        with pytest.raises(ValidationError, match="ref"):
            rcd.build_cube_descriptor(cube_id="c", grid=_grid(), assets=assets)

    def test_optical_requires_known_band_role(self):
        with pytest.raises(ValidationError, match="band|角色"):
            rcd.build_cube_descriptor(
                cube_id="c", grid=_grid(),
                assets=[{"ref": "r", "time_iso": "2024-03-01",
                         "role": "optical", "band": "b04"}])  # 位置名不是语义角色

    def test_sar_requires_polarization_vocab(self):
        with pytest.raises(ValidationError):
            rcd.build_cube_descriptor(
                cube_id="c", grid=_grid(),
                assets=[{"ref": "r", "time_iso": "2024-03-01",
                         "role": "sar", "polarization": "xx"}])

    def test_sar_without_polarization_rejected(self):
        with pytest.raises(ValidationError):
            rcd.build_cube_descriptor(
                cube_id="c", grid=_grid(),
                assets=[{"ref": "r", "time_iso": "2024-03-01", "role": "sar"}])

    def test_unknown_role_rejected(self):
        with pytest.raises(ValidationError, match="role"):
            rcd.build_cube_descriptor(
                cube_id="c", grid=_grid(),
                assets=[{"ref": "r", "time_iso": "2024-03-01", "role": "dem"}])

    def test_unknown_gap_code_rejected(self):
        with pytest.raises(ValidationError, match="gap"):
            rcd.build_cube_descriptor(
                cube_id="c", grid=_grid(),
                assets=[_optical("2024-03-01", gap_code="sorta_cloudy")])

    def test_quality_fraction_range(self):
        with pytest.raises(ValidationError):
            rcd.build_cube_descriptor(
                cube_id="c", grid=_grid(),
                assets=[_optical("2024-03-01", quality_fraction=1.5)])

    def test_slice_bound(self, monkeypatch):
        monkeypatch.setattr(rcd, "CUBE_DESCRIPTOR_MAX_ASSETS", 4)
        assets = [_optical(f"2024-0{m}-01") for m in range(1, 6)]
        with pytest.raises(ValidationError, match="上限|bound|512|4"):
            rcd.build_cube_descriptor(cube_id="c", grid=_grid(), assets=assets)

    def test_bad_cube_id_rejected(self):
        with pytest.raises(ValidationError):
            rcd.build_cube_descriptor(cube_id="bad id!", grid=_grid(),
                                      assets=[_optical("2024-03-01")])

    def test_bad_time_string_rejected(self):
        with pytest.raises(ValidationError):
            rcd.build_cube_descriptor(cube_id="c", grid=_grid(),
                                      assets=[_optical("03/01/2024")])

    def test_bad_grid_rejected(self):
        with pytest.raises(ValidationError):
            rcd.build_cube_descriptor(cube_id="c", grid=_grid(width=0),
                                      assets=[_optical("2024-03-01")])

    def test_bad_transform_length_rejected(self):
        with pytest.raises(ValidationError):
            rcd.build_cube_descriptor(
                cube_id="c", grid=_grid(transform=(1.0, 2.0, 3.0)),
                assets=[_optical("2024-03-01")])


class TestTimeSemantics:
    def test_date_only_is_utc_midnight_deterministic(self):
        a = rcd.parse_time_iso("2024-03-01")
        b = rcd.parse_time_iso("2024-03-01T00:00:00+00:00")
        assert a == b

    def test_aware_time_converted_to_utc(self):
        t = rcd.parse_time_iso("2024-03-01T12:00:00+08:00")
        import datetime as _dt
        expect = _dt.datetime(2024, 3, 1, 4, 0, tzinfo=_dt.timezone.utc).timestamp()
        assert t == pytest.approx(expect)

    def test_naive_time_assumed_utc_and_disclosed(self):
        t = rcd.parse_time_iso("2024-03-01T06:00:00")
        import datetime as _dt
        expect = _dt.datetime(2024, 3, 1, 6, 0, tzinfo=_dt.timezone.utc).timestamp()
        assert t == pytest.approx(expect)
        assert any("UTC" in s for s in rcd.naive_time_disclosures(["2024-03-01T06:00:00"]))


class TestVocabAlignment:
    def test_role_vocab_mirrors_lakehouse(self):
        from app.services.lakehouse.rs_cube import RS_ROLES

        assert set(rcd.CUBE_SOURCE_ROLES) == set(RS_ROLES)

    def test_band_roles_from_spectral_layer(self):
        from app.lib.geo_analysis.spectral import BAND_ROLES

        for band in ("red", "nir", "swir1", "vv", "vh"):
            assert band in BAND_ROLES

    def test_gap_code_vocabulary_closed(self):
        assert rcd.GAP_CODES == frozenset({
            "missing_acquisition", "cloud", "cloud_shadow", "nodata",
            "layover", "sar_shadow", "off_grid", "below_quality",
            "unregistered",
        })
