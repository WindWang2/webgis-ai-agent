"""RenderWorkProjection 契约测试（F13，ADR-0214 D1）。

锁定：确定性、单调性、revision/fingerprint 绑定、缺 profile 的诚实披露、
unsupported 披露、空图安全、有界 receipt 形状。
"""

import pytest

from app.lib.cartography.layer_capability import (
    LAYER_TYPE_SUPPORT,
    disclose_unsupported,
)
from app.lib.cartography.render_work_projection import (
    DEFAULT_CANVAS_HEIGHT,
    DEFAULT_CANVAS_WIDTH,
    PROJECTION_SCHEMA_VERSION,
    UNKNOWN_FEATURES_PER_LAYER,
    project_render_work,
    RenderWorkProjection,
)
from app.services.governor.render_budget import (
    estimate_render,
    render_work_units,
)


def _spec(**overrides):
    base = {
        "version": "1.2",
        "sources": {
            "src-a": {
                "type": "geojson",
                "ref": "ref:abc",
                "ref_id": "ref:abc",
                "profile": {"featureCount": 1200},
            },
        },
        "layers": [
            {"id": "l1", "source": "src-a", "type": "fill"},
            {"id": "l2", "source": "src-a", "type": "line"},
        ],
        "layout": {"components": []},
    }
    base.update(overrides)
    return base


@pytest.mark.cartography
class TestRenderWorkProjectionContract:
    def test_schema_version_and_revision_binding(self):
        proj = project_render_work(
            _spec(), revision=7, fingerprint="fp-sha256:deadbeef"
        )
        assert proj.schema_version == PROJECTION_SCHEMA_VERSION
        assert proj.mapspec_revision == 7
        assert proj.mapspec_fingerprint == "fp-sha256:deadbeef"

    def test_deterministic_same_spec_same_projection(self):
        spec = _spec()
        a = project_render_work(spec, revision=3, fingerprint="fp-x")
        b = project_render_work(dict(spec), revision=3, fingerprint="fp-x")
        assert a.to_dict() == b.to_dict()
        assert render_work_units(a.work_input) == render_work_units(b.work_input)

    def test_counts_from_skeleton_only(self):
        proj = project_render_work(_spec())
        wi = proj.work_input
        # 同源多（子）层共享源计数 —— 不按层重复累加
        assert wi.feature_count == 1200
        assert wi.layer_count == 2
        assert wi.source_count == 1
        assert proj.layers_total == 2
        assert proj.layers_visible == 2
        assert proj.features_estimated is False

    def test_hidden_layers_excluded_from_work_but_counted_total(self):
        spec = _spec(
            layers=[
                {"id": "l1", "source": "src-a", "type": "fill"},
                {"id": "l2", "source": "src-a", "type": "line",
                 "visible": False},
            ]
        )
        proj = project_render_work(spec)
        assert proj.layers_total == 2
        assert proj.layers_visible == 1
        assert proj.work_input.layer_count == 1

    def test_missing_profile_estimates_with_disclosure(self):
        spec = _spec(
            sources={"src-a": {"type": "geojson"}},
        )
        proj = project_render_work(spec)
        assert proj.features_estimated is True
        assert proj.work_input.feature_count == UNKNOWN_FEATURES_PER_LAYER
        assert any("estimated" in n for n in proj.notes)

    def test_labels_count_with_zoom_bands(self):
        spec = _spec(
            layers=[
                {
                    "id": "l1", "source": "src-a", "type": "symbol",
                    "label": {
                        "field": "name",
                        "zoomBands": [
                            {"minZoom": 5, "maxZoom": 9},
                            {"minZoom": 9, "maxZoom": 12},
                        ],
                    },
                },
            ]
        )
        # label band 计数属可见层（1 + 2 bands）
        assert project_render_work(spec).work_input.label_count == 3

    def test_raster_sources_counted_with_image_size(self):
        spec = _spec(
            sources={
                "src-a": {"type": "geojson",
                          "profile": {"featureCount": 10}},
                "img": {"type": "raster", "imageRef": "ref:i",
                        "bounds": [0, 0, 1, 1], "imageSize": [800, 600]},
                "dem": {"type": "raster-dem", "url": "https://x"},
            }
        )
        proj = project_render_work(spec)
        assert proj.work_input.raster_layer_count == 2
        # raster 源有 imageSize → 精确像素；无 → 画布保守面积
        expected = 800 * 600 + DEFAULT_CANVAS_WIDTH * DEFAULT_CANVAS_HEIGHT
        assert proj.work_input.raster_pixels == expected

    def test_components_chart_and_floating(self):
        spec = _spec(
            layout={
                "components": [
                    {"id": "c1", "type": "chart_panel",
                     "placement": {"mode": "floating", "x": 10, "y": 10}},
                    {"id": "c2", "type": "title"},
                    {"id": "c3", "type": "legend", "enabled": False},
                ]
            }
        )
        proj = project_render_work(spec)
        assert proj.work_input.chart_count == 1
        assert proj.work_input.floating_components == 1

    def test_unsupported_types_disclosed(self):
        spec = _spec(
            layers=[
                {"id": "l1", "source": "src-a", "type": "fill"},
                {"id": "lx", "source": "src-a", "type": "not-a-type"},
            ]
        )
        proj = project_render_work(spec)
        assert proj.unsupported_layer_types == ("not-a-type",)

    def test_empty_and_none_spec_yield_empty_projection(self):
        for empty in (None, {}, {"layers": []}):
            proj = project_render_work(empty, revision=0, fingerprint="")
            assert isinstance(proj, RenderWorkProjection)
            assert proj.work_input.layer_count == 0
            assert proj.work_input.feature_count == 0
            assert proj.features_estimated is False
            assert proj.unsupported_layer_types == ()

    def test_monotonic_more_layers_more_work(self):
        small = project_render_work(_spec())
        big = project_render_work(
            _spec(
                layers=[
                    {"id": f"l{i}", "source": "src-a", "type": "fill"}
                    for i in range(50)
                ]
            )
        )
        assert (
            render_work_units(big.work_input)
            > render_work_units(small.work_input)
        )

    def test_monotonic_more_features_more_work(self):
        base = project_render_work(_spec())
        bigger = project_render_work(
            _spec(
                sources={
                    "src-a": {
                        "type": "geojson",
                        "profile": {"featureCount": 120_000},
                    }
                }
            )
        )
        assert (
            render_work_units(bigger.work_input)
            > render_work_units(base.work_input)
        )

    def test_export_dpi_amplifies_work(self):
        screen = project_render_work(_spec())
        hidpi = project_render_work(_spec(), export_dpi=300)
        assert (
            render_work_units(hidpi.work_input)
            > render_work_units(screen.work_input)
        )
        # estimate_render 链路可用（governor 契约面）
        est = estimate_render(hidpi.work_input)
        assert est.source == "render_formula.v1"
        assert est.dim is not None

    def test_to_dict_receipt_is_bounded_json_safe(self):
        spec = _spec()
        # inline 大载荷不进投影：本体只被计数路径读取，receipt 不携带
        spec["sources"]["src-a"]["inlineData"] = {"features": [{"x": 1}]}
        receipt = project_render_work(
            spec, revision=2, fingerprint="fp"
        ).to_dict()
        text = repr(receipt)
        assert "inlineData" not in text
        assert receipt["schema_version"] == PROJECTION_SCHEMA_VERSION
        assert isinstance(receipt["layers_total"], int)
        for note in receipt["notes"]:
            assert isinstance(note, str) and len(note) < 200

    def test_layer_count_truncation_disclosed(self):
        spec = _spec(
            layers=[
                {"id": f"l{i}", "source": "src-a", "type": "fill"}
                for i in range(5000)
            ]
        )
        proj = project_render_work(spec)
        assert proj.layers_total == 5000
        assert proj.work_input.layer_count == 4096
        assert any("truncated" in n for n in proj.notes)


@pytest.mark.cartography
class TestLayerCapabilityVocabulary:
    def test_schema_vocabulary_fully_declared(self):
        """MapSpecLayer.type 词表（9 种）必须逐项声明 —— 词表扩而矩阵
        未跟上时此测试立刻爆（不得静默放行未声明类型）。"""
        from app.lib.cartography.mapspec_schema import MapSpecLayer

        vocab = set(
            MapSpecLayer.model_fields["type"].annotation.__args__
        )
        assert vocab == set(LAYER_TYPE_SUPPORT.keys()), (
            "MapSpecLayer.type 词表与 layer_capability 矩阵漂移: "
            f"{vocab ^ set(LAYER_TYPE_SUPPORT.keys())}"
        )

    def test_declared_levels_are_closed_vocabulary(self):
        from app.lib.cartography.layer_capability import (
            SUPPORT_FULL,
            SUPPORT_NONE,
            SUPPORT_PARTIAL,
        )
        for sup in LAYER_TYPE_SUPPORT.values():
            assert sup.level in (SUPPORT_FULL, SUPPORT_PARTIAL, SUPPORT_NONE)

    def test_disclose_unsupported_bounded_and_stable(self):
        spec = _spec(
            layers=[
                {"id": "l1", "source": "src-a", "type": "weird"},
                {"id": "l2", "source": "src-a", "type": "weird"},
                {"id": "l3", "source": "src-a", "type": "bizarre"},
            ]
        )
        assert disclose_unsupported(spec) == ["weird", "bizarre"]
        assert disclose_unsupported(None) == []
        assert disclose_unsupported("nope") == []

    def test_design_system_manifest_exports_layer_capability(self):
        from app.lib.cartography.design_system import (
            build_design_system_manifest,
        )
        manifest = build_design_system_manifest()
        cap = manifest["layerCapability"]
        assert set(cap.keys()) == set(LAYER_TYPE_SUPPORT.keys())
        for entry in cap.values():
            assert set(entry.keys()) == {"support", "note"}
        # 与 rendererCapability 并列（同一导出链，不另开通道）
        assert "rendererCapability" in manifest
