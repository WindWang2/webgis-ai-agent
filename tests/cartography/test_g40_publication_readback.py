"""G40 出版链回读（S14/S26/S27）——SVG/PNG/PDF 三通道真实产物核验。

- SVG：自包含（defs 内联、无外链资源），离线打开语义 = 解析即完整；
- PNG：rsvg-convert 真实栅格化（含 librsvg CLI 缩放回归 workaround），
  像素 oracle 判定纹理/符号在场（混合感知阈值，非像素全等）；
- PDF：WeasyPrint 真实渲染（缺席时显式 skip，不冒充通过），pdftoppm
  栅格化后同 oracle；
- S14：96/300 DPI 输出物理尺寸一致（SVG 根 mm 宽不变、采样像素随 DPI）；
- S27：manifest 记录冻结 digest 清单。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.lib.cartography.symbol_asset import get_builtin_asset
from app.services.cartographic_assets.catalog import (
    collect_symbol_asset_documents,
)
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

RASTERIZERS = {"rsvg": shutil.which("rsvg-convert")}


def _spec(assets_triple):
    tri, hatch = assets_triple
    pts = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": "a"}, "geometry": {
            "type": "Point", "coordinates": [116.39, 39.91]}},
        {"type": "Feature", "properties": {"kind": "b"}, "geometry": {
            "type": "Point", "coordinates": [116.41, 39.89]}},
    ]}
    zones = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [
            [[116.38, 39.88], [116.42, 39.88], [116.42, 39.92], [116.38, 39.92], [116.38, 39.88]],
            [[116.395, 39.895], [116.405, 39.895], [116.405, 39.905],
             [116.395, 39.905], [116.395, 39.895]]]}}]}
    return {
        "version": "2.1", "view": {"center": [116.4, 39.9], "zoom": 10},
        "sources": {"pts": {"type": "geojson", "inlineData": pts},
                    "zones": {"type": "geojson", "inlineData": zones}},
        "layers": [
            {"id": "zones", "source": "zones", "type": "fill",
             "paint": {"color": "#60a5fa"},
             "symbol_asset": {
                 "asset_id": "cartoasset.builtin.hatch", "revision": 1,
                 "content_digest": hatch.content_digest, "kind": "area_texture",
                 "params": {"spacing_px": 10, "angle_deg": 45,
                            "stroke_width_px": 1.5, "fg_color": "#334155",
                            "bg_color": "#fef08a", "pattern_scale": 1.0}}},
            {"id": "pts", "source": "pts", "type": "circle",
             "paint": {"color": "#2563eb"},
             "symbol_asset": {
                 "asset_id": "cartoasset.builtin.triangle", "revision": 1,
                 "content_digest": tri.content_digest, "kind": "point_symbol",
                 "params": {"size_px": 22, "anchor": "center",
                            "rotation_deg": 90, "opacity": 1.0}}},
        ],
    }


def _docs():
    tri = get_builtin_asset("cartoasset.builtin.triangle")
    hatch = get_builtin_asset("cartoasset.builtin.hatch")
    docs, failures = collect_symbol_asset_documents(None, _spec((tri, hatch)))
    assert not failures
    return (tri, hatch), docs


def _render_png_pixels(svg: str, tmp: Path, w: int, h: int, stem: str):
    from app.lib.cartography.symbol_asset_svg import prepare_svg_for_rasterize

    src = tmp / f"{stem}.svg"
    src.write_text(prepare_svg_for_rasterize(svg, w, h), encoding="utf-8")
    dst = tmp / f"{stem}.png"
    proc = subprocess.run(
        [RASTERIZERS["rsvg"], "-w", str(w), "-h", str(h), "-o", str(dst), str(src)],
        capture_output=True, timeout=60)
    assert proc.returncode == 0 and dst.is_file(), proc.stderr[:200]
    from PIL import Image

    return Image.open(dst).convert("RGBA")


def _has_ink_classes(im):
    """混合感知分类计数（三角蓝 / 纹理暗线 / 黄底）。"""
    tri = dark = yellow = 0
    W, H = im.size
    for x in range(0, W, 2):
        for y in range(0, H, 2):
            r, g, b, a = im.getpixel((x, y))
            if a <= 100:
                continue
            if abs(r - 0x25) < 45 and abs(g - 0x63) < 45 and abs(b - 0xEB) < 45:
                tri += 1
            elif 0.299 * r + 0.587 * g + 0.114 * b < 150:
                dark += 1
            elif r > 230 and g > 220 and b < 190:
                yellow += 1
    return tri, dark, yellow


def test_s26_svg_self_contained_offline_semantics():
    (tri, hatch), docs = _docs()
    comp = compile_mapspec_to_svg_detailed(
        _spec((tri, hatch)), symbol_assets=docs, include_chrome=False)
    svg = comp.svg
    # 自包含：无外链（http/data 引用只允许 data URI 的栅格窗口 —— 本 spec 无）
    assert "http://" not in svg.replace(
        'xmlns="http://www.w3.org/2000/svg"', "")
    assert "<defs>" in svg and 'id="cartoasset-' in svg
    assert 'url(#cartoap-' in svg
    # 解析即完整（XML 合法）
    from defusedxml import ElementTree as SafeET

    SafeET.fromstring(svg)


@pytest.mark.skipif(not RASTERIZERS["rsvg"], reason="rsvg-convert 不可用")
def test_s26_png_real_rasterization_oracle():
    (tri, hatch), docs = _docs()
    comp = compile_mapspec_to_svg_detailed(
        _spec((tri, hatch)), target_dpi=96, symbol_assets=docs,
        include_chrome=False)
    with tempfile.TemporaryDirectory(prefix="g40-png-") as td:
        im = _render_png_pixels(comp.svg, Path(td), 1200, 800, "p96")
        tri_px, dark_px, yellow_px = _has_ink_classes(im)
    assert tri_px >= 4, f"triangle missing: {tri_px}"
    assert dark_px >= 200, f"hatch lines missing: {dark_px}"
    assert yellow_px >= 5000, f"texture bg missing: {yellow_px}"


def test_s14_dpi_invariance_physical_size():
    """同一 mm 版式（width 逻辑 px 恒定）在不同 target_dpi 下：SVG 根
    尺寸不变（viewBox 逻辑轴），采样像素按 DPI 缩放。"""
    (tri, hatch), docs = _docs()
    svg96 = compile_mapspec_to_svg_detailed(
        _spec((tri, hatch)), target_dpi=96, symbol_assets=docs,
        include_chrome=False).svg
    svg300 = compile_mapspec_to_svg_detailed(
        _spec((tri, hatch)), target_dpi=300, symbol_assets=docs,
        include_chrome=False).svg
    import re

    def root_size(svg):
        m = re.search(r'<svg[^>]*width="(\d+)"[^>]*height="(\d+)"', svg)
        return int(m.group(1)), int(m.group(2))

    assert root_size(svg96) == root_size(svg300)  # 物理版面（逻辑 px）一致
    # 纹理间距按 dpi_scale 缩放（96: ×1.333 vs 300: ×4.167）
    def pattern_width(svg):
        m = re.search(r'cartoap-[^"]*"[^>]*width="([\d.]+)"', svg)
        return float(m.group(1))
    w96, w300 = pattern_width(svg96), pattern_width(svg300)
    assert abs(w300 / w96 - 300 / 96) < 0.02, (w96, w300)


@pytest.mark.skipif(
    shutil.which("pdftoppm") is None, reason="pdftoppm 不可用")
def test_s26_pdf_real_weasyprint_render():
    pytest.importorskip("weasyprint", reason="weasyprint 未安装（可选依赖）")


    (tri, hatch), _ = _docs()
    spec = _spec((tri, hatch))
    # mixed 导出要求 numeric raster 层 —— 本 spec 无；直接用 G34 提交面
    # 的 PDF 组装（同一 compile → WeasyPrint 面）。
    from app.services.print_layout_authoring.service import _svg_to_pdf

    docs, failures = collect_symbol_asset_documents(None, spec)
    comp = compile_mapspec_to_svg_detailed(
        spec, target_dpi=150, symbol_assets=docs, include_chrome=True)
    pdf_bytes = _svg_to_pdf(comp.svg, 297.0, 210.0, "G40 回读")
    assert pdf_bytes[:4] == b"%PDF"
    with tempfile.TemporaryDirectory(prefix="g40-pdf-") as td:
        src = Path(td) / "page.pdf"
        src.write_bytes(pdf_bytes)
        subprocess.run(
            ["pdftoppm", "-png", "-r", "96", str(src), str(Path(td) / "pg")],
            check=True, timeout=60)
        pages = sorted(Path(td).glob("pg-*.png"))
        assert pages, "PDF 无页面"
        from PIL import Image

        im = Image.open(pages[0]).convert("RGBA")
        tri_px, dark_px, _ = _has_ink_classes(im)
    assert tri_px >= 2, f"triangle missing in PDF: {tri_px}"
    assert dark_px >= 100, f"hatch missing in PDF: {dark_px}"


def test_s27_manifest_records_frozen_digests():
    """collect_symbol_asset_documents 输出 = 导出 manifest 的 resolved 集
    （出版期冻结证明；失败条目如实列入 unresolved）。"""
    tri, hatch = _docs()[0]
    docs, failures = collect_symbol_asset_documents(None, _spec((tri, hatch)))
    assert {tri.content_digest, hatch.content_digest} == set(docs.keys())
    spec = _spec(_docs()[0])
    spec["layers"].append({"id": "broken", "source": "pts", "type": "circle",
                           "symbol_asset": {
                               "asset_id": "cartoasset.builtin.nope",
                               "revision": 1,
                               "content_digest": "sha256:" + "e" * 64,
                               "kind": "point_symbol"}})
    docs2, failures2 = collect_symbol_asset_documents(None, spec)
    assert any(f["asset_id"] == "cartoasset.builtin.nope" for f in failures2)
    assert len(docs2) == 2  # 其余不受影响
