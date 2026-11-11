"""G15 WP04 —— 画幅有效文档解析器契约测试。

不变量锚点（任务书 §3/§5）：
- 相邻画幅互不污染；重复解析逐位一致（指纹相等）；
- 帧显式时间窗/where 优先，全局 temporal cursor 不参与帧解析；
- 共享分级只写共享图例画幅，breaks 与帧内数据子集无关；
- 输入文档不可变（解析后原 deep-equal）。
"""
from __future__ import annotations

import copy

import pytest

from app.services.gis_harness.frame_products.resolver import (
    FrameResolutionError,
    resolve_effective_bounds,
    resolve_frame_document,
)

pytestmark = pytest.mark.unit


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _f(name, year, value, geom=None):
    return {
        "type": "Feature",
        "properties": {"name": name, "year": year, "value": value},
        "geometry": geom or {"type": "Point", "coordinates": [104.0, 30.6]},
    }


def _document() -> dict:
    """三帧产品主文档：两年时间窗 + 一个类别帧。"""
    feats = [
        _f("jinjiang", 2022, 3.0), _f("wuhou", 2022, 7.0),
        _f("jinjiang", 2024, 5.0), _f("wuhou", 2024, 9.0),
        _f("gaoxin", 2024, 11.0),
    ]
    return {
        "version": "2.0",
        "view": {"center": [104.0, 30.6], "zoom": 10},
        "sources": {"src-districts": {"type": "geojson", "inlineData": _fc(feats)}},
        "layers": [{
            "id": "lyr-districts", "type": "fill", "source": "src-districts",
            "legend_spec": {"type": "graduated", "field": "value",
                            "breaks": [0.0, 4.0, 8.0, 12.0],
                            "labels": ["0 – 4", "4 – 8", "8 – 12"],
                            "palette": "Blues", "method": "quantiles", "k": 3},
        }],
        "layout": {
            "frames": [
                {"id": "f-2022", "title": "2022", "extent": [103.9, 30.5, 104.2, 30.8]},
                {"id": "f-2024", "title": "2024", "extent": [103.9, 30.5, 104.2, 30.8]},
                {"id": "f-cat", "title": "高新", "extent": [103.9, 30.5, 104.2, 30.8]},
            ],
        },
        "frame_product": {
            "schema_version": "frame_product.v1",
            "enabled": True,
            "product_kind": "time_series",
            "grid": {"columns": 3, "title": "成都两年指标"},
            "frames": [
                {"frame_id": "f-2022", "legend": "shared",
                 "data": {"ref": "ref:ind", "layer_id": "lyr-districts",
                          "field": "value", "content_revision": 3,
                          "time_window": {
                              "time_field": "year", "time_representation": "year_int",
                              "granularity": "year", "timezone": "UTC",
                              "layer_id": "lyr-districts",
                              "start": "2022-01-01T00:00:00Z",
                              "end": "2023-01-01T00:00:00Z"}}},
                {"frame_id": "f-2024", "legend": "shared",
                 "data": {"ref": "ref:ind", "layer_id": "lyr-districts",
                          "field": "value", "content_revision": 3,
                          "time_window": {
                              "time_field": "year", "time_representation": "year_int",
                              "granularity": "year", "timezone": "UTC",
                              "layer_id": "lyr-districts",
                              "start": "2024-01-01T00:00:00Z",
                              "end": "2025-01-01T00:00:00Z"}}},
                {"frame_id": "f-cat", "legend": "independent",
                 "data": {"ref": "ref:ind", "layer_id": "lyr-districts",
                          "field": "value", "content_revision": 3,
                          "where": {"op": "eq", "field": "name", "value": "gaoxin"}}},
            ],
            "shared_expression": {
                "field": "value", "semantics": "other",
                "method": "quantiles", "k": 3, "palette": "Blues",
                "breaks": [0.0, 5.0, 10.0, 12.0],
                "domain_status": "complete",
                "per_frame_counts": {"f-2022": 2, "f-2024": 3},
                "sample_size": 5,
                "eligibility": ["union_breaks"],
            },
        },
    }


class TestFrameIsolation:
    def test_time_windows_do_not_cross_pollute(self):
        doc = _document()
        r22 = resolve_frame_document(doc, "f-2022")
        r24 = resolve_frame_document(doc, "f-2024")
        feats22 = r22.document["sources"]["src-districts"]["inlineData"]["features"]
        feats24 = r24.document["sources"]["src-districts"]["inlineData"]["features"]
        assert [f["properties"]["year"] for f in feats22] == [2022, 2022]
        assert sorted(f["properties"]["year"] for f in feats24) == [2024, 2024, 2024]

    def test_input_document_immutable(self):
        doc = _document()
        before = copy.deepcopy(doc)
        resolve_frame_document(doc, "f-2022")
        resolve_frame_document(doc, "f-cat")
        assert doc == before

    def test_repeated_resolution_identical_fingerprint(self):
        doc = _document()
        a = resolve_frame_document(doc, "f-2022")
        b = resolve_frame_document(doc, "f-2022")
        assert a.fingerprint == b.fingerprint
        assert a.document == b.document

    def test_where_filter_slices_bound_layer(self):
        doc = _document()
        res = resolve_frame_document(doc, "f-cat")
        feats = res.document["sources"]["src-districts"]["inlineData"]["features"]
        assert [f["properties"]["name"] for f in feats] == ["gaoxin"]
        assert res.filtered_feature_count == 1
        assert "where_filter" in res.applied

    def test_missing_frame_refused_typed(self):
        with pytest.raises(FrameResolutionError) as ei:
            resolve_frame_document(_document(), "ghost")
        assert ei.value.code == "frame_not_found"

    def test_dangling_layer_refused_not_silent(self):
        doc = _document()
        doc["frame_product"]["frames"][0]["data"]["layer_id"] = "nope"
        with pytest.raises(FrameResolutionError) as ei:
            resolve_frame_document(doc, "f-2022")
        assert ei.value.code == "frame_layer_unavailable"


class TestSharedExpression:
    def test_shared_breaks_applied_to_shared_frames_only(self):
        doc = _document()
        r22 = resolve_frame_document(doc, "f-2022")
        rcat = resolve_frame_document(doc, "f-cat")
        legend22 = r22.document["layers"][0]["legend_spec"]
        assert legend22["breaks"] == [0.0, 5.0, 10.0, 12.0]
        assert legend22["shared_scope"] is True
        assert legend22["labels"] == ["0 – 5", "5 – 10", "10 – 12"]
        # 独立图例帧：分级不被共享域覆写。
        legend_cat = rcat.document["layers"][0]["legend_spec"]
        assert legend_cat["breaks"] == [0.0, 4.0, 8.0, 12.0]
        assert "shared_scope" not in legend_cat

    def test_independent_frame_subset_does_not_reclassify_shared(self):
        """2022 帧只有 2 个要素 —— 共享 breaks 不随帧子集漂移（统一分级只算一次）。"""
        doc = _document()
        r22 = resolve_frame_document(doc, "f-2022")
        assert r22.document["layers"][0]["legend_spec"]["breaks"] == [0.0, 5.0, 10.0, 12.0]

    def test_uncomputed_shared_expression_disclosed(self):
        doc = _document()
        doc["frame_product"]["shared_expression"]["breaks"] = None
        doc["frame_product"]["shared_expression"]["domain_status"] = "unknown"
        res = resolve_frame_document(doc, "f-2022")
        assert res.document["layers"][0]["legend_spec"]["breaks"] == [0.0, 4.0, 8.0, 12.0]
        assert "shared_expression_not_computed" in res.disclosures


class TestFrameOverrides:
    def test_layer_overrides_merge_per_key(self):
        doc = _document()
        doc["layout"]["frames"][0]["layerOverrides"] = {
            "lyr-districts": {"opacity": 0.4},
        }
        res = resolve_frame_document(doc, "f-2022")
        layer = res.document["layers"][0]
        assert layer["opacity"] == 0.4
        # 未提及键保留（type/source/legend_spec 原样）。
        assert layer["type"] == "fill" and layer["source"] == "src-districts"

    def test_atlas_only_mode_ignores_product(self):
        doc = _document()
        res = resolve_frame_document(doc, "f-2022", apply_product_semantics=False)
        # 无时间窗过滤 —— 全量要素（atlas 旧语义）。
        feats = res.document["sources"]["src-districts"]["inlineData"]["features"]
        assert len(feats) == 5
        assert res.document["layers"][0]["legend_spec"]["breaks"] == [0.0, 4.0, 8.0, 12.0]


class TestBounds:
    def test_extent_preferred_over_view(self):
        assert resolve_effective_bounds(
            {"extent": [1.0, 2.0, 3.0, 4.0], "view": {"center": [0, 0], "zoom": 5}}
        ) == [1.0, 2.0, 3.0, 4.0]

    def test_invalid_extent_rejected(self):
        assert resolve_effective_bounds({"extent": [3.0, 2.0, 1.0, 4.0]}) is None

    def test_view_converts_like_publication(self):
        b = resolve_effective_bounds({"view": {"center": [104.0, 30.0], "zoom": 2.0}})
        assert b is not None
        # span = 360/2^2 = 90 → [104-45, 104+45]
        assert b[0] == pytest.approx(59.0) and b[2] == pytest.approx(149.0)
