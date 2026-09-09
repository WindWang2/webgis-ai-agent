"""V6（ADR-0120 W8）publication 矢量 PDF 领域服务测试。

weasyprint 缺席环境 skip（诚实降级路径由 unavailable 单测覆盖）。
断言真实 PDF：pypdf 结构化读页数 + **文本可提取**（可选文本验收）。
"""
import io

import pytest

from app.lib.cartography.render_diagnostics import RENDER_DIAGNOSTICS
from app.services.publication_export import (
    render_publication_pdf,
)

weasyprint = pytest.importorskip(
    "weasyprint", reason="WeasyPrint not installed (needs pango system libs)"
)

try:
    import pypdf  # noqa: F401

    HAS_PYPDF = True
except ImportError:  # pragma: no cover
    HAS_PYPDF = False


def _spec(frames=None):
    spec = {
        "version": "1.1",
        "sources": {
            "g": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
                            "properties": {"zone": "res"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.5, 39.5]},
                            "properties": {"zone": "ind"},
                        },
                    ],
                },
            },
        },
        "layers": [
            {
                "id": "zones",
                "source": "g",
                "type": "circle",
                "paint": {"circle-radius": 6, "circle-color": "#de2d26"},
                "legend_spec": {
                    "type": "categorical",
                    "field": "zone",
                    "title": "功能区",
                    "categories": [
                        {"key": "res", "color": "#fca5a5", "label": "居住"},
                        {"key": "ind", "color": "#93c5fd", "label": "工业"},
                    ],
                },
                "label": {"field": "zone"},
            },
        ],
        "layout": {
            "components": [
                {"id": "t", "type": "title", "position": "top-center", "options": {"text": "城市专题图"}},
                {"id": "lg", "type": "legend", "position": "bottom-left"},
                {"id": "sb", "type": "scale_bar"},
                {"id": "n", "type": "north_arrow"},
                {"id": "mb", "type": "map_border"},
            ],
        },
    }
    if frames is not None:
        spec["layout"]["frames"] = frames
    return spec


class TestVectorPdfPublication:
    def test_single_page_pdf_with_selectable_text(self):
        result = render_publication_pdf(_spec())
        assert result.pdf[:5] == b"%PDF-"
        assert result.page_count >= 1
        assert result.frames_rendered == 1
        assert any(d["code"] == "pdf_font_fallback" for d in result.diagnostics) or result.font_cjk

    @pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
    def test_text_is_extractable(self):
        """Epic 完成证明核心：矢量 PDF 的文字必须可选可检索。"""
        import pypdf

        result = render_publication_pdf(_spec())
        reader = pypdf.PdfReader(io.BytesIO(result.pdf))
        text = "".join(page.extract_text() or "" for page in reader.pages)
        assert "城市专题图" in text, "标题文本应可提取（真矢量，非栅格）"
        assert "功能区" in text, "图例标题应可提取"

    @pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
    def test_spec_frames_produce_multi_page(self):
        frames = [
            {"id": "f1", "title": "Frame 1", "extent": [115.0, 38.0, 118.0, 41.0]},
            {"id": "f2", "title": "Frame 2", "extent": [110.0, 30.0, 122.0, 42.0],
             "pageSize": {"width": 210, "height": 297}},
        ]
        result = render_publication_pdf(_spec(frames=frames))
        assert result.frames_rendered == 2
        assert result.page_count == 2
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(result.pdf))
        text = "".join(page.extract_text() or "" for page in reader.pages)
        assert "城市专题图" in text  # 每帧都带 chrome

    def test_raster_sources_disclosed_and_omitted(self):
        spec = _spec()
        spec["sources"]["basemap"] = {
            "type": "raster",
            "imageRef": "ref:raster/test",
            "bounds": [110.0, 30.0, 120.0, 40.0],
        }
        result = render_publication_pdf(spec)
        assert any(
            d["code"] == "raster_layer_unavailable_vector_pdf" for d in result.diagnostics
        ), result.diagnostics

    def test_disabled_frame_skipped(self):
        frames = [
            {"id": "on", "extent": [115.0, 38.0, 118.0, 41.0]},
            {"id": "off", "extent": [110.0, 30.0, 122.0, 42.0], "enabled": False},
        ]
        result = render_publication_pdf(_spec(frames=frames))
        assert result.frames_rendered == 1

    def test_forward_version_rejected(self):
        from app.lib.cartography.mapspec_schema import MapSpecSchemaError

        spec = _spec()
        spec["version"] = "9.9"
        with pytest.raises(MapSpecSchemaError) as ei:
            render_publication_pdf(spec)
        assert ei.value.code == "mapspec_forward_version"

    def test_url_fetch_denied(self):
        """SSRF 封闭：deny-all fetcher 对外部资源一律拒绝。"""
        from app.services.publication_export import _deny_url_fetcher

        with pytest.raises(ValueError):
            _deny_url_fetcher("http://169.254.169.254/latest/meta-data")

    def test_new_codes_registered_with_emitters(self):
        for code in (
            "raster_layer_unavailable_vector_pdf",
            "pdf_font_fallback",
            "vector_pdf_unavailable",
        ):
            assert code in RENDER_DIAGNOSTICS
