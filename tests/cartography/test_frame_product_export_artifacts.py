"""G15 WP12 —— 真实导出工件核验（2×2 图版：SVG 结构 + PDF 文本 + PNG 渲染）。

真实旅程（任务书 §6）：同一产品导出三格式，语义一致 —— 各画幅字段/时间/
分级/范围/图例一致；SVG clipPath 不跨帧碰撞；PNG 经 pdftoppm 实际栅格化
（非 mock timing）；跨格式标题/标签一致。
"""
from __future__ import annotations

import json
import os
import subprocess
import xml.etree.ElementTree as ET

import pytest

from app.lib.cartography.publication_ir import AtlasPolicy, plan_publication_pages
from app.services.publication_export import (
    compile_publication_page,
    render_publication_pdf,
)

pytestmark = pytest.mark.cartography

ARTIFACT_DIR = os.environ.get("G15_EXPORT_ARTIFACTS", "")


def _four_year_document() -> dict:
    # 每年跨多个分级（真实感 + 避免全年同级的近空白帧）。
    year_offsets = {2020: (0.0, 4.0, 8.0, 12.0),
                    2022: (6.0, 10.0, 14.0, 18.0),
                    2024: (10.0, 14.0, 18.0, 19.5),
                    2026: (14.0, 17.0, 19.0, 19.8)}
    feats = []
    for y in (2020, 2022, 2024, 2026):
        for d in range(4):
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[
                    [104.0 + d * 0.1, 30.6], [104.1 + d * 0.1, 30.6],
                    [104.1 + d * 0.1, 30.7], [104.0 + d * 0.1, 30.7],
                    [104.0 + d * 0.1, 30.6]]]},
                "properties": {"year": y, "district": f"d{d}",
                               "per_10k": 20.0 + year_offsets[y][d]},
            })
    tw = lambda y: {  # noqa: E731
        "frame_id": f"fp-t-{y}", "label": str(y),
        "start": f"{y}-01-01T00:00:00Z", "end": f"{y + 1}-01-01T00:00:00Z",
        "time_field": "year", "time_representation": "year_int",
        "granularity": "year", "timezone": "UTC", "layer_id": "lyr-d",
    }
    return {
        "version": "2.0",
        "view": {"center": [104.15, 30.65], "zoom": 10},
        "sources": {"src-d": {"type": "geojson",
                              "inlineData": {"type": "FeatureCollection",
                                             "features": feats}}},
        "layers": [{
            "id": "lyr-d", "type": "fill", "source": "src-d",
            "legend_spec": {
                "type": "graduated", "field": "per_10k",
                "breaks": [20.0, 26.5, 33.0, 40.0],  # 局部分级故意不同于共享域（review#2 unmask）
                "palette_colors": ["#c6dbef", "#6baed6", "#2171b5"],
                "labels": ["20 – 27", "27 – 34", "34 – 40"],
                "palette": "Blues", "method": "equal_interval", "k": 3,
                "nodata": {"label": "No data", "color": "#cccccc"},
            },
        }],
        "layout": {"frames": [
            {"id": f"fp-t-{y}", "title": str(y),
             "extent": [103.95, 30.55, 104.45, 30.78]}
            for y in (2020, 2022, 2024, 2026)
        ]},
        "frame_product": {
            "schema_version": "frame_product.v1", "enabled": True,
            "product_kind": "time_series",
            "grid": {"columns": 2, "title": "成都每万人小学学位（2020-2026）"},
            "frames": [
                {"frame_id": f"fp-t-{y}", "legend": "shared",
                 "data": {"ref": "ref:ind", "layer_id": "lyr-d",
                          "field": "per_10k", "content_revision": 9,
                          "time_window": tw(y)}}
                for y in (2020, 2022, 2024, 2026)
            ],
            "shared_expression": {
                "field": "per_10k", "semantics": "other",
                "method": "equal_interval", "k": 3, "palette": "Blues",
                "breaks": [20.0, 27.0, 34.0, 40.0],
                "domain_status": "complete",
                "per_frame_counts": {f"fp-t-{y}": 4 for y in (2020, 2022, 2024, 2026)},
                "sample_size": 16, "eligibility": ["union_breaks"],
            },
        },
    }


@pytest.fixture(scope="module")
def product_2x2():
    doc = _four_year_document()
    ir = plan_publication_pages(doc, atlas=AtlasPolicy(driver="product"))
    page = ir.pages[0]
    outcome = compile_publication_page(
        doc, page, ir, frames_lookup=[], target_dpi=150,
        max_labels=60, svg_budget_remaining=8 * 1024 * 1024)
    return doc, ir, outcome


class TestSvgStructure:
    def test_2x2_slots_with_unique_clips(self, product_2x2):
        _, _, outcome = product_2x2
        root = ET.fromstring(outcome.svg)
        ns = "{http://www.w3.org/2000/svg}"
        clips = root.findall(f".//{ns}clipPath")
        assert len(clips) == 4
        ids = [c.get("id") for c in clips]
        assert len(set(ids)) == 4  # 不跨帧碰撞
        # 槽标题（4 年份）+ 页标题 + 共享图例带标签。
        texts = [t.text or "" for t in root.findall(f".//{ns}text")]
        joined = " ".join(texts)
        for y in ("2020", "2022", "2024", "2026"):
            assert y in joined
        assert "成都每万人小学学位" in joined
        assert "Shared classification" in joined

    def test_artifact_written_when_requested(self, product_2x2, tmp_path):
        if not ARTIFACT_DIR:
            pytest.skip("set G15_EXPORT_ARTIFACTS to keep artifacts")
        _, _, outcome = product_2x2
        out = tmp_path / "g15_product_2x2.svg"
        out.write_text(outcome.svg, encoding="utf-8")
        os.replace(out, os.path.join(ARTIFACT_DIR, "g15_product_2x2.svg"))


class TestPdfReal:
    def test_pdf_text_layer_semantics(self, product_2x2, tmp_path):
        pytest.importorskip("weasyprint")
        import pypdf

        doc, ir, _ = product_2x2
        result = render_publication_pdf(
            doc, title="成都四年对比", atlas=AtlasPolicy(driver="product"))
        assert result.pdf[:5] == b"%PDF-"
        assert result.page_count == 1
        reader = pypdf.PdfReader(
            __import__("io").BytesIO(result.pdf))
        text = reader.pages[0].extract_text() or ""
        # 跨格式语义一致：PDF 文本层携带与 SVG 相同的槽标题/页标题。
        for y in ("2020", "2022", "2024", "2026"):
            assert y in text
        assert "成都每万人小学学位" in text
        # review#2：PDF 共享图例带 = 共享域标签；局部边界 26.5 不得出现。
        assert "20 – 27" in text
        assert "26.5" not in text

    def test_pdf_rasterized_to_png_real(self, product_2x2, tmp_path):
        pytest.importorskip("weasyprint")
        if subprocess.run(["which", "pdftoppm"], capture_output=True).returncode:
            pytest.skip("pdftoppm unavailable")
        doc, ir, _ = product_2x2
        result = render_publication_pdf(
            doc, title="成都四年对比", atlas=AtlasPolicy(driver="product"))
        pdf_path = tmp_path / "g15_product_2x2.pdf"
        pdf_path.write_bytes(result.pdf)
        png_path = tmp_path / "g15_product_2x2"
        proc = subprocess.run(
            ["pdftoppm", "-png", "-r", "72", str(pdf_path), str(png_path)],
            capture_output=True, timeout=120)
        assert proc.returncode == 0, proc.stderr.decode()[:400]
        produced = sorted(tmp_path.glob("g15_product_2x2-*.png"))
        assert produced, "pdftoppm produced no png"
        png = produced[0]
        # 像素级核验：四个画幅地图区必须真实着墨（空白帧 = 空产品页）。
        from PIL import Image

        img = Image.open(png).convert("RGB")
        sx, sy = img.size[0] / 1680.0, img.size[1] / 1188.0
        margin, gap, tb = 0.06, 0.02, 0.08
        cw = (1 - 2 * margin - gap) / 2
        ch = (1 - 2 * margin - 0.12 - gap) / 2
        inks = []
        for r in range(2):
            for c in range(2):
                x = margin + c * (cw + gap)
                y = margin + r * (ch + gap) + tb * ch
                box = img.crop((
                    int(x * 1680 * sx) + 4, int(y * 1188 * sy) + 4,
                    int((x + cw) * 1680 * sx) - 4,
                    int((y + ch * (1 - tb)) * 1188 * sy) - 4))
                data = list(box.getdata())
                inks.append(
                    sum(1 for p in data if p != (255, 255, 255))
                    / max(len(data), 1))
        assert all(f > 0.02 for f in inks), (
            f"map frames blank in raster: {inks}")
        if ARTIFACT_DIR:
            os.replace(png, os.path.join(ARTIFACT_DIR, "g15_product_2x2.png"))
            os.replace(pdf_path, os.path.join(ARTIFACT_DIR, "g15_product_2x2.pdf"))
