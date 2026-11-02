"""atlas time driver 测试（GOAL 03 M6 —— 时序图册导出）。

覆盖：
- temporal 契约 → 逐真实帧分页（页计数 = 窗口内真值；帧 cursor 盖章）；
- 无 temporal / 单期 → typed 拒绝（不做假图册）；
- manifest 时间证据（atlas.temporal 块 + 页 time_window）；
- 时间注记进入 SVG（单帧导出时间证据）。
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.lib.cartography.atlas_job_spec import AtlasJobSpec
from app.lib.cartography.publication_ir import (
    AtlasPolicy,
    MapSpecSchemaError,
    plan_publication_pages,
)
from app.services.atlas_batch.planner import plan_atlas_binding


def _temporal_doc(years: List[int]) -> Dict[str, Any]:
    """3 年学校点（year_int）+ temporal 契约。"""
    feats = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.6]},
            "properties": {"year": y, "name": f"school-{i}"},
        }
        for i, y in enumerate(years)
    ]
    frames = [
        {
            "id": f"f-{y}", "label": str(y),
            "start": f"{y}-01-01T00:00:00Z", "end": f"{y + 1}-01-01T00:00:00Z",
            "feature_count": years.count(y),
        }
        for y in sorted(set(years))
    ]
    return {
        "version": "1.8",
        "layers": [{"id": "schools", "type": "circle", "source": "s1"}],
        "sources": {
            "s1": {"type": "geojson", "inlineData": {
                "type": "FeatureCollection", "features": feats}}
        },
        "temporal": {
            "schema_version": "temporal_view.v1",
            "enabled": True,
            "layers": [{"layer_id": "schools", "source_ref": "ref:schools"}],
            "time_field": "year", "time_semantics": "snapshot",
            "time_representation": "year_int", "timezone": "UTC",
            "granularity": "year",
            "extent": [f"{min(years)}-01-01T00:00:00Z",
                       f"{max(years) + 1}-01-01T00:00:00Z"],
            "frames": frames,
            "cursor_frame": frames[-1]["id"],
            "playback": {"playing": False, "rate": 1.0},
            "source_ref": "ref:schools", "content_revision": 1,
        },
    }


def _time_spec(**policy_overrides) -> AtlasJobSpec:
    params: Dict[str, Any] = {
        "session_id": "sess-atlas-time-0001",
        "atlas": {"driver": "time", "atlas_title": "逐年学校图册", **policy_overrides},
    }
    return AtlasJobSpec(**params)


class TestTimeDriverPlanning:
    def test_pages_per_real_frame(self):
        doc = _temporal_doc([2021, 2021, 2022, 2023, 2023, 2023])
        ir = plan_publication_pages(doc, atlas=AtlasPolicy(driver="time"))
        content = [p for p in ir.pages if not p.cover]
        assert [p.page_id for p in content] == ["t-f-2021", "t-f-2022", "t-f-2023"]
        assert [p.title for p in content] == ["2021", "2022", "2023"]
        # 每页时间窗证据齐全。
        for p in content:
            tw = p.time_window
            assert tw["time_field"] == "year"
            assert tw["time_representation"] == "year_int"
            assert tw["layer_id"] == "schools"

    def test_no_temporal_refused(self):
        doc = _temporal_doc([2021, 2022])
        del doc["temporal"]
        with pytest.raises(MapSpecSchemaError) as ei:
            plan_publication_pages(doc, atlas=AtlasPolicy(driver="time"))
        assert ei.value.code == "atlas_time_no_temporal"

    def test_single_period_refused(self):
        doc = _temporal_doc([2023, 2023])
        with pytest.raises(MapSpecSchemaError, match="帧数 < 2"):
            plan_publication_pages(doc, atlas=AtlasPolicy(driver="time"))

    def test_page_budget_truncation_disclosed(self):
        doc = _temporal_doc([2021, 2022, 2023])
        ir = plan_publication_pages(doc, atlas=AtlasPolicy(driver="time", page_budget=2))
        assert len([p for p in ir.pages if not p.cover]) == 2
        assert any(d["code"] == "atlas_truncated" for d in ir.degradations)


class TestTimeDriverBinding:
    def test_binding_counts_are_window_truth(self):
        """页 feature_count = 窗口内真值（2021 页=2、2023 页=3）—— 不是全量。"""
        doc = _temporal_doc([2021, 2021, 2022, 2023, 2023, 2023])
        spec = _time_spec()
        plan = plan_atlas_binding(spec, doc)
        counts = {b.page_id: b.feature_count for b in plan.bindings if not b.cover}
        assert counts == {"t-f-2021": 2, "t-f-2022": 1, "t-f-2023": 3}
        # 每页 data_digest 不同（内容真实差异，非重复页）。
        digests = {b.page_id: b.data_digest for b in plan.bindings if not b.cover}
        assert len(set(digests.values())) == 3
        # 时间窗随绑定（manifest 证据源）。
        assert all(b.time_window and b.time_window["frame_id"].startswith("f-")
                   for b in plan.bindings if not b.cover)

    def test_effective_doc_cursor_stamp(self):
        """页有效文档 temporal.cursor = 本页帧（SVG 时间注记随页）。"""
        from app.services.gis_harness.temporal_products.time_filter import (
            apply_time_window_to_doc,
        )
        doc = _temporal_doc([2021, 2022])
        tw = {"frame_id": "f-2021", "label": "2021",
              "start": "2021-01-01T00:00:00Z", "end": "2022-01-01T00:00:00Z",
              "time_field": "year", "time_representation": "year_int",
              "granularity": "year", "layer_id": "schools"}
        out = apply_time_window_to_doc(doc, tw)
        feats = out["sources"]["s1"]["inlineData"]["features"]
        assert [f["properties"]["year"] for f in feats] == [2021]
        assert out["temporal"]["cursor_frame"] == "f-2021"
        # 输入不被动。
        assert len(doc["sources"]["s1"]["inlineData"]["features"]) == 2
        assert doc["temporal"]["cursor_frame"] == "f-2022"


class TestSvgTimeAnnotation:
    def test_temporal_note_in_svg(self):
        """temporal 启用 → SVG 副标题含当前帧时间证据。"""
        from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

        doc = _temporal_doc([2021, 2022])
        res = compile_mapspec_to_svg_detailed(
            doc, target_dpi=96, width=800, height=600, padding=24,
            include_chrome=True,
        )
        svg = res.svg
        assert "时间: 2022" in svg
        assert "窗口 [2022-01-01T00:00:00Z ~ 2023-01-01T00:00:00Z）" in svg

    def test_no_temporal_no_note(self):
        from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

        doc = _temporal_doc([2021, 2022])
        del doc["temporal"]
        res = compile_mapspec_to_svg_detailed(
            doc, target_dpi=96, width=800, height=600, padding=24,
            include_chrome=True,
        )
        assert "时间:" not in res.svg
