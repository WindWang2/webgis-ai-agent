"""Unit tests for Deep PDF Cartography Engine."""
import io
import pytest
from PIL import Image
from app.lib.cartography.pdf_renderer import generate_map_pdf


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
