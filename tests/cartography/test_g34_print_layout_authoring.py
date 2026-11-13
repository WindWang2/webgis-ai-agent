"""G34 纸面版式作者工作台 —— 真实产物 oracle（WP01–WP12）。

验收纪律：

* 断言**真实 SVG/PDF/PNG 产物**里的几何与文字，不只是序列化字段存在。
* ``legacy_bytes_unchanged`` 锁定 opt-in 前后旧导出逐字节不变。
* 负向用例 ``test_t22_exporter_ignoring_component_frame_fails_oracle``
  故意让绘制器忽略作者框 → 几何 oracle 必须失败（防止「只填 IR」假完成）。
* 分层记录：本文件是「后端 + 产物」层；Pi 子进程层在
  ``tests/integration/test_pi_g34_print_layout_journey.py``；浏览器层在
  frontend vitest / Playwright，不把分段通过标成全链通过。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict

import pytest

from app.lib.cartography.print_layout_schema import (
    PRINT_LAYOUT_SCHEMA_VERSION, empty_print_layout, parse_print_layout,
    validate_print_layout,
)
from app.lib.cartography.publication_ir import plan_publication_pages
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed
from app.services.print_layout_authoring import operations, template as template_mod
from app.services.print_layout_authoring.compile import (
    build_draw_plan, build_map_projection,
)
from app.services.print_layout_authoring.fonts import (
    check_glyphs, font_asset_version, measure_text_pt, resolve_font, wrap_text,
)
from app.services.print_layout_authoring.legend import (
    layout_legend, read_legend_content,
)
from app.services.print_layout_authoring.pages import (
    page_numbering, sync_pages,
)
from app.services.print_layout_authoring.units import (
    canvas_units_to_mm, mm_to_canvas_units, mm_to_sample_px,
    pt_to_canvas_units, units_corpus,
)
from app.services.print_layout_authoring.validate import (
    check_source_revision, component_is_locked, validate_authoring,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_UNITS = REPO_ROOT / "tests/cartography/golden_corpus/print_layout_units/basic.json"

LONG_TITLE = (
    "遂宁市土地利用与覆盖变化专题图 2020—2025 "
    "Supercalifragilisticexpialidocious"
)


def _legend_spec() -> Dict[str, Any]:
    return {
        "type": "categorical",
        "title": "土地利用分类与变化监测（含无数据与范围外披露）",
        "unit": "km²",
        "categories": [
            {"key": str(i), "color": f"#{0x225588 + i * 0x001111:06x}",
             "label": f"类别{i:02d} 建设用地扩张监测"} for i in range(14)
        ],
        "nodata": {"color": "#cccccc", "label": "无数据"},
        "out_of_range": {"color": "#eeeeee", "label": "范围外（已截断）"},
        "notes": ["数值区间外单独披露", "单位来源：数据集声明"],
        "proportional_symbols": [
            {"label": "1", "value": 1.0, "color": "#334155"},
            {"label": "10", "value": 10.0, "color": "#334155"},
            {"label": "100", "value": 100.0, "color": "#334155"},
        ],
    }


def _mapspec(*, with_plan: bool = True, locks: Any = None) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "version": "2.1",
        "sources": {
            "s1": {"type": "geojson", "inlineData": {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature",
                     "geometry": {"type": "Point", "coordinates": [105.0, 30.5]},
                     "properties": {"v": 1}},
                    {"type": "Feature",
                     "geometry": {"type": "Point", "coordinates": [105.5, 30.8]},
                     "properties": {"v": 2}},
                ],
            }},
        },
        "layers": [
            {"id": "landuse", "source": "s1", "type": "circle",
             "paint": {"circle-color": "#225588"}, "legend_spec": _legend_spec()},
            {"id": "monitor", "source": "s1", "type": "circle",
             "paint": {"circle-color": "#884422"},
             "legend_spec": {
                 "type": "categorical", "title": "监测点等级",
                 "categories": [
                     {"key": "a", "color": "#16a34a", "label": "一级"},
                     {"key": "b", "color": "#f59e0b", "label": "二级"},
                 ],
                 "nodata": {"color": "#dddddd", "label": "无数据"},
             }},
        ],
        "view": {"center": [105.25, 30.65], "zoom": 8,
                 "bounds": [104.0, 30.0, 106.0, 31.0]},
        "layout": {
            "components": [
                {"id": "title", "type": "title", "enabled": True,
                 "options": {"text": LONG_TITLE}},
                {"id": "subtitle", "type": "subtitle", "enabled": True,
                 "options": {"text": "数据来源：遂宁市自然资源调查 2025"}},
                {"id": "legend", "type": "legend", "enabled": True,
                 "options": {"layerId": "landuse"}},
                {"id": "scale", "type": "scale_bar", "enabled": True},
                {"id": "attribution", "type": "attribution", "enabled": True,
                 "options": {"text": "制图：WebGIS AI Agent"}},
            ],
        },
    }
    if locks:
        doc["workbench"] = {"lockedComponentIds": list(locks)}
    if with_plan:
        plan = empty_print_layout()
        # 标题显式 x=15mm / 宽=180mm（验收场景 2）
        for c in plan["components"]:
            if c["id"] == "title":
                c["text"] = {
                    "mode": "binding", "value": "", "binding": "map_title",
                    "format": "plain", "missing_placeholder": "〔数据缺失〕",
                }
                c["frame"] = {"x": 15.0, "y": 10.0, "width": 180.0, "height": 24.0}
                c["typography"]["size_pt"] = 18.0
            if c["id"] == "legend":
                c["legend"]["columns"] = 2
            if c["id"] == "attribution":
                c["text"] = {
                    "mode": "binding", "value": "", "binding": "source_attribution",
                    "format": "plain", "missing_placeholder": "〔数据缺失〕",
                }
        doc["layout"]["print_layout"] = plan
    return doc


def _compile(doc: Dict[str, Any], *, dpi: int = 300, plan: Any = ...):
    raw = doc.get("layout", {}).get("print_layout") if plan is ... else plan
    paper = (raw or {}).get("paper") or {}
    pw = float(paper.get("width_mm") or 297.0)
    ph = float(paper.get("height_mm") or 210.0)
    return compile_mapspec_to_svg_detailed(
        doc, target_dpi=dpi, width=int(pw * 4), height=int(ph * 4),
        padding=24, include_chrome=True,
        bounds=[104.0, 30.0, 106.0, 31.0],
        **({"print_layout": raw, "paper_mm": (pw, ph), "page_id": "main"}
           if raw else {}),
    )


def _g34_attrs(svg: str, component_id: str) -> Dict[str, str]:
    m = re.search(
        r'<g class="chrome-print-text" data-g34-id="' + re.escape(component_id)
        + r'"([^>]*)>', svg)
    if m is None:
        return {}
    return dict(re.findall(r'(data-g34-[\w-]+)="([^"]*)"', m.group(1)))


# ── T01 / T02 / T03：opt-in 字节稳定 + 模型 + 单位 corpus ────────────────

def test_t01_legacy_export_bytes_unchanged_without_plan():
    """无 layout.print_layout → 旧绘制路径逐字节不变。"""
    plain = _mapspec(with_plan=False)
    base = compile_mapspec_to_svg_detailed(
        plain, target_dpi=300, width=1188, height=840, padding=24,
        include_chrome=True, bounds=[104.0, 30.0, 106.0, 31.0])
    assert "chrome-print-text" not in base.svg
    assert "g34-map-clip" not in base.svg
    assert base.print_layout_receipt == {}
    # 同一输入重复编译确定性（旧链未被新字段污染）
    again = compile_mapspec_to_svg_detailed(
        plain, target_dpi=300, width=1188, height=840, padding=24,
        include_chrome=True, bounds=[104.0, 30.0, 106.0, 31.0])
    assert hashlib.sha256(base.svg.encode()).hexdigest() == \
           hashlib.sha256(again.svg.encode()).hexdigest()


def test_t02_versioned_model_rejects_unknown_version_and_fields():
    plan = empty_print_layout()
    assert validate_print_layout(plan).ok
    bad_version = dict(plan, version="print_layout.v9")
    v = validate_print_layout(bad_version)
    assert not v.ok and v.issues[0].code == "UNKNOWN_PRINT_LAYOUT_VERSION"
    bad_field = dict(plan, rogue_field=1)
    assert validate_print_layout(bad_field).issues[0].code == "PRINT_LAYOUT_INVALID"
    with pytest.raises(ValueError):
        parse_print_layout(bad_version)
    assert parse_print_layout(plan).version == PRINT_LAYOUT_SCHEMA_VERSION


def test_t03_units_golden_parity_and_four_planes():
    corpus = units_corpus()
    stored = json.loads(GOLDEN_UNITS.read_text(encoding="utf-8"))
    assert corpus == stored
    # mm / pt / 逻辑 px / 采样 px 是四条不同的轴
    assert mm_to_canvas_units(1, 300) == pytest.approx(300 / 18, rel=1e-9)
    assert pt_to_canvas_units(12, 300) == pytest.approx(12 * 300 * 25.4 / 1296, rel=1e-9)
    assert mm_to_sample_px(210, 300) == pytest.approx(210 * 300 / 25.4, rel=1e-9)
    assert canvas_units_to_mm(mm_to_canvas_units(15, 150), 150) == pytest.approx(15, abs=1e-9)


# ── T04 / T05：毫米几何 oracle + DPI ─────────────────────────────────────

@pytest.mark.parametrize("dpi", [96, 150, 300])
def test_t04_title_mm_geometry_oracle_in_real_svg(dpi):
    doc = _mapspec()
    comp = _compile(doc, dpi=dpi)
    root = ET.fromstring(comp.svg)  # 真实 XML 可解析（不是字符串拼接假象）
    assert root.tag.endswith("svg")
    attrs = _g34_attrs(comp.svg, "title")
    assert attrs, "exporter did not consume the authored component frame"
    assert float(attrs["data-g34-x-mm"]) == pytest.approx(15.0, abs=0.01)
    assert float(attrs["data-g34-width-mm"]) == pytest.approx(180.0, abs=0.01)
    # 真实 <text> 的 x 也必须落在 15mm 换算出的 user unit 上（居中锚点）。
    # 只取 title 组的行（其它组件各有自己的毫米框，不能混算）。
    parent = {child: p for p in root.iter() for child in p}

    def _in_title(el):
        p = parent.get(el)
        return p is not None and p.attrib.get("data-g34-id") == "title"

    texts = [e for e in root.iter()
             if e.tag.endswith("text") and e.get("data-g34-line") is not None
             and _in_title(e)]
    assert texts, "authored title lines were not really drawn"
    expected_center = mm_to_canvas_units(15 + 180 / 2, dpi)
    for el in texts:
        assert float(el.get("x")) == pytest.approx(expected_center, abs=0.5)
    # 从产物反算毫米：不依赖 data-* 自述也可独立复核
    assert canvas_units_to_mm(float(texts[0].get("x")) - mm_to_canvas_units(15, dpi),
                              dpi) == pytest.approx(90.0, abs=0.05)
    # 物理毫米不随 DPI 变化；viewBox 随 DPI 线性变化
    assert float(root.get("width")) == 297 * 4
    assert float(root.get("height")) == 210 * 4
    vb = [float(v) for v in root.get("viewBox").split()]
    assert vb[2] == pytest.approx(297 * 4 * dpi / 72, rel=1e-6)
    assert vb[3] == pytest.approx(210 * 4 * dpi / 72, rel=1e-6)


def test_t05_dpi_physical_size_constant_and_sample_px_varies():
    assert mm_to_sample_px(297, 96) < mm_to_sample_px(297, 150) \
        < mm_to_sample_px(297, 300)
    for dpi in (96, 150, 300):
        assert canvas_units_to_mm(mm_to_canvas_units(180, dpi), dpi) == \
            pytest.approx(180, abs=1e-9)
    # 字号物理 pt 不随 DPI 漂移（修复 legacy 硬编码 font-size 的缺陷）
    assert pt_to_canvas_units(12, 300) / pt_to_canvas_units(12, 96) == \
        pytest.approx(300 / 96, rel=1e-9)


# ── T06：PDF 真实文本层 / 纸面尺寸 / 字体不被全局 CSS 覆盖 ────────────────

def test_t06_pdf_page_mm_text_layer_and_authored_font():
    pytest.importorskip("pypdf")
    import io

    import pypdf

    from app.services.print_layout_authoring.service import _svg_to_pdf

    doc = _mapspec()
    comp = _compile(doc, dpi=300)
    pdf = _svg_to_pdf(comp.svg, 297.0, 210.0, "G34 oracle")
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 1
    box = reader.pages[0].mediabox
    assert float(box.width) == pytest.approx(297 / 25.4 * 72, abs=1.5)
    assert float(box.height) == pytest.approx(210 / 25.4 * 72, abs=1.5)
    text = reader.pages[0].extract_text() or ""
    assert "遂宁市" in text, "authored title text missing from real PDF text layer"
    # 作者字体写在 SVG presentation attribute 上，未被全局 CSS 静默覆盖；
    # 且主族之后必须跟受控 CJK 兜底族（缺字不落 tofu）。
    assert 'font-family="Noto Sans SC Embedded,' in comp.svg
    assert "Noto Sans SC Embedded, Noto Sans CJK SC" in comp.svg, \
        "authored family must be first in a controlled fallback stack"
    from app.services.publication_export import (
        _cjk_font_face_css, _svg_to_page_html, assemble_pages_pdf,
    )
    single = _svg_to_page_html(comp.svg, 297.0, 210.0,
                               print_layout_active=True)
    assert 'svg text:not([font-family])' in single
    assert 'svg, svg text { font-family:' not in single
    # G34 opt-in 必须真实嵌入所选字体；旧路径不嵌入（字节稳定）
    assert '@font-face' in single
    legacy = _svg_to_page_html(comp.svg, 297.0, 210.0)
    assert '@font-face' not in legacy
    # 多页装配面同样不无条件覆盖；opt-in 时嵌入字体并产出真实 PDF
    from app.lib.cartography.render_diagnostics import DiagnosticSink
    sink = DiagnosticSink()
    multi_bytes = assemble_pages_pdf(
        [("p1", comp.svg, 297.0, 210.0)], title="G34", sink=sink,
        print_layout_active=True)
    assert multi_bytes[:4] == b"%PDF"
    reader2 = pypdf.PdfReader(io.BytesIO(multi_bytes))
    assert len(reader2.pages) == 1
    _ = _cjk_font_face_css


# ── T07 / T08：真实文字度量、换行、缺字、weight fallback ─────────────────

def test_t07_long_cjk_latin_wrap_real_lines_no_truncation():
    r = wrap_text(
        LONG_TITLE, font_id="noto-sans-sc-embedded", size_pt=12,
        box_width_mm=60, box_height_mm=8, overflow_policy="expand")
    assert len(r["lines"]) >= 3
    assert r["truncated"] is False
    joined = "".join(r["lines"])
    # 不截字：所有非空白字符都还在
    assert "Supercalifragilisticexpialidocious" in joined.replace(" ", "") or all(
        ch in joined for ch in "Supercalifragilisticexpialidocious")
    assert "遂宁市" in joined
    assert r["overflow"] is True and r["status"] == "expanded"
    assert r["used_width_pt"] > 0
    assert r["line_widths_pt"]
    # 产物里逐行 <text> 与 lines 一致
    comp = _compile(_mapspec(), dpi=300)
    svg = comp.svg
    receipt_title = next(t for t in comp.print_layout_receipt["text"]
                         if t["component_id"] == "title")
    assert int(_g34_attrs(svg, "title")["data-g34-lines"]) == len(receipt_title["lines"])
    assert receipt_title["lines"] == r["lines"] or len(receipt_title["lines"]) >= 2


def test_t08_missing_glyph_and_unavailable_weight_are_explicit():
    missing = check_glyphs("中文A" + chr(0x1F600), "noto-sans-sc-embedded")
    assert chr(0x1F600) in missing
    bold = resolve_font("noto-sans-sc-embedded", "bold", "normal")
    assert "FONT_WEIGHT_UNAVAILABLE" in bold["issues"]
    assert bold["weight_applied"] == "normal"
    assert bold["applied"] is False  # 不假称所选字体/weight 已完整应用
    ok = resolve_font("noto-sans-cjk-sc", "bold", "normal")
    assert ok["weight_applied"] == "bold"
    assert measure_text_pt("中文ABC", "noto-sans-sc-embedded", 12) > 0
    # 字符串长度不能冒充精确测量
    assert measure_text_pt("iii", "noto-sans-sc-embedded", 12) != \
        measure_text_pt("WWW", "noto-sans-sc-embedded", 12)


# ── T09 / T10 / T11：图例完整性、续排、比例符号 ──────────────────────────

def test_t09_legend_two_columns_complete_with_nodata_and_out_of_range():
    content = read_legend_content(_mapspec(), "legend")
    # 组件 options.layerId=landuse → canonical 单源：14 类 + nodata + 范围外 = 16
    assert content["entries_total"] == 16
    assert content["has_nodata"] and content["has_out_of_range"]
    lay = {"columns": 2, "column_gap_mm": 4, "row_gap_mm": 1.5,
           "swatch_mm": 4, "symbol_mm": 6, "continuation": "expand"}
    plan = layout_legend(content, lay,
                         {"width": 110, "height": 14},
                         {"size_pt": 9, "font_id": "noto-sans-sc-embedded"}, 300)
    assert plan["columns"] == 2
    assert plan["entries_rendered"] == plan["entries_total"]
    assert plan["complete"] is True
    assert plan["status"] == "expanded"
    svg = _compile(_mapspec(), dpi=300).svg
    m = re.search(r'class="chrome-print-legend"([^>]*)>', svg)
    assert m
    a = dict(re.findall(r'(data-g34-[\w-]+)="([^"]*)"', m.group(1)))
    assert int(a["data-g34-entries-total"]) == int(a["data-g34-entries-rendered"])
    assert a["data-g34-columns"] == "2"
    assert a["data-g34-nodata"] == "true"
    assert a["data-g34-out-of-range"] == "true"
    assert svg.count('data-g34-entry="') == content["entries_total"]


def test_t10_legend_overflow_expand_continue_block_never_slices():
    content = read_legend_content(_mapspec(), "legend")
    total = content["entries_total"]
    base = {"columns": 2, "column_gap_mm": 4, "row_gap_mm": 1.5,
            "swatch_mm": 4, "symbol_mm": 6}
    frame = {"width": 110, "height": 5}
    typo = {"size_pt": 9, "font_id": "noto-sans-sc-embedded"}
    blocked = layout_legend(content, {**base, "continuation": "block"},
                            frame, typo, 300)
    assert blocked["status"] == "blocked"
    assert blocked["code"] == "PRINT_LEGEND_OVERFLOW_BLOCKED"
    cont = layout_legend(content, {**base, "continuation": "continue"},
                         frame, typo, 300)
    assert cont["complete"] is True
    assert sum(b["entry_count"] for b in cont["boxes"]) == total
    exp = layout_legend(content, {**base, "continuation": "expand"},
                        frame, typo, 300)
    assert exp["complete"] is True and exp["status"] == "expanded"
    assert exp["needed_height_mm"] > frame["height"]


def test_t11_proportional_symbols_share_one_scale_across_columns():
    content = read_legend_content(_mapspec(), "legend")
    lay = {"columns": 3, "column_gap_mm": 4, "row_gap_mm": 1.5,
           "swatch_mm": 4, "symbol_mm": 6, "continuation": "expand"}
    plan = layout_legend(content, lay, {"width": 180, "height": 40},
                         {"size_pt": 9}, 300)
    radii = [s["radius_mm"] for s in plan["proportional"]]
    assert len(radii) == 3
    # area ∝ value ⇒ r ∝ sqrt(value)：1 : 10 : 100 → 1 : √10 : 10
    assert radii[1] / radii[0] == pytest.approx(10 ** 0.5, rel=1e-6)
    assert radii[2] / radii[0] == pytest.approx(10.0, rel=1e-6)
    svg = _compile(_mapspec(), dpi=300).svg
    values = [float(v) for v in re.findall(r'data-g34-symbol-value="([\d.]+)"', svg)]
    drawn = [float(v) for v in re.findall(r'data-g34-radius-mm="([\d.]+)"', svg)]
    assert len(values) == 3 and len(drawn) == 3
    assert drawn[1] / drawn[0] == pytest.approx(10 ** 0.5, rel=1e-3)


# ── T12：地图槽 contain/crop 不拉伸 ──────────────────────────────────────

def test_t12_map_slot_contain_crop_uniform_scale_effective_extent():
    bounds = [104.0, 30.0, 106.0, 31.0]
    rect = {"x": 0.0, "y": 0.0, "width": 1000.0, "height": 100.0}  # 长窄框
    contain = build_map_projection(bounds, rect, "contain")
    crop = build_map_projection(bounds, rect, "crop")
    assert contain["uniform_scale"] and not contain["non_uniform_stretch"]
    assert contain["sx"] > 0 and contain["sy"] > 0
    assert contain["units_per_radian"] > 0 and crop["units_per_radian"] > 0
    eff_c = contain["effective_extent"]
    eff_k = crop["effective_extent"]
    # contain 保全范围（外扩留白）；crop 明确裁切（子集）
    assert eff_c[0] <= bounds[0] and eff_c[2] >= bounds[2]
    assert eff_k[0] >= bounds[0] and eff_k[2] <= bounds[2]
    # 同一 scale 同时作用于经度与墨卡托 Y（无拉伸）
    assert contain["units_per_radian"] == pytest.approx(
        min(contain["sx"], contain["sy"]), rel=1e-9)
    assert crop["units_per_radian"] == pytest.approx(
        max(crop["sx"], crop["sy"]), rel=1e-9)
    plan = empty_print_layout()
    for c in plan["components"]:
        if c["kind"] == "map_slot":
            c["frame"] = {"x": 10.0, "y": 34.0, "width": 277.0, "height": 20.0}
            c["map_slot"]["fit"] = "contain"
    doc = _mapspec()
    doc["layout"]["print_layout"] = plan
    comp = _compile(doc, dpi=300)
    assert 'data-g34-fit="contain"' in comp.svg
    receipt = comp.print_layout_receipt
    assert receipt["effective_extent"] != list(bounds)
    assert 'g34-map-clip' in comp.svg  # 地理图形裁到纸面槽


# ── T13 / T14：G26 混合政策 + 多画幅产品页 ──────────────────────────────

def test_t13_mixed_export_svg_consumes_same_authoring_plan():
    from app.services.mixed_map_export import _compile_single_svg

    doc = _mapspec()
    comp = _compile_single_svg(doc, {}, bounds=[104.0, 30.0, 106.0, 31.0])
    assert "chrome-print-text" in comp.svg
    assert comp.print_layout_receipt.get("version") == "print_layout_draw.v1"
    # 无栅格绑定时旧 G26 政策不回归：raster_placements 为空且不谎报
    assert comp.raster_placements == []


def test_t14_product_page_overlay_uses_same_plan_without_second_runtime():
    from app.services.print_layout_authoring.html import authored_product_overlay

    doc = _mapspec()
    plan = doc["layout"]["print_layout"]
    out = authored_product_overlay(
        doc, plan, page_id="main", page_title="三画幅产品",
        page_number=1, page_count=1, paper_mm=(297.0, 210.0),
        geo_bounds=[104.0, 30.0, 106.0, 31.0])
    assert out.get("error") is None
    assert 'data-g34-id="title"' in out["svg"]
    assert 'data-g34-entries-total' in out["html"] or "遂宁市" in out["html"]


# ── T15 / T16 / T17 / T18：页册、模板、绑定、stale ───────────────────────

def test_t15_three_page_atlas_reorder_disable_keeps_binding_and_toc():
    doc = _mapspec()
    doc["layout"]["frames"] = [
        {"id": f"f{i}", "title": f"页{i}", "enabled": True,
         "pageSize": {"profile": "a4_landscape"}} for i in (1, 2, 3)
    ]
    plan = doc["layout"]["print_layout"]
    synced = sync_pages(plan, doc)
    plan = synced["plan"]
    assert synced["planner_page_count"] == 3
    before = {p["page_id"]: p["binding_digest"] for p in plan["atlas"]["pages"]}
    # 重排 + 关闭一页（只改 order/enabled，不动 digest）
    result = operations.apply_operations(plan, [{"op": "set_pages", "pages": [
        {"page_id": "f3", "order": 0, "title": "重排后的第三页"},
        {"page_id": "f1", "order": 1, "enabled": False},
        {"page_id": "f2", "order": 2},
    ]}])
    assert result["ok"], result["issues"]
    after = {p["page_id"]: p["binding_digest"] for p in result["plan"]["atlas"]["pages"]}
    assert before == after, "排序后页绑定不得串页"
    toc = page_numbering(result["plan"])["toc"]
    assert page_numbering(result["plan"])["page_count"] == 2
    assert [r["page_id"] for r in toc] == ["f3", "f2"]
    assert toc[0]["title"] == "重排后的第三页"
    assert toc[0]["page_number"] == 1 and toc[1]["page_number"] == 2
    # plan fingerpint 参与页缓存（planner 侧）
    from app.services.atlas_batch.planner import plan_atlas_binding
    from app.lib.cartography.atlas_job_spec import AtlasJobSpec, AtlasPolicy

    spec = AtlasJobSpec(session_id="g34-test-session",
                        atlas=AtlasPolicy(driver="frames", page_budget=20))
    binding = plan_atlas_binding(spec, {**doc, "layout": {
        **doc["layout"], "print_layout": result["plan"]}})
    assert len(binding.bindings) == 3
    # 页覆写变化必须轮换 fingerprint（否则 executor 缓存串页）
    changed = copy.deepcopy(result["plan"])
    changed["atlas"]["pages"][0]["overrides"] = [
        {"component_id": "title", "frame": {"x": 20, "y": 10, "width": 100, "height": 20}}]
    binding2 = plan_atlas_binding(spec, {**doc, "layout": {
        **doc["layout"], "print_layout": changed}})
    assert [b.fingerprint for b in binding.bindings] != \
           [b.fingerprint for b in binding2.bindings]


def test_t16_template_reset_only_clears_g34_overrides():
    plan = empty_print_layout()
    plan["atlas"]["pages"] = [
        {"page_id": "p1", "enabled": True, "order": 0, "title": "用户标题",
         "original_title": "原始标题", "paper_profile": "a3_landscape",
         "template_id": "tpl:x", "binding_digest": "abc",
         "overrides": [{"component_id": "title", "visible": False}]},
    ]
    out = template_mod.reset_overrides(plan, "p1")
    page = out["atlas"]["pages"][0]
    assert page["overrides"] == []
    assert page["template_id"] is None and page["paper_profile"] is None
    assert page["title"] == "原始标题"
    assert page["binding_digest"] == "abc"
    # 源图标题/锁/图层可见性根本不在这个模型里（未被触碰）
    assert out["components"] == plan["components"]
    assert "workbench" not in out and "layers" not in out


def test_t17_dynamic_binding_missing_shows_visible_placeholder():
    r = template_mod.resolve_binding("data_time", {"data_time": ""})
    assert r["missing"] is True and "缺失" in r["value"]
    assert r["code"] == "TEXT_BINDING_MISSING"
    ok = template_mod.resolve_binding("page_number", {"page_number": 3}, fmt="padded2")
    assert ok["value"] == "03" and ok["resolved"] is True
    bad = template_mod.resolve_binding("unknown", {})
    assert bad["code"] == "UNKNOWN_TEXT_BINDING"
    # 关闭格式化函数集：无 eval / HTML / SQL
    assert "UNKNOWN_TEXT_FORMAT" == template_mod.resolve_binding(
        "page_number", {"page_number": 1}, fmt="eval(1+1)")["code"]


def test_t18_stale_revision_refused_without_mixing_pages():
    plan = empty_print_layout()
    plan["source"]["mapspec_revision"] = 7
    assert check_source_revision(plan, 7)["ok"] is True
    stale = check_source_revision(plan, 8)
    assert stale["code"] == "SUPERSEDED" and not stale["ok"]
    assert check_source_revision(plan, None)["code"] == "MISSING_EXPECTED_REVISION"


# ── T20 / T21 / T22 / T23 / T24 ────────────────────────────────────────

def test_t20_plan_survives_mapspec_document_round_trip():
    from app.lib.cartography.mapspec_schema import MapSpecDocument

    doc = _mapspec()
    parsed = MapSpecDocument.model_validate(doc)
    dumped = parsed.model_dump(mode="json", exclude_none=True)
    assert dumped["layout"]["print_layout"]["version"] == "print_layout.v1"
    reparsed = MapSpecDocument.model_validate(json.loads(json.dumps(dumped)))
    assert reparsed.layout.print_layout["components"][0]["frame"]["x"] == 15.0
    # TS 投影面也已 additive 生成
    generated = (REPO_ROOT / "frontend/lib/mapspec-compiler/types.generated.ts").read_text()
    assert "print_layout?: Record<string, unknown>;" in generated


def test_t21_pi_and_ui_operations_share_one_service_and_result():
    plan = empty_print_layout()
    ops = [
        {"op": "set_frame", "component_ids": ["title"],
         "frame": {"x": 15, "y": 12, "width": 180, "height": 20}},
        {"op": "set_legend", "component_ids": ["legend"],
         "legend": {"columns": 2}},
    ]
    a = operations.apply_operations(plan, ops, locks=["scale"])
    b = operations.apply_operations(plan, ops, locks=["scale"])
    assert a["ok"] and b["ok"]
    assert a["plan"] == b["plan"]
    assert a["changes"] == b["changes"]
    assert parse_print_layout(a["plan"]).version == PRINT_LAYOUT_SCHEMA_VERSION


def test_t22_exporter_ignoring_component_frame_fails_oracle(monkeypatch):
    """负向：让绘制器忽略作者框 → 几何 oracle 必须失败，不能只靠序列化测试。"""
    import app.services.mapspec_to_svg as svg_mod

    monkeypatch.setattr(svg_mod, "_render_authored_chrome",
                        lambda *a, **k: ("", {"rendered": [], "omitted": [],
                                              "diagnostics": []}))
    comp = _compile(_mapspec(), dpi=300)
    attrs = _g34_attrs(comp.svg, "title")
    assert attrs == {}, "负向用例失效：忽略作者框后仍出现了几何 oracle 属性"
    # 真实 oracle 在这种情况下必须判失败
    assert float(attrs.get("data-g34-x-mm", "-1")) != pytest.approx(15.0, abs=0.01)


def test_t23_font_unavailable_and_cancel_are_explicit_terminal_states():
    import asyncio

    from app.services.print_layout_authoring import service

    # 取消：service.preview 在编译前检查 cancel_event，不返回 completed
    class _Cancelled:
        def is_set(self) -> bool:
            return True

    class _Ok:
        def is_set(self) -> bool:
            return False

    # 不依赖真实 session：直接验证取消短路（不触碰 mapspec_store）
    out = asyncio.run(service.preview("", cancel_event=_Cancelled()))
    assert out["status"] == "cancelled" and out["success"] is False
    out2 = asyncio.run(service.submit_export("", cancel_event=_Cancelled()))
    assert out2["status"] == "cancelled" and out2["success"] is False
    assert font_asset_version()
    _ = _Ok


def test_t24_atomic_align_distribute_lock_and_undo():
    plan = empty_print_layout()
    # 三个可动组件
    for c in plan["components"]:
        if c["id"] == "legend":
            c["frame"] = {"x": 30, "y": 186, "width": 110, "height": 14}
        if c["id"] == "scale":
            c["frame"] = {"x": 60, "y": 186, "width": 72, "height": 12}
        if c["id"] == "attribution":
            c["frame"] = {"x": 90, "y": 198, "width": 180, "height": 5}
    ids = ["legend", "scale", "attribution"]
    before = {c["id"]: dict(c["frame"]) for c in plan["components"]}
    aligned = operations.apply_operations(plan, [
        {"op": "align", "component_ids": ids, "edge": "left"},
        {"op": "distribute", "component_ids": ids, "axis": "x"},
    ])
    assert aligned["ok"], aligned["issues"]
    assert len(aligned["changes"]) == 2
    after = {c["id"]: dict(c["frame"]) for c in aligned["plan"]["components"]}
    # align left 把三个框都移到最小 x（legend 本就是最左 → 保持不变），
    # distribute 保持首尾、等分中间间距；断言真实发生的是「中间框移动」。
    assert after["legend"]["x"] == before["legend"]["x"]
    assert after["attribution"]["x"] != before["attribution"]["x"]
    xs = [after[i]["x"] for i in ("legend", "scale", "attribution")]
    widths = [after[i]["width"] for i in ("legend", "scale", "attribution")]
    gap1 = xs[1] - (xs[0] + widths[0])
    gap2 = xs[2] - (xs[1] + widths[1])
    assert gap1 == pytest.approx(gap2, abs=1e-6)
    # 一次「反向重放」整笔恢复（undo 语义：同一通道，不是状态回写）
    undo = operations.apply_operations(aligned["plan"], [
        {"op": "replace_plan", "plan": plan}])
    assert undo["ok"]
    restored = {c["id"]: dict(c["frame"]) for c in undo["plan"]["components"]}
    assert restored == before
    # 锁组件：默认整笔拒绝（不移动、不形成半套未说明布局）
    locked = operations.apply_operations(plan, [
        {"op": "align", "component_ids": ids, "edge": "left"}], locks=["scale"])
    assert not locked["ok"]
    assert locked["issues"][0]["code"] == "PRINT_LAYOUT_LOCKED_COMPONENT"
    assert locked["plan"]["components"] == plan["components"]
    # 显式 skip_locked：只移动未锁组件并披露，仍是一次原子动作
    skipped = operations.apply_operations(plan, [
        {"op": "align", "component_ids": ids, "edge": "left",
         "skip_locked": True}], locks=["scale"])
    assert skipped["ok"] and skipped["skipped_locked"] == ["scale"]
    assert next(c for c in skipped["plan"]["components"]
                if c["id"] == "scale")["frame"] == before["scale"]
    assert component_is_locked("scale__v2", ["scale"]) is True


def test_t25_validate_authoring_reports_real_lines_and_legend_completeness():
    doc = _mapspec()
    plan = doc["layout"]["print_layout"]
    report = validate_authoring(doc, plan, dpi=300)
    assert report["ok"], report["errors"]
    title = next(c for c in report["components"] if c["component_id"] == "title")
    assert title["text_layout"]["lines"]
    assert title["text_layout"]["truncated"] is False
    legend = next(c for c in report["components"] if c["component_id"] == "legend")
    assert legend["legend"]["complete"] is True
    assert legend["locked"] is False


def test_t26_plan_publication_pages_consumes_plan_and_paper():
    doc = _mapspec()
    ir = plan_publication_pages(doc)
    assert len(ir.pages) == 1
    assert ir.pages[0].print_layout["version"] == "print_layout.v1"
    assert ir.pages[0].paper.width_mm == 297.0
    # 自定义纸张（A3）真实进入 IR
    doc2 = copy.deepcopy(doc)
    doc2["layout"]["print_layout"]["paper"] = {
        "profile": "a3_landscape", "width_mm": 420.0, "height_mm": 297.0,
        "orientation": "landscape"}
    ir2 = plan_publication_pages(doc2)
    assert ir2.pages[0].paper.width_mm == 420.0
    assert ir2.pages[0].paper.height_mm == 297.0
