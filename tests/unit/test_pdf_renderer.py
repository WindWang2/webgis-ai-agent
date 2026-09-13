"""Unit tests for Deep PDF Cartography Engine."""
import io
import pytest
from PIL import Image
from app.lib.cartography.pdf_renderer import generate_map_pdf
from app.lib.cartography.layout_description import LayoutInput, build_publication_layout


def _create_sample_png_bytes() -> bytes:
    """Create a sample 100x100 RGB PNG in memory."""
    img = Image.new("RGB", (100, 100), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_generate_map_pdf_valid_image():
    """测试将有效图片合成 A4 横向专题地图 PDF"""
    sample_bytes = _create_sample_png_bytes()
    pdf_bytes = generate_map_pdf(sample_bytes, title="北京土地利用现状图")

    assert pdf_bytes is not None
    assert len(pdf_bytes) > 0
    # PDF magic bytes header check
    assert pdf_bytes.startswith(b"%PDF")


def test_generate_map_pdf_custom_metadata():
    """测试包含自定义标题、副标题、作者和比例尺信息的 PDF 合成"""
    sample_bytes = _create_sample_png_bytes()
    pdf_bytes = generate_map_pdf(
        img_bytes=sample_bytes,
        title="海淀区公园绿地分布图",
        subtitle="2026年Q2统计数据",
        author="GIS 专家",
        scale_text="1:25,000",
    )

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_generate_map_pdf_invalid_bytes_raises_value_error():
    """测试传递损坏/非图片字节流时抛出 ValueError"""
    invalid_bytes = b"NOT_A_REAL_IMAGE"
    with pytest.raises(ValueError, match="Invalid or unparseable image bytes"):
        generate_map_pdf(invalid_bytes)


def test_generate_map_pdf_empty_bytes_raises_value_error():
    """测试传递空字节流时抛出 ValueError"""
    with pytest.raises(ValueError, match="img_bytes cannot be empty"):
        generate_map_pdf(b"")


# ── ac-08（ADR-0157 P6）：出版整饰 + 版面描述 IR 接线 ──────────────────


def _publication_layout() -> dict:
    """与前端 buildPublicationLayout 同源的 IR（scale-math 单源数字）。"""
    return build_publication_layout(
        LayoutInput(
            paper_size="A4",
            orientation="landscape",
            dpi=300,
            frame_width=2480,
            frame_height=1754,
            request_title="海淀区公园绿地分布图",
            meters_per_pixel=100,
        )
    )


def test_generate_map_pdf_with_layout_ir_and_legend():
    """IR 在场 → 指北针/比例尺整饰 + 图例框入 PDF（P6：补齐缺失整饰）。"""
    sample_bytes = _create_sample_png_bytes()
    layout = _publication_layout()
    assert layout["scaleBar"]["label"] == "10 km"  # scale-math 单源数字

    pdf_bytes = generate_map_pdf(
        img_bytes=sample_bytes,
        title="海淀区公园绿地分布图",
        layout=layout,
        legend_items=[
            {"label": "高覆盖", "color": "#22c55e"},
            {"label": "中覆盖", "color": "#eab308"},
            {"label": "低覆盖", "color": "#ef4444"},
        ],
    )
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_generate_map_pdf_layout_without_scale_bar_still_renders():
    """无 metersPerPixel → IR.scaleBar 缺席 → 不画比例尺（不虚构），PDF 正常产出。"""
    sample_bytes = _create_sample_png_bytes()
    layout = build_publication_layout(
        LayoutInput(
            paper_size="A4",
            orientation="landscape",
            dpi=150,
            frame_width=2480,
            frame_height=1754,
            request_title="无比例尺图",
        )
    )
    assert layout["scaleBar"] is None
    pdf_bytes = generate_map_pdf(img_bytes=sample_bytes, layout=layout)
    assert pdf_bytes.startswith(b"%PDF")
