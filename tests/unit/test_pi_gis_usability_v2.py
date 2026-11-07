"""V2 locks: bbox geography honesty and explicit publication constraint layout.

These tests reproduce the leftovers confirmed on master after #1989.
They do not weaken an existing assertion.
"""
from __future__ import annotations

from app.lib.cartography.layout_solver import LayoutParticipantV4
from app.lib.cartography.publication_constraint_layout import (
    evaluate_constraint_layout,
    prepare_publication_document,
    resolve_cartographer_layout,
)
from app.services.gis_harness.scope_ref_guard import (
    product_scope_geography,
    scope_geography_verdict,
    scope_ref_mismatch,
)
from app.services.mapspec_to_svg import _render_chrome_groups


BEIJING = [115.4, 39.4, 117.5, 41.1]
CHENGDU = [103.9, 30.5, 104.3, 30.8]


def test_name_mismatch_still_rejects_without_bbox():
    reason = scope_ref_mismatch("北京市", features=[{
        "type": "Feature",
        "properties": {"cityname": "成都市"},
    }])
    assert reason is not None
    verdict = scope_geography_verdict(
        "北京市",
        features=[{"type": "Feature", "properties": {"cityname": "成都市"}}],
        scope_bbox=BEIJING,
    )
    assert verdict["verdict"] == "mismatched"
    assert verdict["evidence"] == "name"


def test_missing_names_do_not_invent_a_mismatch():
    assert scope_ref_mismatch("北京市", features=[{
        "type": "Feature",
        "properties": {"name": "无城市字段"},
    }]) is None
    verdict = scope_geography_verdict(
        "北京市",
        features=[{"type": "Feature", "properties": {"name": "无城市字段"}}],
    )
    assert verdict == {
        "verdict": "unverified",
        "reason": "scope_unverified",
        "evidence": "none",
    }


def test_disjoint_bbox_rejects_when_names_are_absent():
    verdict = scope_geography_verdict(
        "北京市",
        descriptor={"bbox": CHENGDU},
        scope_bbox=BEIJING,
    )
    assert verdict["verdict"] == "mismatched"
    assert verdict["evidence"] == "bbox"
    assert verdict["reason"].startswith("scope_bbox_disjoint:")


def test_contained_bbox_matches_and_partial_overlap_stays_unverified():
    inside = scope_geography_verdict(
        "北京市",
        descriptor={"bbox": [116.3, 39.9, 116.5, 40.0]},
        scope_bbox=BEIJING,
    )
    assert inside["verdict"] == "matched"
    assert inside["evidence"] == "bbox"
    national = scope_geography_verdict(
        "北京市",
        descriptor={"bbox": [73.0, 18.0, 135.0, 54.0]},
        scope_bbox=BEIJING,
    )
    assert national["verdict"] == "unverified"
    assert national["evidence"] == "bbox"


def test_degree_crs_aliases_still_reject_a_different_city():
    point = {
        "type": "Feature",
        "properties": {"name": "学校"},
        "geometry": {"type": "Point", "coordinates": [104.06, 30.67]},
    }
    for crs in ("gcj02", "EPSG:4490", "CRS:84", "urn:ogc:def:crs:EPSG::4326"):
        verdict = scope_geography_verdict(
            "北京市",
            descriptor={"crs": crs},
            features=[point],
            scope_bbox=BEIJING,
        )
        assert verdict["verdict"] == "mismatched", crs
        assert verdict["evidence"] == "bbox"


def test_feature_sample_without_crs_does_not_invent_a_mismatch():
    verdict = scope_geography_verdict(
        "北京市",
        features=[{
            "type": "Feature",
            "properties": {"name": "local-grid"},
            "geometry": {"type": "Point", "coordinates": [12.0, 8.0]},
        }],
        scope_bbox=BEIJING,
    )
    assert verdict["verdict"] == "unverified"


def test_projected_crs_is_not_compared_as_lonlat():
    verdict = scope_geography_verdict(
        "北京市",
        descriptor={"crs": "EPSG:3857", "bbox": CHENGDU},
        scope_bbox=BEIJING,
    )
    assert verdict["verdict"] == "unverified"
    assert verdict["evidence"] == "none"


def test_admin_lookup_runs_only_without_place_tokens():
    calls = []

    def lookup(name: str):
        calls.append(name)
        return BEIJING

    named = product_scope_geography(
        "北京市",
        descriptor={"district": "成都市"},
        scope_bbox_lookup=lookup,
    )
    assert named["verdict"] == "mismatched"
    assert named["evidence"] == "name"
    assert calls == []

    bare = product_scope_geography(
        "北京市",
        descriptor={"bbox": CHENGDU},
        scope_bbox_lookup=lookup,
    )
    assert calls == ["北京市"]
    assert bare["verdict"] == "mismatched"
    assert bare["evidence"] == "bbox"


def test_admin_lookup_failure_is_unverified_not_a_mismatch():
    def boom(_name: str):
        raise RuntimeError("admin db down")

    verdict = product_scope_geography(
        "北京市",
        descriptor={"bbox": CHENGDU},
        scope_bbox_lookup=boom,
    )
    assert verdict["verdict"] == "unverified"


def test_default_publication_document_is_not_rewritten():
    document = {
        "version": "1.2",
        "layout": {"components": [{
            "id": "legend-1",
            "type": "legend",
            "position": "bottom-left",
            "enabled": True,
        }]},
    }
    same, report = prepare_publication_document(document, page_profile=None)
    assert same is document
    assert report["constraint_layout"] == "not_evaluated"
    unsupported, report = prepare_publication_document(
        document, page_profile="a3_landscape",
    )
    assert unsupported is document
    assert report["reason"] == "profile_unsupported"


def test_explicit_a4_landscape_evaluates_existing_solver():
    document = {
        "version": "1.2",
        "layout": {"components": [
            {"id": "title-1", "type": "title", "position": "top-center", "optional": False},
            {"id": "legend-1", "type": "legend", "position": "bottom-left"},
            {"id": "scale-1", "type": "scale_bar", "position": "bottom-right"},
        ]},
    }
    cloned, report = prepare_publication_document(
        document, page_profile="a4_landscape",
    )
    assert cloned is not document
    assert document["layout"].get("constraint_layout") is None
    assert cloned["layout"]["constraint_layout"] == "evaluated"
    assert report["constraint_layout"] == "evaluated"
    assert report["page_profile"] == "a4_landscape"
    assert {item["id"] for item in report["placements"]} >= {"title-1", "legend-1"}
    absent = evaluate_constraint_layout(document["layout"]["components"])
    assert absent["constraint_layout"] == "not_evaluated"


def test_key_notes_past_the_disclosure_cap_stay_enabled():
    components = [
        {
            "id": f"ann-{index:02d}",
            "type": "annotation",
            "position": "top-left",
            "enabled": True,
            "optional": True,
        }
        for index in range(32)
    ]
    cloned, report = prepare_publication_document(
        {"version": "1.2", "layout": {"components": components}},
        page_profile="a4_landscape",
    )
    enabled = {
        comp["id"]: comp.get("enabled", True)
        for comp in cloned["layout"]["components"]
    }
    assert all(enabled[f"ann-{index:02d}"] for index in range(32))
    assert len(report["key_component_ids"]) == 32


def test_subtitle_move_does_not_drag_the_title():
    spec = {"layout": {
        "constraint_layout": "evaluated",
        "components": [
            {
                "id": "title-1",
                "type": "title",
                "enabled": True,
                "position": "top-center",
                "options": {"text": "成都小学"},
            },
            {
                "id": "sub-1",
                "type": "subtitle",
                "enabled": True,
                "position": "bottom-left",
                "options": {"text": "副题"},
            },
        ],
    }}
    svg, _coverage = _render_chrome_groups(
        spec, [104.0, 30.0, 105.0, 31.0], 800.0, 600.0, lambda _pt: (0.0, 0.0),
    )
    assert 'font-size="22"' in svg and 'y="42"' in svg
    assert 'font-size="12"' in svg
    assert 'y="62"' not in svg


def test_moved_legend_box_stays_on_the_page():
    entries = [{"label": f"c{i}", "color": "#111111"} for i in range(8)]
    spec = {
        "layers": [{
            "id": "layer-1",
            "type": "circle",
            "legend_spec": {"title": "图例", "entries": entries},
        }],
        "layout": {
            "constraint_layout": "evaluated",
            "components": [{
                "id": "legend-1",
                "type": "legend",
                "enabled": True,
                "position": "bottom-center",
                "options": {"layerId": "layer-1"},
            }],
        },
    }
    svg, _coverage = _render_chrome_groups(
        spec, [104.0, 30.0, 105.0, 31.0], 800.0, 1123.0, lambda _pt: (0.0, 0.0),
    )
    assert 'class="chrome-legend"' in svg
    y = float(svg.split('y="', 1)[1].split('"', 1)[0])
    height = float(svg.split('height="', 1)[1].split('"', 1)[0])
    assert y >= 24.0
    assert y + height <= 1123.0 - 24.0


def test_evaluated_title_anchor_moves_vertically():
    spec = {"layout": {
        "constraint_layout": "evaluated",
        "components": [
            {
                "id": "title-1",
                "type": "title",
                "enabled": True,
                "position": "bottom-center",
                "options": {"text": "成都小学"},
            },
            {
                "id": "sub-1",
                "type": "subtitle",
                "enabled": True,
                "position": "bottom-center",
                "options": {"text": "副题"},
            },
        ],
    }}
    svg, _coverage = _render_chrome_groups(
        spec, [104.0, 30.0, 105.0, 31.0], 800.0, 600.0, lambda _pt: (0.0, 0.0),
    )
    assert 'y="42"' not in svg
    assert "chrome-title" in svg


def test_screen_cartographer_does_not_claim_constraint_layout():
    parts = [LayoutParticipantV4(
        id="comp-legend", type="legend", requested_zone="top-right", optional=False,
    )]
    solution, report = resolve_cartographer_layout(parts, "screen_16_9")
    assert solution.page_profile == "viewport"
    assert report["constraint_layout"] == "not_evaluated"
    printed, printed_report = resolve_cartographer_layout(parts, "a4_landscape")
    assert printed.page_profile == "a4_landscape"
    assert printed_report["constraint_layout"] == "evaluated"


def test_default_chrome_coordinates_honor_component_anchor():
    """G04（#2000）：组件锚点是 chrome 挪位的唯一门（不再依赖全局标记）。"""
    spec = {"layout": {"components": [{
        "id": "north-1",
        "type": "north_arrow",
        "enabled": True,
        "position": "bottom-left",
    }]}}
    svg, _coverage = _render_chrome_groups(
        spec, [104.0, 30.0, 105.0, 31.0], 800.0, 600.0, lambda _pt: (0.0, 0.0),
    )
    historical = {"layout": {"components": [{
        "id": "north-1",
        "type": "north_arrow",
        "enabled": True,
        "position": "top-right",
    }]}}
    historical_svg, _coverage = _render_chrome_groups(
        historical, [104.0, 30.0, 105.0, 31.0], 800.0, 600.0, lambda _pt: (0.0, 0.0),
    )
    assert "chrome-north-arrow" in svg
    # 用户/求解写入的非历史锚点被导出尊重（挪到底部），历史锚点保持默认坐标
    assert svg != historical_svg
    # 全局 constraint_layout 标记不再改变输出（同锚点同字节）
    with_marker = {
        "layout": {
            "constraint_layout": "evaluated",
            "page_profile": "a4_landscape",
            "components": spec["layout"]["components"],
        },
    }
    marker_svg, _coverage = _render_chrome_groups(
        with_marker, [104.0, 30.0, 105.0, 31.0], 800.0, 600.0, lambda _pt: (0.0, 0.0),
    )
    assert svg == marker_svg
