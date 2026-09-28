"""atlas 多页导出管线级测试（C14）—— PublicationIR → 多页矢量 PDF。

覆盖 Definition of Done 的验证面：

- 单页与 atlas 多页从同一 PublicationIR 产生（同一入口，页数/页序/几何一致）；
- PNG/PDF/SVG 布局 parity 的**语义面**：逐页 SVG 语义（extract_semantics）
  对拍 expected_semantics（页文档），PDF 页数 == IR 页数（不只像素）；
- 封面/目录（确定性文本页，PDF 文本层可检索）；
- 帧失败跳过政策（坏页不中断 atlas + ``atlas_page_skipped`` 披露）；
- WeasyPrint 串行化（busy → 结构化拒绝，不排队）；
- 长标题/图例溢出 → preflight 显式 warning（不 silent crop）；
- 回执面（layout_version / spec_fingerprint / atlas_pages）。
"""

import io
import json

import pytest

from app.lib.cartography.export_semantic_corpus import (
    expected_semantics,
    extract_semantics,
)
from app.lib.cartography.publication_ir import AtlasPolicy
from app.lib.cartography.mapspec_schema import MapSpecSchemaError
from app.services.publication_export import (
    PublicationBusyError,
    render_publication_pdf,
)

pytestmark = pytest.mark.cartography

pdf = pytest.importorskip("pypdf")
pytest.importorskip("weasyprint")


def _atlas_doc(num_features: int = 6, title: str = "C14 管线图册",
               distinct_zones: bool = False):
    feats = []
    for i in range(num_features):
        zone = (f"zone-{i:03d}" if distinct_zones
                else f"zone-{chr(ord('A') + i % 3)}")
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [110.0 + (i % 3) * 0.5, 30.0 + (i // 3) * 0.5]},
            "properties": {"zone": zone, "value": i},
        })
    return {
        "version": "1.1",
        "sources": {"g": {"type": "geojson",
                          "inlineData": {"type": "FeatureCollection",
                                         "features": feats}}},
        "layers": [{"id": "l1", "source": "g", "type": "circle",
                    "paint": {"circle-color": "#0f766e", "circle-radius": 5},
                    "legend_spec": {"entries": [
                        {"label": "站点", "color": "#0f766e"}]}},
        ],
        "layout": {"components": [
            {"id": "t", "type": "title", "options": {"text": title}},
            {"id": "s", "type": "subtitle", "options": {"text": "atlas pipeline"}},
            {"id": "na", "type": "north_arrow"},
            {"id": "sb", "type": "scale_bar"},
            {"id": "lg", "type": "legend"},
            {"id": "at", "type": "attribution", "options": {"text": "(c) C14 test"}},
        ]},
    }


def _pdf_text(pdf_bytes: bytes, page_idx: int) -> str:
    reader = pdf.PdfReader(io.BytesIO(pdf_bytes))
    return reader.pages[page_idx].extract_text() or ""


def test_single_page_via_ir_receipt_fields():
    result = render_publication_pdf(_atlas_doc(), title="单页")
    assert result.page_count == 1
    assert result.layout_version == "1.0.0"
    assert result.spec_fingerprint.startswith("pubspec-sha256:")
    assert result.atlas is False and result.atlas_pages == []


def test_category_atlas_end_to_end():
    doc = _atlas_doc()
    result = render_publication_pdf(
        doc,
        title="C14",
        atlas=AtlasPolicy(driver="category", category_property="zone",
                          atlas_title="C14 管线图册", include_cover=True),
    )
    assert result.atlas is True
    # 封面 + 3 类别页 = 4
    assert result.page_count == 4
    assert [p["page_id"] for p in result.atlas_pages] == [
        "cover", "cat_zone-A", "cat_zone-B", "cat_zone-C"]
    # 封面文本层：图册标题 + 目录条目（矢量文本可检索 —— 非 silent crop）
    cover_text = _pdf_text(result.pdf, 0)
    assert "C14 管线图册" in cover_text
    assert "zone-A" in cover_text
    # 内容页地图语义在场（标题族 chrome 编译进每页）
    p1_text = _pdf_text(result.pdf, 1)
    assert "C14 管线图册" in p1_text


def test_svg_semantic_parity_per_atlas_page():
    """PNG/PDF/SVG parity 的语义面：每页 SVG 语义 == 该页文档期望语义。"""
    from app.services.publication_export import _page_filtered_doc  # noqa:  模块内消费面
    from app.lib.cartography.publication_ir import plan_publication_pages

    doc = _atlas_doc()
    ir = plan_publication_pages(
        doc, atlas=AtlasPolicy(driver="category", category_property="zone"))
    assert len(ir.pages) == 3
    from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

    for page in ir.pages:
        page_doc = _page_filtered_doc(doc, page)
        kept = page_doc["sources"]["g"]["inlineData"]["features"]
        assert all(f["properties"]["zone"] == page.filter_value for f in kept)
        comp = compile_mapspec_to_svg_detailed(
            page_doc, width=int(page.paper.width_mm * 4),
            height=int(page.paper.height_mm * 4), padding=24,
            include_chrome=True, bounds=page.bounds,
        )
        expected = expected_semantics(page_doc)
        actual = extract_semantics(comp.svg)
        assert actual["parse_ok"] is True
        assert actual["title_text"] == expected["title_text"]
        assert actual["legend_entry_count"] == expected["legend_entries"]
        assert set(expected["chrome_families"]) == set(actual["chrome_families"])
        # 页间隔离：本页类别外的要素颜色/内容不出现（无跨页泄漏的语义面探针）
        others = [v for v in ("zone-A", "zone-B", "zone-C") if v != page.filter_value]
        svg_text = comp.svg
        for other in others:
            assert other not in svg_text


def test_frame_failure_skips_page_and_discloses(monkeypatch):
    """帧失败跳过政策：坏页不中断 atlas（页序保持）+ atlas_page_skipped 披露。"""
    doc = _atlas_doc()
    doc["layout"]["frames"] = [
        {"id": "ok1", "extent": [100.0, 30.0, 110.0, 40.0]},
        {"id": "bad", "extent": [110.0, 30.0, 120.0, 40.0]},
        {"id": "ok2", "view": {"center": [116.0, 39.9], "zoom": 10}},
    ]
    import app.services.publication_export as pe

    real_compile = pe.compile_mapspec_to_svg_detailed

    def _flaky(frame_doc, *, bounds=None, **kwargs):
        # 坏帧以其 bounds 辨识（帧级编译面 = 覆盖后整文档，frames 列表对每页可见）
        if bounds and abs(bounds[0] - 110.0) < 1e-9 and abs(bounds[2] - 120.0) < 1e-9:
            raise RuntimeError("injected compile failure")
        return real_compile(frame_doc, bounds=bounds, **kwargs)

    monkeypatch.setattr(pe, "compile_mapspec_to_svg_detailed", _flaky)
    result = render_publication_pdf(doc, title="skips")
    codes = {d.get("code") for d in result.diagnostics}
    assert result.frames_skipped == 1
    assert result.frames_rendered == 2
    assert result.page_count == 2
    assert "atlas_page_skipped" in codes


def test_atlas_pages_truncation_receipt():
    doc = _atlas_doc(num_features=60, distinct_zones=True)  # 60 类别 → 预算 20 封顶
    result = render_publication_pdf(
        doc, title="trunc",
        atlas=AtlasPolicy(driver="category", category_property="zone"),
    )
    codes = {d.get("code") for d in result.diagnostics}
    assert "atlas_truncated" in codes
    assert result.page_count <= 20


def test_long_title_preflight_warning_not_silent():
    doc = _atlas_doc()
    doc["layout"]["frames"] = [{"id": "f1", "title": "长" * 80,
                                "extent": [100.0, 30.0, 110.0, 40.0]}]
    result = render_publication_pdf(doc, title="wrap")
    preflight = [d for d in result.diagnostics if d.get("code") == "publication_preflight"]
    assert any("title_wrap_expected" in str(d.get("detail")) for d in preflight)


def test_weasyprint_busy_is_typed_not_queued():
    """WeasyPrint 串行化：真实进程锁占用中 → PublicationBusyError（不排队）。

    经真实 render_pdf_exclusive 路径（非阻塞 acquire → 结构化拒绝），
    不 mock 锁原语（_thread.lock.acquire 只读）。
    """
    import app.services.publication_export as pe

    assert pe._WEASYPRINT_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(PublicationBusyError):
            render_publication_pdf(_atlas_doc(), title="busy")
    finally:
        pe._WEASYPRINT_LOCK.release()


def test_unhydrated_ref_sources_still_typed_rejected_under_atlas():
    doc = _atlas_doc()
    doc["sources"]["g"] = {"type": "geojson", "ref": "ref:dataset/big"}
    with pytest.raises(MapSpecSchemaError) as e:
        render_publication_pdf(
            doc, title="ref",
            atlas=AtlasPolicy(driver="category", category_property="zone"),
        )
    assert e.value.code == "mapspec_ref_sources_unhydrated"


def test_atlas_fingerprint_tracks_spec_shape():
    doc = _atlas_doc()
    r1 = render_publication_pdf(doc, title="fp")
    doc["layers"][0]["paint"]["circle-radius"] = 7
    r2 = render_publication_pdf(doc, title="fp")
    assert r1.spec_fingerprint != r2.spec_fingerprint


def test_twenty_five_enabled_frames_non_atlas_still_render(monkeypatch):
    """review P0 回归：非 atlas 22–50 enabled 帧路径不得因 IR 模型界 500。

    编译面替换为微缩 SVG（只测页面装配/计数，不测编译内容 —— 内容由
    其余用例覆盖），锁定「帧驱动既有帽 = MAX_SPEC_FRAMES」不变式。
    """
    doc = _atlas_doc()
    doc["layout"]["frames"] = [
        {"id": f"f{i}", "title": f"页{i}", "extent": [100.0 + i, 30.0, 101.0 + i, 31.0]}
        for i in range(25)
    ]
    import types

    import app.services.publication_export as pe

    def _tiny(frame_doc, **kwargs):
        return types.SimpleNamespace(
            svg="<svg xmlns='http://www.w3.org/2000/svg'><title>t</title></svg>",
            diagnostics=[], rendered_component_types=["title"],
            omitted_components=[], feature_count=0, truncated_features=0,
            timed_out=False,
        )

    monkeypatch.setattr(pe, "compile_mapspec_to_svg_detailed", _tiny)
    result = render_publication_pdf(doc, title="many-frames")
    assert result.frames_rendered == 25
    assert result.frames_skipped == 0
    assert result.page_count == 25


def test_duplicate_frame_ids_and_cover_reservation_distinct_irs():
    """review P2 回归：重复帧 id / 与保留封面 id 冲突 → 确定性去重，IR 不串页。"""
    from app.lib.cartography.publication_ir import plan_publication_pages

    doc = _atlas_doc()
    doc["layout"]["frames"] = [
        {"id": "cover", "title": "A", "extent": [0.0, 0.0, 1.0, 1.0]},
        {"id": "cover", "title": "B", "extent": [1.0, 1.0, 2.0, 2.0]},
        {"id": "f3", "title": "C", "extent": [2.0, 2.0, 3.0, 3.0]},
    ]
    ir = plan_publication_pages(
        doc, atlas=AtlasPolicy(driver="frames", include_cover=True))
    ids = [p.page_id for p in ir.pages]
    assert len(ids) == len(set(ids)), ids
    assert ids[0] == "cover" and ir.pages[0].cover
    irs = [json.dumps(p.component_ir, sort_keys=True) for p in ir.pages]
    assert len(set(irs)) == len(irs)  # 每页组件 IR 互不相同（不串页）


def test_atlas_frames_driver_without_frames_typed_rejected():
    """review P3 回归：显式 atlas + 无帧 → atlas_no_pages（不静默整幅）。"""
    from app.lib.cartography.publication_ir import plan_publication_pages

    with pytest.raises(MapSpecSchemaError) as e:
        plan_publication_pages(
            _atlas_doc(), atlas=AtlasPolicy(driver="frames"))
    assert e.value.code == "atlas_no_pages"


def test_bool_property_excluded_from_category_pages_and_filters():
    """review P3 回归：bool 属性值不入类别页；过滤判据与 planner 对齐。"""
    from app.lib.cartography.publication_ir import plan_publication_pages
    from app.services.publication_export import _page_filtered_doc

    doc = _atlas_doc(num_features=2)
    doc["sources"]["g"]["inlineData"]["features"].extend([
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [130.0, 30.0]},
         "properties": {"zone": True}},
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [131.0, 30.0]},
         "properties": {"zone": "True"}},
    ])
    ir = plan_publication_pages(
        doc, atlas=AtlasPolicy(driver="category", category_property="zone"))
    # 值域：字符串 "True" 成页；bool True 不成页
    assert "True" in [p.filter_value for p in ir.pages]
    assert "zone-A" in [p.filter_value for p in ir.pages]
    page = next(p for p in ir.pages if p.filter_value == "True")
    page_doc = _page_filtered_doc(doc, page)
    kept_zones = [f["properties"]["zone"]
                  for f in page_doc["sources"]["g"]["inlineData"]["features"]]
    assert kept_zones == ["True"]  # bool True 不因 str() 巧合混入


def test_parse_chart_series_cap_dict_entry_semantics():
    """review P1 回归：series 帽按 **dict 形状条目**计（非法 data 占位零产出），
    与既有 [dict 过滤后][:MAX_SERIES] 逐值同语义。"""
    from app.lib.cartography.svg_charts import MAX_SERIES, _parse_chart

    raw = {"type": "bar", "data": [], "series": (
        [{"name": "empty", "data": []}]
        + [{"name": f"s{i}", "data": [{"value": i}]} for i in range(8)]
    )}
    _k, _t, _p, series, _st, raw_count = _parse_chart(raw)
    assert len(series) == MAX_SERIES - 1  # empty 占首位帽位，有效序列少一
    assert [s["name"] for s in series] == [f"s{i}" for i in range(MAX_SERIES - 1)]
    assert raw_count == MAX_SERIES - 1
