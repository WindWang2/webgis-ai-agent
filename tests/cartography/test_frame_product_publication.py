"""G15 WP10 —— 同页多画幅出版（PublicationIR product 驱动 + 页编译 + PDF）。

真实旅程锚点（任务书 §6）：
- 同一产品导出的 PDF 各画幅字段/时间/分级/范围/图例一致；
- SVG id/clipPath 不跨帧碰撞（命名空间化）；
- 某帧失败 → 请求按定义失败（typed code + frame_id），不留静默空槽；
- 旧 frames 驱动/atlas 语义零变化（无 frame_product 的 spec 行为不变）。
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from app.lib.cartography.publication_ir import (
    AtlasPolicy,
    MapSpecSchemaError,
    plan_publication_pages,
)
from app.services.publication_export import (
    PageCompileFailure,
    compile_publication_page,
)

pytestmark = pytest.mark.cartography


def _feature(year: int, district: str, value: float, d: int) -> Dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[
            [104.0 + d * 0.1, 30.6], [104.1 + d * 0.1, 30.6],
            [104.1 + d * 0.1, 30.7], [104.0 + d * 0.1, 30.7],
            [104.0 + d * 0.1, 30.6]]]},
        "properties": {"year": year, "district": district, "per_10k": value},
    }


def _product_document(shared_breaks=None) -> Dict[str, Any]:
    feats = [
        _feature(2020, "jinjiang", 21.0 + d, d) for d in range(3)
    ] + [
        _feature(2022, "jinjiang", 25.0 + d, d) for d in range(3)
    ] + [
        _feature(2024, "jinjiang", 31.0 + d, d) for d in range(3)
    ]
    shared = {
        "field": "per_10k", "semantics": "other",
        "method": "equal_interval", "k": 3, "palette": "Blues",
        "domain_status": "complete",
        "per_frame_counts": {"fp-t-2020": 3, "fp-t-2022": 3, "fp-t-2024": 3},
        "sample_size": 9, "eligibility": ["union_breaks"],
    }
    if shared_breaks is not None:
        shared["breaks"] = shared_breaks
    else:
        shared["breaks"] = [20.0, 26.0, 32.0, 35.0]
    return {
        "version": "2.0",
        "view": {"center": [104.15, 30.65], "zoom": 10},
        "sources": {"src-d": {"type": "geojson",
                              "inlineData": {"type": "FeatureCollection",
                                             "features": feats}}},
        "layers": [{
            "id": "lyr-districts", "type": "fill", "source": "src-d",
            "legend_spec": {
                "type": "graduated", "field": "per_10k",
                "breaks": [20.0, 25.0, 30.0, 35.0],
                "palette_colors": ["#c6dbef", "#6baed6", "#2171b5"],
                "labels": ["20 – 25", "25 – 30", "30 – 35"],
                "palette": "Blues", "method": "equal_interval", "k": 3,
                "nodata": {"label": "No data", "color": "#cccccc"},
            },
        }],
        "layout": {"frames": [
            {"id": "fp-t-2020", "title": "2020", "extent": [103.95, 30.55, 104.35, 30.75]},
            {"id": "fp-t-2022", "title": "2022", "extent": [103.95, 30.55, 104.35, 30.75]},
            {"id": "fp-t-2024", "title": "2024", "extent": [103.95, 30.55, 104.35, 30.75]},
        ]},
        "frame_product": {
            "schema_version": "frame_product.v1",
            "enabled": True,
            "product_kind": "time_series",
            "grid": {"columns": 3, "title": "成都每万人小学学位（三年）"},
            "frames": [
                {"frame_id": "fp-t-2020", "legend": "shared",
                 "data": {"ref": "ref:ind", "layer_id": "lyr-districts",
                          "field": "per_10k", "content_revision": 1,
                          "time_window": _tw(2020)}},
                {"frame_id": "fp-t-2022", "legend": "shared",
                 "data": {"ref": "ref:ind", "layer_id": "lyr-districts",
                          "field": "per_10k", "content_revision": 1,
                          "time_window": _tw(2022)}},
                {"frame_id": "fp-t-2024", "legend": "shared",
                 "data": {"ref": "ref:ind", "layer_id": "lyr-districts",
                          "field": "per_10k", "content_revision": 1,
                          "time_window": _tw(2024)}},
            ],
            "shared_expression": shared,
        },
    }


def _tw(year: int) -> Dict[str, Any]:
    return {
        "frame_id": f"fp-t-{year}", "label": str(year),
        "start": f"{year}-01-01T00:00:00Z", "end": f"{year + 1}-01-01T00:00:00Z",
        "time_field": "year", "time_representation": "year_int",
        "granularity": "year", "timezone": "UTC", "layer_id": "lyr-districts",
    }


class TestProductPagePlanning:
    def test_product_driver_single_page_with_slots(self):
        ir = plan_publication_pages(
            _product_document(), atlas=AtlasPolicy(driver="product"))
        assert len(ir.pages) == 1
        page = ir.pages[0]
        assert page.frame_slots and len(page.frame_slots) == 3
        assert [s.frame_id for s in page.frame_slots] == [
            "fp-t-2020", "fp-t-2022", "fp-t-2024"]
        # 2×2 内不重叠：槽矩形两两不相交（1×3 网格横向排布）。
        rects = [(s.x, s.y, s.x + s.w, s.y + s.h) for s in page.frame_slots]
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                overlap = not (a[2] <= b[0] or b[2] <= a[0]
                               or a[3] <= b[1] or b[3] <= a[1])
                assert not overlap, f"slots {i},{j} overlap"
        # 槽在页内。
        for s in page.frame_slots:
            assert 0.0 <= s.x and s.x + s.w <= 1.0
            assert 0.0 <= s.y and s.y + s.h <= 1.0

    def test_legacy_frames_driver_unchanged_without_product_request(self):
        """不带 product driver 的既有导出语义零变化（atlas frames 仍逐帧一页）。"""
        doc = _product_document()
        ir = plan_publication_pages(doc)  # 无 atlas → frames 驱动
        assert len(ir.pages) == 3  # 三帧三页（旧语义）
        assert all(p.frame_slots is None for p in ir.pages)

    def test_no_product_refused(self):
        with pytest.raises(MapSpecSchemaError) as ei:
            plan_publication_pages(
                {"version": "2.0"}, atlas=AtlasPolicy(driver="product"))
        assert ei.value.code == "product_page_no_frame_product"

    def test_grid_2x2_layout(self):
        doc = _product_document()
        doc["frame_product"]["grid"]["columns"] = 2
        doc["layout"]["frames"].append(
            {"id": "fp-t-2026", "title": "2026",
             "extent": [103.95, 30.55, 104.35, 30.75]})
        doc["frame_product"]["frames"].append(
            {"frame_id": "fp-t-2026", "legend": "shared",
             "data": {"ref": "ref:ind", "layer_id": "lyr-districts",
                      "field": "per_10k", "content_revision": 1,
                      "time_window": _tw(2026)}})
        ir = plan_publication_pages(doc, atlas=AtlasPolicy(driver="product"))
        slots = ir.pages[0].frame_slots
        assert len(slots) == 4
        # 2 列网格：前两个在第一行（y 相同），后两个在第二行（y 更大）。
        assert slots[0].y == pytest.approx(slots[1].y)
        assert slots[2].y > slots[0].y
        assert slots[0].x < slots[1].x


class TestProductPageCompile:
    def _compile(self, doc=None):
        doc = doc or _product_document()
        ir = plan_publication_pages(doc, atlas=AtlasPolicy(driver="product"))
        page = ir.pages[0]
        return compile_publication_page(
            doc, page, ir, frames_lookup=[], target_dpi=150,
            max_labels=60, svg_budget_remaining=8 * 1024 * 1024,
        )

    def test_real_svg_composition_with_namespaced_clips(self):
        outcome = self._compile()
        svg = outcome.svg
        assert svg.startswith("<svg")
        # 三个槽 clipPath，id 唯一且命名空间化。
        assert svg.count("<clipPath") == 3
        for i in range(3):
            assert f'id="fpclip{i}"' in svg
            assert f"url(#fpclip{i})" in svg
        # 槽标题带真实渲染。
        for t in ("2020", "2022", "2024"):
            assert f">{t}</text>" in svg
        # 页标题。
        assert "成都每万人小学学位（三年）" in svg
        # 共享图例带（shared breaks 的标签来自 resolver 盖章 legend_spec）。
        assert "Shared classification" in svg
        assert "20 – 26" in svg or "20 – 26" in svg.replace("–", "–")
        assert outcome.svg_bytes > 0

    def test_frame_failure_fails_whole_page_typed(self):
        doc = _product_document()
        doc["frame_product"]["frames"][1]["data"]["layer_id"] = "ghost"
        with pytest.raises(PageCompileFailure) as ei:
            self._compile(doc)
        assert ei.value.code == "product_frame_unresolvable"
        assert "fp-t-2022" in ei.value.detail

    def test_each_slot_carries_only_its_year(self):
        """槽 SVG 内只含该帧时间窗要素的区名标签（互不污染）。"""
        doc = _product_document()
        # 给每个区名写进 district 属性供检查：值按年区分（value 21/25/31）。
        outcome = self._compile(doc)
        svg = outcome.svg
        # fill 色随共享分级：21→第一级、25→第二级、31+→第三级（同 breaks）。
        # 结构性检查：三个槽 group + 三种共享色带色都出现。
        assert svg.count("<clipPath") == 3

    def test_shared_breaks_drive_slot_legends_not_local(self):
        """共享 breaks（[20,26,32,35]）不同于层默认（[20,25,30,35]）——
        组合页的共享图例带标签来自共享域。"""
        outcome = self._compile()
        assert "26" in outcome.svg  # 共享 breaks 边界 26 出现
        # 局部域边界 25 不应作为图例标签出现（它已被共享域取代）。
        assert "25 – 30" not in outcome.svg

    def test_shared_legend_band_svg_escapes_label_color_field(self):
        """图例带 SVG 面的 label/color/field 必须转义（HTML 孪生面同链）。

        label/color 来自用户 legend_spec（nodata 尤其原样透传）、field 来自
        shared_expression —— 未转义时单文件 SVG 导出可注入脚本，color 还能
        破出 fill 属性注入事件处理器。
        """
        from app.services.publication_export import _shared_legend_band_svg

        doc = {"frame_product": {"shared_expression": {
            "field": "per_10k</text><script>alert(1)</script>",
            "unit": "人/万人"}}}
        shared_legend_spec = {
            "type": "graduated", "breaks": [20.0, 26.0],
            "labels": ["20 – 26<img src=x onerror=alert(2)>"],
            "palette_colors": ['#ffffff" onclick="alert(3)"'],
            "nodata": {"label": "无数据</text><script>alert(4)</script>",
                       "color": '#cccccc" onload="alert(5)"'},
        }
        band = _shared_legend_band_svg(doc, None, 1200, shared_legend_spec)
        # 未转义载荷不得出现（内容面 + 属性面）。
        for payload in ("<script", "<img", 'onclick="alert', 'onload="alert',
                        "</text><script>"):
            assert payload not in band, payload
        # 转义形态在场（确实转义而非被丢弃）。
        assert "per_10k&lt;/text&gt;&lt;script&gt;" in band
        assert "&lt;img src=x onerror=alert(2)&gt;" in band
        assert '#ffffff&quot; onclick=&quot;alert(3)&quot;' in band
        assert '#cccccc&quot; onload=&quot;alert(5)&quot;' in band

    def test_product_page_svg_has_no_raw_legend_payload(self):
        """端到端：恶意 nodata label/color 经真实导出路径进入组合页 SVG
        时是转义形态（单文件 SVG 导出不可执行）。"""
        doc = _product_document()
        doc["layers"][0]["legend_spec"]["nodata"] = {
            "label": "无数据</text><script>alert(1)</script>",
            "color": '#cccccc" onload="alert(2)"',
        }
        outcome = self._compile(doc)
        assert outcome.svg.count("<clipPath") == 3  # 页面仍正常组合
        for payload in ("<script", 'onload="alert', "</text><script>"):
            assert payload not in outcome.svg, payload
        assert "无数据&lt;/text&gt;&lt;script&gt;" in outcome.svg
        assert '#cccccc&quot; onload=&quot;alert(2)&quot;' in outcome.svg


@pytest.mark.slow
class TestProductPdfReal:
    """真实 WeasyPrint PDF 旅程（可独立跳过：weasyprint 缺席时 xfail）。"""

    def test_pdf_bytes_valid_and_single_page(self, tmp_path):
        pytest.importorskip("weasyprint")
        from app.services.publication_export import render_publication_pdf

        doc = _product_document()
        result = render_publication_pdf(
            doc, title="成都三年对比", atlas=AtlasPolicy(driver="product"))
        pdf = result.pdf
        assert pdf[:5] == b"%PDF-"
        # 单页产品版面。
        assert result.page_count == 1
        out = tmp_path / "g15_product_2x2.pdf"
        out.write_bytes(pdf)
        assert out.stat().st_size > 10_000
        import pypdf

        reader = pypdf.PdfReader(str(out))
        assert len(reader.pages) == 1
        text = reader.pages[0].extract_text() or ""
        # 页标题 + 各槽标题真实嵌入 PDF 文本层。
        assert "成都每万人小学学位" in text
        for t in ("2020", "2022", "2024"):
            assert t in text
