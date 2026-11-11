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
    assert all(c.visibility_semantics.startswith("live_export_shared:")
               for c in contexts)


def test_a15_dpi_only_affects_sampling_and_size_not_visibility():
    """review F4：可证伪 —— DPI 确实改变物理分母，但不改变任何语义裁决。

    旧断言把 `export_adjudicate_layer`（本就不接受 dpi）对同一 zoom 重复
    调用三次再 set 去重，恒真。改为：三种 DPI 各自产出完整 scale context，
    其 `visibility_semantics` 由 `plan_scale_contexts` 内部真实执行
    `export_adjudicate_layer` 得到；若有人把 DPI 当语义 zoom 接进门控，
    该摘要会随 DPI 变化，本断言即失败。
    """
    ir = _ir()
    by_dpi = {
        dpi: plan_scale_contexts(ir, DOC, target_dpi=dpi,
                                 logical_viewport_px=[1200, 800])
        for dpi in (72, 300, 600)
    }
    # ① DPI 真的进入了采样/尺寸换算通道（分母三值互不相同）
    denominators = {
        dpi: tuple(c.scale_denominator for c in contexts)
        for dpi, contexts in by_dpi.items()
    }
    assert len(set(denominators.values())) == 3
    assert all(all(d is not None for d in ds) for ds in denominators.values())
    # ② 但语义 zoom / frame extent / 逻辑 viewport / 真实可见层裁决摘要
    #    与 DPI 完全无关（逐位比对三种 DPI 产出的上下文）
    signatures = {
        dpi: tuple((tuple(c.frame_extent or []), c.semantic_zoom,
                    c.visibility_semantics, tuple(c.logical_viewport_px))
                   for c in contexts)
        for dpi, contexts in by_dpi.items()
    }
    assert len(set(signatures.values())) == 1
    assert all(c.visibility_semantics.startswith("live_export_shared:")
               for contexts in by_dpi.values() for c in contexts)
    assert all(c.semantic_zoom == 10.0 for contexts in by_dpi.values() for c in contexts)
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
