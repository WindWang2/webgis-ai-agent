"""F14 review-gate 修复回归（P0-1 / P1-1..P1-6 / P2 系列）。

独立 review（Subagent C）NEEDS_FIXES 清偿的钉定测试；每条对应一个发现。
"""
import re

import pytest

from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed


def _base_spec(components):
    return {
        "version": "1.1",
        "sources": {"s1": {"type": "geojson", "inlineData": {
            "type": "FeatureCollection",
            "features": [{"type": "Feature",
                          "geometry": {"type": "Point",
                                       "coordinates": [10.0, 40.0]},
                          "properties": {}}]}}},
        "layers": [{"id": "l1", "source": "s1", "type": "circle",
                    "paint": {"circle-color": "#2563eb", "circle-radius": 4},
                    "visible": True}],
        "layout": {"components": components},
    }


def _compile(spec, **kw):
    kw.setdefault("bounds", [5, 35, 15, 45])
    return compile_mapspec_to_svg_detailed(spec, include_chrome=True, **kw)


# ── P0-1：单点 series 不再整页空白 ───────────────────────────────────────


def test_p0_single_point_series_keeps_page():
    chart = {"type": "line", "title": "L", "series": [
        {"name": "a", "data": [{"name": "x", "value": 1}]},
        {"name": "b", "data": [{"name": "x", "value": 1},
                               {"name": "y", "value": 2},
                               {"name": "z", "value": 3}]}]}
    comp = _compile(_base_spec([
        {"id": "t", "type": "title", "options": {"text": "单点"}},
        {"id": "cp", "type": "chart_panel", "options": {"chart": chart}},
    ]))
    assert len(comp.svg) > 1000, "整页编译不得被单组件缺陷放大为空白 fallback"
    assert "chrome-title" in comp.svg
    assert 'data-kind="chart"' in comp.svg


# ── P1-2：截断回执保真 ──────────────────────────────────────────────────


def test_p1_point_truncation_reports_raw_count():
    pts = [{"name": f"p{i}", "value": float(i)} for i in range(70)]
    comp = _compile(_base_spec([
        {"id": "cp", "type": "chart_panel",
         "options": {"chart": {"type": "line", "title": "长序列",
                               "data": pts}}}]))
    notes = [d["detail"] for d in comp.diagnostics
             if d["code"] == "publication_layout_truncated"]
    assert any("70" in n and "64" in n for n in notes), notes


def test_p1_bar_family_uses_categories_detail():
    bars = [{"name": f"c{i}", "value": float(i)} for i in range(15)]
    comp = _compile(_base_spec([
        {"id": "cp", "type": "chart_panel",
         "options": {"chart": {"type": "bar", "title": "长类目",
                               "data": bars}}}]))
    notes = [d["detail"] for d in comp.diagnostics
             if d["code"] == "publication_layout_truncated"]
    assert any("categories" in n for n in notes), notes
    assert not any(n.startswith("chart points") for n in notes), (
        "条级截断不得复用点级文案")


def test_p1_line_family_not_counted_against_bars():
    pts = [{"name": f"p{i}", "value": float(i)} for i in range(15)]
    comp = _compile(_base_spec([
        {"id": "cp", "type": "chart_panel",
         "options": {"chart": {"type": "line", "title": "线", "data": pts}}}]))
    notes = [d["detail"] for d in comp.diagnostics
             if d["code"] == "publication_layout_truncated"]
    assert not any("categories" in n for n in notes), (
        "line 族不受 12 条类目上限约束（此前误套）")


# ── P1-3：多 colorbar gradient id 唯一 ──────────────────────────────────


def test_p1_multiple_colorbars_unique_gradient_ids():
    layers = [
        {"id": "l1", "source": "s1", "type": "circle",
         "paint": {"circle-color": "#2563eb", "circle-radius": 4},
         "visible": True,
         "legend_spec": {"type": "continuous", "title": "A", "min": 0,
                         "max": 10, "unit": "m",
                         "palette_colors": ["#f7fbff", "#08306b"]}},
        {"id": "l2", "source": "s1", "type": "circle",
         "paint": {"circle-color": "#ef4444", "circle-radius": 4},
         "visible": True,
         "legend_spec": {"type": "continuous", "title": "B", "min": 0,
                         "max": 5, "unit": "h",
                         "palette_colors": ["#fff7bc", "#662506"]}},
    ]
    spec = _base_spec([
        {"id": "cb1", "type": "continuous_colorbar",
         "options": {"layerId": "l1"}},
        {"id": "cb2", "type": "continuous_colorbar",
         "options": {"layerId": "l2"}},
    ])
    spec["layers"] = layers
    comp = _compile(spec)
    ids = set(re.findall(r'linearGradient id="([^"]+)"', comp.svg))
    assert len(ids) == 2, f"gradient id 必须互异: {ids}"
    for gid in ids:
        assert comp.svg.count(f"url(#{gid})") == 1


# ── P1-4：callout 成功不重复画静态卡 ────────────────────────────────────


def test_p1_callout_success_skips_static_card():
    spec = _base_spec([
        {"id": "an", "type": "annotation",
         "options": {"text": "锚定注记\n第二行",
                     "anchorCoordinate": {"lng": 10.0, "lat": 40.0}}}])
    comp = _compile(spec)
    assert comp.svg.count('class="chrome-annotation"') == 1, (
        "callout 投影成功 → 只画 callout（canvas 同语义），静态卡不画")


# ── P1-6：collapsed 面板 → 折叠条 ───────────────────────────────────────


def test_p1_collapsed_panel_renders_folded_bar():
    spec = _base_spec([
        {"id": "sp", "type": "statistics_panel",
         "placement": {"collapsed": True},
         "options": {"stats": {"title": "统计卡",
                               "items": [{"label": "均值", "value": 1}]}}}])
    comp = _compile(spec)
    assert "已折叠" in comp.svg
    assert "均值" not in comp.svg, "折叠面板不得展开正文行（E-2 parity）"
    assert 'data-kind="statistics"' in comp.svg


# ── P1-5：第四键 mid-run 漂移守卫（源级接线 + 条件语义）─────────────────


def test_p1_product_state_drift_guard_wired():
    import inspect

    from app.services.gis_harness.completion import pipeline

    src = inspect.getsource(pipeline)
    assert "validated_product_fp" in src
    # 守卫必须在持久化（save_session_plan）之前比较 fresh 指纹
    guard_pos = src.index("product state changed mid-run")
    save_pos = src.index("await save_session_plan(fresh)", guard_pos)
    assert guard_pos < save_pos


def test_p1_product_state_drift_guard_condition():
    from app.services.gis_harness.workflow_instance import (
        product_state_fingerprint,
    )

    ch_before = {"export_receipts": []}
    ch_after = {"export_receipts": [
        {"format": "pdf", "revision": "7", "created_at": 1.0,
         "filename": "late.pdf"}]}
    fp_before = product_state_fingerprint(ch_before)
    fp_after = product_state_fingerprint(ch_after)
    assert fp_before != fp_after, "守卫条件必须能识别 mid-run receipt 落章"


# ── P2 系列 ─────────────────────────────────────────────────────────────


def test_p2_non_chrome_types_exempt_from_catch_all():
    comp = _compile(_base_spec([
        {"id": "xl", "type": "export_layout", "options": {}},
        {"id": "bm", "type": "basemap", "options": {}},
        {"id": "ll", "type": "label_layer", "options": {}},
    ]))
    assert comp.omitted_components == [], (
        "结构上非 chrome 的类型不得发 publication_component_omitted 噪音")


def test_p2_table_height_estimate_matches_render():
    from app.services.mapspec_to_svg import _table_height

    rows = [[f"r{i}", i] for i in range(12)]
    h = _table_height(rows, total=12)
    # 渲染几何：12 头 + (8 行 + 1 表头)×14 + 尾注 12 + 内边距
    assert h == pytest.approx(32.0 + 20.0 + 9 * 14.0 + 12.0)


def test_p2_canvas_dpi_zero_is_unrecorded():
    from app.api.routes import map as map_mod

    assert map_mod._clamp_canvas_dpi(0) == 0
    assert map_mod._clamp_canvas_dpi(None) == 0
    assert map_mod._clamp_canvas_dpi(96) == 96


def test_p2_svg_text_rule_only_with_embedded_font(monkeypatch):
    from app.services import publication_export as pe

    monkeypatch.setattr(pe, "_probe_cjk_font", lambda: True)
    monkeypatch.setattr(pe, "_FONT_FACE_CACHE", None)
    html = pe._svg_to_page_html("<svg/>", 210.0, 297.0, title="t")
    assert "svg, svg text" not in html, (
        "无内嵌字体时不得无声改变文本字体解析面")
    monkeypatch.setattr(pe, "_probe_cjk_font", lambda: False)
    monkeypatch.setattr(pe, "_FONT_FACE_CACHE", None)
    html2 = pe._svg_to_page_html("<svg/>", 210.0, 297.0, title="t")
    assert "svg, svg text" in html2


def test_p2_sidecar_gc_orphan_and_with_primary(tmp_path, monkeypatch):
    """诊断 sidecar 与 .owner 同纪律：随主件删除；孤儿超龄清除。"""
    import asyncio
    import os
    import time

    from app.services import artifact_lifecycle as lifecycle
    from app.services import export_paths

    root = tmp_path / "exports"
    root.mkdir()
    monkeypatch.setattr("app.services.export_paths.exports_root",
                        lambda: root)
    aged = time.time() - 30 * 86400
    # 1) 主件仍在的超龄 sidecar 不删（随主件生命周期）
    (root / "map_export_1_aaaaaaaaaaaa.png").write_bytes(b"x")
    side1 = root / "map_export_1_aaaaaaaaaaaa.png.diagnostics.json"
    side1.write_text("{}")
    os.utime(side1, (aged, aged))
    # 2) 主件缺失的孤儿 sidecar（超龄）删除
    side2 = root / "map_export_2_bbbbbbbbbbbb.pdf.diagnostics.json"
    side2.write_text("{}")
    os.utime(side2, (aged, aged))

    result = asyncio.run(lifecycle.sweep_aged_artifacts())
    assert result["exports_removed"] == 1
    assert side1.exists(), "有主件的 sidecar 不删"
    assert not side2.exists(), "孤儿 sidecar 随龄清除"
