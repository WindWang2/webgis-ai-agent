"""G26 P3 — 混合地图统一编译链端到端（真实 GeoTIFF + 矢量边界 + 标注）。

验收映射：A01（完整 PNG）、A02（SVG 嵌入栅格+矢量文本保留）、A03（PDF
图像对象在场+文字可提取）、A04（多栅格按坐标对齐）、A07（nodata 透明）、
A11（冻结修订不漂移）、A19（嵌入层不得标省略）。

全部走真实编译路径（compile_mapspec_to_svg_detailed / render_publication_pdf
/ rsvg-convert），像素级断言而非「文件存在」。
"""
from __future__ import annotations

import base64
import io
import os
import re

import numpy as np
import pytest
from PIL import Image
from rasterio.transform import from_origin

from app.schemas.raster_spec import (
    RasterExpressionSpec,
    RasterStretchBounds,
)
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed
from app.services.publication_export import render_publication_pdf
from tests.unit.raster_authoring.helpers import write_tif

try:
    import pypdf

    HAS_PYPDF = True
except ImportError:  # pragma: no cover
    HAS_PYPDF = False

RSVG = None
for _c in ("/usr/bin/rsvg-convert", "/usr/local/bin/rsvg-convert"):
    if os.path.isfile(_c):
        RSVG = _c
        break
if RSVG is None:  # pragma: no cover - 能力探测（宿主缺席时跳过 PNG 面）
    try:
        import shutil

        RSVG = shutil.which("rsvg-convert")
    except Exception:
        RSVG = None


def _spec(bands, mode="single", colormap=None, stretch=(0.0, 400.0),
          data_revision="fp:test"):
    return RasterExpressionSpec(
        mode=mode, bands=list(bands),
        stretch=RasterStretchBounds(min=stretch[0], max=stretch[1]),
        colormap=colormap, data_revision=data_revision,
    ).finalize()


def _binding(path, spec, layer_id, source_id, bounds):
    st = os.stat(path)
    return {
        "layer_id": layer_id,
        "source_id": source_id,
        "ref_id": "ref:raster/test",
        "path": str(path),
        "stat_key": [st.st_mtime_ns, st.st_size],
        "spec": spec.model_dump(mode="json"),
        "style_id": spec.style_id,
        "data_revision": spec.data_revision,
        "crs": "EPSG:4326",
        "source_bounds": bounds,
    }


def _gradient_tif(path, *, w=8, h=8, west=10.0, north=25.0, dw=10.0, dn=10.0,
                  nodata=None):
    rows, cols = np.mgrid[0:h, 0:w]
    values = (rows * 10 + cols).astype("float32")[None]
    return write_tif(
        path, bands=1, width=w, height=h, dtype="float32", nodata=nodata,
        values=values, crs="EPSG:4326",
        transform=from_origin(west, north, dw / w, dn / h),
    )


def _mixed_spec(raster_bounds, *, labels=True, extra_raster_layer=False):
    features = [
        {"type": "Feature", "properties": {"name": "西部站", "v": 1},
         "geometry": {"type": "Point", "coordinates": [11.5, 22.0]}},
        {"type": "Feature", "properties": {"name": "东部站", "v": 2},
         "geometry": {"type": "Point", "coordinates": [18.0, 17.0]}},
    ]
    sources = {
        "rsrc": {"type": "numeric_raster",
                 "schema_version": "raster_expression.v1",
                 "refId": "ref:raster/test", "bounds": raster_bounds,
                 "crs": "EPSG:4326", "dataRevision": "fp:test", "bandCount": 1},
        "vec": {"type": "geojson", "inlineData": {
            "type": "FeatureCollection", "features": features}},
    }
    layers = [
        {"id": "raster-layer", "source": "rsrc", "type": "raster",
         "paint": {},
         "raster_style": None,  # 由绑定携带（编译面不重读）
         },
        {"id": "vec-layer", "source": "vec", "type": "circle",
         "paint": {"circle-radius": 6, "circle-color": "#111111"},
         **({"label": {"field": "name"}} if labels else {})},
    ]
    if extra_raster_layer:
        sources["rsrc2"] = dict(sources["rsrc"])
        layers.insert(0, {"id": "raster-under", "source": "rsrc2",
                          "type": "raster", "paint": {}})
    return {"version": "1.9", "sources": sources, "layers": layers,
            "layout": {"components": [
                {"id": "title", "type": "title", "options": {"text": "混合地图出版链 A01-A03"}},
                {"id": "attr", "type": "attribution", "options": {"text": "G26 测试数据"}},
            ]}}


VIEW = [10.0, 15.0, 20.0, 25.0]


class TestMixedSvgCompilation:
    def test_svg_embeds_raster_and_keeps_vector_text(self, tmp_path):
        """A02：SVG 内嵌 data-URI 栅格；矢量文本/标注保留为 <text>。"""
        tif = _gradient_tif(tmp_path / "grad.tif")
        spec = _spec([1], colormap="viridis")
        doc = _mixed_spec(list(VIEW))
        bindings = {"raster-layer": _binding(tif, spec, "raster-layer", "rsrc", list(VIEW))}
        comp = compile_mapspec_to_svg_detailed(
            doc, width=800, height=640, padding=24, include_chrome=True,
            bounds=VIEW, raster_bindings=bindings)
        assert "data:image/png;base64," in comp.svg
        assert 'data-g26-raster="raster-layer"' in comp.svg
        assert "<text" in comp.svg, "矢量文本必须保留可选取"
        assert "西部站" in comp.svg and "东部站" in comp.svg, "标注在场"
        assert "混合地图出版链" in comp.svg, "标题（chrome）在场"
        assert any(d["code"] == "raster_window_embedded" for d in comp.diagnostics)
        assert comp.raster_placements and comp.raster_placements[0]["embedded"]
        # 放置矩形覆盖视图（源=视图同范围）
        p = comp.raster_placements[0]
        assert p["bounds_wsen"] == pytest.approx(VIEW, abs=0.05)

    def test_raster_between_vector_layers_keeps_order(self, tmp_path):
        """A10 前置：栅格夹在矢量层之间 —— <image> 出现在下层矢量之后。"""
        tif = _gradient_tif(tmp_path / "grad.tif")
        spec = _spec([1], colormap="viridis")
        doc = _mixed_spec(list(VIEW))
        # 下层矢量 → 栅格 → 上层矢量（circle 层在 raster 之后）
        bindings = {"raster-layer": _binding(tif, spec, "raster-layer", "rsrc", list(VIEW))}
        comp = compile_mapspec_to_svg_detailed(
            doc, width=800, height=640, bounds=VIEW, raster_bindings=bindings)
        img_pos = comp.svg.find('data-g26-raster="raster-layer"')
        circle_pos = comp.svg.find("西部站")
        assert 0 < img_pos < circle_pos, "栅格先于上层矢量标注（层序保持）"

    def test_missing_binding_discloses_not_silent(self, tmp_path):
        """A19 前置：绑定缺席 → 诚实披露，绝不静默丢层。"""
        doc = _mixed_spec(list(VIEW))
        comp = compile_mapspec_to_svg_detailed(
            doc, width=800, height=640, bounds=VIEW)  # 无 bindings
        diags = [d["code"] for d in comp.diagnostics]
        assert "layer_ref_unavailable" in diags
        assert not any("data:image/png" in comp.svg for _ in [0])
        det = [d for d in comp.diagnostics if d["code"] == "layer_ref_unavailable"]
        assert any("numeric_raster_offline_snapshot_required" in str(d.get("detail"))
                   for d in det)

    def test_disjoint_raster_disclosed_transparent(self, tmp_path):
        """A06：不相交视图 → raster_window_no_overlap 披露 + 无嵌入。"""
        tif = _gradient_tif(tmp_path / "grad.tif")
        spec = _spec([1], colormap="viridis")
        doc = _mixed_spec(list(VIEW))
        bindings = {"raster-layer": _binding(tif, spec, "raster-layer", "rsrc", list(VIEW))}
        comp = compile_mapspec_to_svg_detailed(
            doc, width=800, height=640, bounds=[100.0, 50.0, 110.0, 60.0],
            raster_bindings=bindings)
        assert any(d["code"] == "raster_window_no_overlap" for d in comp.diagnostics)
        assert "data:image/png;base64," not in comp.svg

    def test_window_failure_disclosed_not_silently_swallowed(self, tmp_path):
        """窗口渲染失败（退化视图）→ 诚实披露，绝不走层级宽 except 静默
        消失（review 关键：既有 per-layer except Exception: continue 会吞
        掉栅格窗口异常 —— 本测试锁死披露语义）。"""
        tif = _gradient_tif(tmp_path / "grad.tif")
        spec = _spec([1], colormap="viridis")
        # 单点矢量 → 自动范围退化为零面积 bbox → 窗口渲染 typed 失败
        doc = {
            "version": "1.9",
            "sources": {
                "rsrc": {"type": "numeric_raster",
                         "schema_version": "raster_expression.v1",
                         "refId": "ref:raster/test", "bounds": list(VIEW),
                         "crs": "EPSG:4326", "dataRevision": "fp:test", "bandCount": 1},
                "vec": {"type": "geojson", "inlineData": {"type": "FeatureCollection",
                        "features": [{"type": "Feature", "properties": {"name": "点"},
                                      "geometry": {"type": "Point",
                                                   "coordinates": [12.0, 22.0]}}]}},
            },
            "layers": [
                {"id": "raster-layer", "source": "rsrc", "type": "raster", "paint": {}},
                {"id": "vec-layer", "source": "vec", "type": "circle",
                 "paint": {"circle-radius": 4}, "label": {"field": "name"}},
            ],
        }
        bindings = {"raster-layer": _binding(tif, spec, "raster-layer", "rsrc", list(VIEW))}
        comp = compile_mapspec_to_svg_detailed(
            doc, width=400, height=400, raster_bindings=bindings)  # 无显式 bounds
        diags = [d for d in comp.diagnostics
                 if d["code"] == "layer_ref_unavailable"]
        assert any("numeric_raster_window_failed" in str(d.get("detail"))
                   for d in diags), "窗口失败必须披露"
        assert "data:image/png;base64," not in comp.svg
        assert "点" in comp.svg, "矢量层不受影响"


@pytest.mark.skipif(RSVG is None, reason="rsvg-convert 不可用（PNG 面能力探测）")
class TestMixedPng:
    def test_png_composition_pixel_probe(self, tmp_path):
        """A01/A04：SVG → PNG；灰度渐变角像元与矢量圆点像素核验。"""
        import subprocess

        tif = _gradient_tif(tmp_path / "grad.tif")
        spec = _spec([1], colormap="gray")
        doc = _mixed_spec(list(VIEW))
        bindings = {"raster-layer": _binding(tif, spec, "raster-layer", "rsrc", list(VIEW))}
        comp = compile_mapspec_to_svg_detailed(
            doc, width=400, height=400, padding=0, bounds=VIEW,
            raster_bindings=bindings)
        proc = subprocess.run(
            [RSVG, "-w", "400", "-h", "400", "-o", str(tmp_path / "out.png"), "-"],
            input=comp.svg.encode(), capture_output=True, timeout=60)
        assert proc.returncode == 0, proc.stderr[:200]
        png = np.array(Image.open(tmp_path / "out.png").convert("RGB"))
        assert png.shape[:2] == (400, 400)
        # 视图=栅格范围（无 padding 投影下 (0,0)=西北 值0 → 黑; (399,399)=东南 值77 → 灰49）
        assert int(png[2, 2, 0]) <= 12 and int(png[2, 2, 2]) <= 12
        se = int(png[397, 397, 0])
        assert abs(se - round(77 / 400 * 255)) <= 14, se
        # 栅格在矢量圆点之下：西部站(11.5,22)处应见深色圆点（#111111）
        cx = int((11.5 - VIEW[0]) / (VIEW[2] - VIEW[0]) * 400)
        cy = int((VIEW[3] - 22.0) / (VIEW[3] - VIEW[1]) * 400)
        patch = png[cy - 4:cy + 4, cx - 4:cx + 4]
        assert patch.min() <= 60, f"圆点覆盖处应有深色符号（min={patch.min()}）"

    def test_two_rasters_align_by_coordinates(self, tmp_path):
        """A04：两个不同 extent 栅格按同一坐标对齐（同图叠放不居中错位）。"""
        import subprocess

        # 栅格A：10-20E；栅格B：15-25E（半重叠）—— 值域区分
        tif_a = _gradient_tif(tmp_path / "a.tif", west=10.0, dw=10.0)
        tif_b = _gradient_tif(tmp_path / "b.tif", west=15.0, dw=10.0, nodata=None)
        rows, cols = np.mgrid[0:8, 0:8]
        write_tif(tmp_path / "b.tif", bands=1, width=8, height=8, dtype="float32",
                  values=(200 + rows * 10 + cols).astype("float32")[None],
                  crs="EPSG:4326", transform=from_origin(15.0, 25.0, 10 / 8, 10 / 8))
        spec_a = _spec([1], colormap="gray", stretch=(0.0, 400.0))
        spec_b = _spec([1], colormap="gray", stretch=(0.0, 500.0))
        doc = _mixed_spec([10.0, 15.0, 25.0, 25.0], extra_raster_layer=False)
        # 手工扩展为双栅格层
        doc["sources"]["rsrc_b"] = {"type": "numeric_raster",
                                    "schema_version": "raster_expression.v1",
                                    "refId": "ref:raster/b", "bounds": [15.0, 15.0, 25.0, 25.0],
                                    "crs": "EPSG:4326", "dataRevision": "fp:test", "bandCount": 1}
        doc["layers"].insert(1, {"id": "raster-b", "source": "rsrc_b", "type": "raster",
                                 "paint": {}})
        view = [10.0, 15.0, 25.0, 25.0]
        bindings = {
            "raster-layer": _binding(tif_a, spec_a, "raster-layer", "rsrc", [10.0, 15.0, 20.0, 25.0]),
            "raster-b": _binding(str(tmp_path / "b.tif"), spec_b, "raster-b", "rsrc_b",
                                 [15.0, 15.0, 25.0, 25.0]),
        }
        comp = compile_mapspec_to_svg_detailed(
            doc, width=500, height=500, padding=0, bounds=view,
            raster_bindings=bindings)
        assert len([p for p in comp.raster_placements if p["embedded"]]) == 2
        proc = subprocess.run(
            [RSVG, "-w", "500", "-h", "500", "-o", str(tmp_path / "two.png"), "-"],
            input=comp.svg.encode(), capture_output=True, timeout=60)
        assert proc.returncode == 0
        png = np.array(Image.open(tmp_path / "two.png").convert("RGB"))
        # A 覆盖 10-20E（x 0..200），B 覆盖 15-25E（x 100..500，值 200+ → 亮灰）。
        # x=50 只有 A：值 0*..≈小 → 暗；x=250 只有 B：值 ~228 → 亮。
        left = int(png[250, 50, 0]); right = int(png[250, 250, 0])
        assert left <= 30, f"A-only 区应暗（got {left}）"
        assert right >= 90, f"B-only 区应亮（got {right}）"


@pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
class TestMixedPdf:
    def test_pdf_image_objects_and_extractable_text(self, tmp_path):
        """A03/A19：PDF 真实图像对象 ≥1 + 中文标题/标注可提取 + 嵌入层不披露省略。"""
        tif = _gradient_tif(tmp_path / "grad.tif")
        spec = _spec([1], colormap="viridis")
        doc = _mixed_spec(list(VIEW))
        # 恢复 raster_style（PDF 链 parse 校验需要冻结表达形状）
        doc["layers"][0]["raster_style"] = spec.model_dump(mode="json")
        bindings = {"raster-layer": _binding(tif, spec, "raster-layer", "rsrc", list(VIEW))}
        result = render_publication_pdf(doc, target_dpi=150,
                                        raster_bindings=bindings)
        assert result.pdf[:5] == b"%PDF-"
        reader = pypdf.PdfReader(io.BytesIO(result.pdf))
        assert len(reader.pages) >= 1
        page = reader.pages[0]

        def walk(obj, seen=None):
            from pypdf.generic import DictionaryObject

            if seen is None:
                seen = set()
            out = []
            try:
                obj = obj.get_object()
            except Exception:
                return out
            if id(obj) in seen:
                return out
            seen.add(id(obj))
            if isinstance(obj, DictionaryObject):
                if str(obj.get("/Subtype")) == "/Image":
                    out.append(obj)
                for v in obj.values():
                    out += walk(v, seen)
            return out

        images = walk(page.get_object())
        assert len(images) >= 1, "混合 PDF 必须包含真实图像对象"
        assert int(images[0].get("/Width")) >= 8
        text = page.extract_text() or ""
        assert "混合地图出版链" in text, "标题文本可提取"
        assert "西部站" in text, "标注文本可提取"
        # A19：嵌入层不得再披露省略
        assert not any(d["code"] == "raster_layer_unavailable_vector_pdf"
                       for d in result.diagnostics)
        assert any(d["code"] == "raster_window_embedded" for d in result.diagnostics)
        assert result.raster_layers and result.raster_layers[0]["layer_id"] == "raster-layer"
        assert result.raster_layers[0]["style_id"] == spec.style_id

    def test_pdf_without_bindings_still_discloses_omission(self, tmp_path):
        """无绑定调用面：既有披露语义不变（numeric_raster 补入披露词表）。"""
        spec = _spec([1], colormap="viridis")
        doc = _mixed_spec(list(VIEW))
        doc["layers"][0]["raster_style"] = spec.model_dump(mode="json")
        result = render_publication_pdf(doc, target_dpi=150)
        codes = [d["code"] for d in result.diagnostics]
        assert "raster_layer_unavailable_vector_pdf" in codes, \
            "numeric_raster 无绑定 → PDF 披露省略（G26 前该源类型漏披露）"
        assert not result.raster_layers


class TestNodataAndAlpha:
    def test_nodata_transparent_in_composed_png(self, tmp_path):
        """A07：nodata 半区透明（alpha=0），有效半区不透明。"""
        tif = _gradient_tif(tmp_path / "grad.tif", nodata=-9999.0)
        # 左半设 nodata（重写值）
        import rasterio

        with rasterio.open(tif, "r+") as ds:
            arr = ds.read(1)
            arr[:, :4] = -9999.0
            ds.write(arr, 1)
        spec = _spec([1], colormap="gray")
        doc = _mixed_spec(list(VIEW), labels=False)
        bindings = {"raster-layer": _binding(tif, spec, "raster-layer", "rsrc", list(VIEW))}
        comp = compile_mapspec_to_svg_detailed(
            doc, width=200, height=200, padding=0, bounds=VIEW,
            raster_bindings=bindings)
        m = re.search(r'data-g26-raster="raster-layer"[^>]*', comp.svg)
        uri = re.search(r'href="(data:image/png;base64,[^"]+)"',
                        comp.svg[comp.svg.find("<image"):])
        assert uri, "嵌入 image 元素携带 data URI"
        png_b = base64.b64decode(uri.group(1).split(",", 1)[1])
        px = np.array(Image.open(io.BytesIO(png_b)).convert("RGBA"))
        assert px.shape[0] >= 8
        half = px.shape[1] // 2
        assert px[:, : half - 2, 3].max() == 0, "nodata 半区全透明"
        assert px[:, half + 2:, 3].min() == 255, "有效半区不透明"


class TestFreezeConsistency:
    def test_frozen_spec_style_id_flows_to_manifest(self, tmp_path):
        """A11 前置：嵌入回执携带冻结 style_id/data_revision（不重估）。"""
        tif = _gradient_tif(tmp_path / "grad.tif")
        spec = _spec([1], colormap="viridis")
        doc = _mixed_spec(list(VIEW), labels=False)
        bindings = {"raster-layer": _binding(tif, spec, "raster-layer", "rsrc", list(VIEW))}
        comp = compile_mapspec_to_svg_detailed(
            doc, width=200, height=200, bounds=VIEW, raster_bindings=bindings)
        p = comp.raster_placements[0]
        assert p["style_id"] == spec.style_id
        assert p["data_revision"] == "fp:test"


@pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
class TestMultiFramePlacements:
    def test_four_frames_each_extent_aligned_unique_ids(self, tmp_path):
        """A17/A18：4 帧各自 extent → 每帧窗口对齐；PDF 4 页各含图像；
        槽/帧命名空间不串（raster_layers 每帧独立 bounds）。"""
        tif = _gradient_tif(tmp_path / "grad.tif", w=16, h=16)
        spec = _spec([1], colormap="viridis", stretch=(0.0, 250.0))
        doc = _mixed_spec([10.0, 15.0, 20.0, 25.0], labels=True)
        doc["layers"][0]["raster_style"] = spec.model_dump(mode="json")
        # 4 帧不同 extent（西北象限 → 东南象限各一）
        doc["layout"]["frames"] = [
            {"id": "f-nw", "enabled": True, "extent": [10.0, 20.0, 15.0, 25.0]},
            {"id": "f-ne", "enabled": True, "extent": [15.0, 20.0, 20.0, 25.0]},
            {"id": "f-sw", "enabled": True, "extent": [10.0, 15.0, 15.0, 20.0]},
            {"id": "f-se", "enabled": True, "extent": [15.0, 15.0, 20.0, 20.0]},
        ]
        bindings = {"raster-layer": _binding(
            tif, spec, "raster-layer", "rsrc", [10.0, 15.0, 20.0, 25.0])}
        result = render_publication_pdf(doc, target_dpi=110,
                                        raster_bindings=bindings)
        assert result.page_count == 4, "每帧一页"
        assert len(result.raster_layers) == 4, "每帧一条嵌入回执"
        bounds_list = [tuple(p["bounds_wsen"] or ()) for p in result.raster_layers]
        # 各帧窗口互不相同且与帧 extent 对齐（西北帧 bounds 北界 > 东南帧）
        assert len(set(bounds_list)) == 4
        nw = next(p for p in result.raster_layers if p["page_id"] == "f-nw")
        se = next(p for p in result.raster_layers if p["page_id"] == "f-se")
        assert nw["bounds_wsen"][3] > se["bounds_wsen"][3] + 2.0, \
            "西北帧覆盖北半、东南帧覆盖南半（extent 正确分窗）"
        # PDF 每页恰绘制一个栅格图像（分层 <img> 面）。WeasyPrint 会把全
        # 文档图像放共享 /Resources —— 计数必须按**内容流 Do 算子**（页实际
        # 绘制的 XObject 引用），不能 walk 资源字典。
        reader = pypdf.PdfReader(io.BytesIO(result.pdf))
        assert len(reader.pages) == 4
        import re as _re

        for i, page in enumerate(reader.pages):
            raw = page.get_contents().get_data()
            drawn = set(_re.findall(rb"/(\w+) Do", raw))
            xo = page.get("/Resources", {}).get("/XObject", {}) or {}
            image_names = {
                name[1:].decode() if isinstance(name, bytes) else str(name)[1:]
                for name, ref in xo.items()
                if ref.get_object().get("/Subtype") == "/Image"
            }
            drawn_images = {d.decode() for d in drawn} & image_names
            assert len(drawn_images) == 1, (
                f"页 {i} 恰绘制一个栅格图像（got {drawn_images}；"
                f"其余 Do 为矢量 Form XObject）")
            text = page.extract_text() or ""
            assert "西部站" in text or "东部站" in text or "混合地图出版链" in text

    def test_frames_disjoint_extent_disclosed_per_frame(self, tmp_path):
        """A18 边界：某帧 extent 与栅格无交集 → 该帧 no_overlap 披露不嵌图。"""
        tif = _gradient_tif(tmp_path / "grad.tif")
        spec = _spec([1], colormap="viridis")
        doc = _mixed_spec([10.0, 15.0, 20.0, 25.0], labels=False)
        doc["layers"][0]["raster_style"] = spec.model_dump(mode="json")
        doc["layout"]["frames"] = [
            {"id": "f-hit", "enabled": True, "extent": [10.0, 15.0, 15.0, 25.0]},
            {"id": "f-miss", "enabled": True, "extent": [100.0, 50.0, 110.0, 60.0]},
        ]
        bindings = {"raster-layer": _binding(
            tif, spec, "raster-layer", "rsrc", [10.0, 15.0, 20.0, 25.0])}
        result = render_publication_pdf(doc, target_dpi=96,
                                        raster_bindings=bindings)
        assert result.page_count == 2
        by_page = {p["page_id"]: p for p in result.raster_layers}
        assert by_page["f-hit"]["embedded"] is True
        assert by_page["f-miss"]["overlap"] == "none"
        assert by_page["f-miss"]["embedded"] is False
