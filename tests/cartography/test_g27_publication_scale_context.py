"""G27 P5 出版等价视窗 —— 真实 PublicationIR 解析路径回归。

A01 旧项目零变化 / A15 DPI 不改可见层 / A16 非 Mercator 不套固定公式 /
A21 多 frame 各自等价 viewport / A24 解释量有界。
"""
from __future__ import annotations

from app.lib.cartography.publication_ir import (
    plan_publication_pages,
    plan_scale_contexts,
)
from app.lib.cartography.representation import (
    export_adjudicate_layer,
    export_zoom_of_mapspec,
)

DOC = {
    "version": "2.1",
    "view": {"zoom": 10, "center": [116.4, 39.9]},
    "layers": [{"id": "L1", "source": "s1", "type": "circle"}],
    "sources": {
        "s1": {
            "type": "geojson",
            "data": {"type": "FeatureCollection", "features": []},
        }
    },
    "layout": {
        "frames": [
            {"id": "f1", "enabled": True,
             "extent": [116.0, 39.0, 117.0, 40.0]},
            {"id": "f2", "enabled": True,
             "extent": [120.0, 30.0, 121.0, 31.0]},
        ],
        "scale_viewport": {
            "crs": "EPSG:3857",
            "logical_viewport_px": [1200, 800],
        },
    },
}

LAYER = {
    "id": "L1",
    "type": "circle",
    "visibility": {"min_zoom": 8.0, "max_zoom": 14.0},
}


def _ir():
    return plan_publication_pages(DOC)


def test_a21_each_frame_consumes_its_own_extent_and_viewport():
    ir = _ir()
    contexts = plan_scale_contexts(
        ir, DOC, crs="EPSG:3857", target_dpi=300,
        logical_viewport_px=[1200, 800])
    assert [c.page_id for c in contexts] == ["f1", "f2"]
    assert contexts[0].frame_extent == [116.0, 39.0, 117.0, 40.0]
    assert contexts[1].frame_extent == [120.0, 30.0, 121.0, 31.0]
    assert contexts[0].frame_extent != contexts[1].frame_extent
    assert all(c.logical_viewport_px == [1200, 800] for c in contexts)
    assert all(c.semantic_zoom == export_zoom_of_mapspec(DOC) == 10.0 for c in contexts)
    # 不同纬度 → 不同物理比例尺分母；但都不是把 DPI 当 zoom
    assert contexts[0].scale_denominator is not None
    assert contexts[1].scale_denominator is not None
    assert contexts[0].scale_denominator != contexts[1].scale_denominator
    assert all(c.visibility_semantics == "live_export_shared" for c in contexts)


def test_a15_dpi_only_affects_sampling_and_size_not_visibility():
    ir = _ir()
    by_dpi = {
        dpi: plan_scale_contexts(ir, DOC, target_dpi=dpi,
                                 logical_viewport_px=[1200, 800])
        for dpi in (72, 300, 600)
    }
    for contexts in by_dpi.values():
        assert [c.frame_extent for c in contexts] == [
            [116.0, 39.0, 117.0, 40.0], [120.0, 30.0, 121.0, 31.0]]
        assert all(c.semantic_zoom == 10.0 for c in contexts)
        assert all(c.visibility_semantics == "live_export_shared" for c in contexts)
    # 可见层裁决函数不接受 DPI：三种 DPI 下逐 zoom 的裁决逐位相同
    for zoom in (0.0, 7.9, 8.0, 10.0, 13.9, 14.0, 24.0):
        verdicts = {
            export_adjudicate_layer(LAYER, zoom)
            for _ in by_dpi
        }
        assert len(verdicts) == 1
    assert export_adjudicate_layer(LAYER, 7.9) == "gated"
    assert export_adjudicate_layer(LAYER, 10.0) == "render"


def test_a16_non_mercator_crs_does_not_use_fixed_formula():
    ir = _ir()
    contexts = plan_scale_contexts(
        ir, DOC, crs="EPSG:4326", target_dpi=300,
        logical_viewport_px=[1200, 800])
    assert len(contexts) == 2
    for c in contexts:
        assert c.crs == "EPSG:4326"
        assert c.scale_denominator is None
        assert any("非 Web Mercator" in a for a in c.assumptions)
        assert any("不套固定公式" in a for a in c.assumptions)


def test_a01_ir_model_and_fingerprint_unchanged():
    """加性解析入口不改 IR 模型：带/不带 scale_viewport 的 IR 指纹相同。"""
    ir_a = _ir()
    doc_without = {k: v for k, v in DOC.items() if k != "layout"}
    doc_without["layout"] = {"frames": DOC["layout"]["frames"]}
    ir_b = plan_publication_pages(doc_without)
    assert ir_a.ir_fingerprint() == ir_b.ir_fingerprint()
    assert not hasattr(ir_a.pages[0], "scale_context")


def test_a24_context_is_bounded():
    ir = _ir()
    contexts = plan_scale_contexts(ir, DOC, target_dpi=300)
    assert len(contexts) <= len(ir.pages)
    for c in contexts:
        assert len(c.assumptions) <= 8
        assert len(c.logical_viewport_px) == 2
        assert len(c.page_id) <= 120
        assert all(len(a) <= 200 for a in c.assumptions)
