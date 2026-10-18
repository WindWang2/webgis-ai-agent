"""Deep-review carto-platform CP-06 / CP-16：栅格 → PDF 解码上限与并发闸。"""

from __future__ import annotations

import io
import threading

import pytest

from app.lib.cartography import pdf_renderer


def _png(w: int, h: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_cp06_oversized_image_rejected_before_decode(monkeypatch):
    monkeypatch.setattr(pdf_renderer, "MAX_RASTER_PDF_PIXELS", 100 * 100)
    small_bytes_big_pixels = _png(400, 400)  # 高压缩：字节小、像素多
    assert len(small_bytes_big_pixels) < 10_000
    with pytest.raises(ValueError, match="limited to"):
        pdf_renderer.generate_map_pdf(small_bytes_big_pixels)


def test_cp06_normal_image_still_renders():
    pdf = pdf_renderer.generate_map_pdf(_png(64, 48), title="t")
    assert pdf.startswith(b"%PDF")


def test_cp06_concurrency_gate_rejects_when_full(monkeypatch):
    gate = threading.BoundedSemaphore(1)
    monkeypatch.setattr(pdf_renderer, "_RASTER_PDF_GATE", gate)
    monkeypatch.setattr(pdf_renderer, "RASTER_PDF_GATE_TIMEOUT_S", 0.05)
    assert gate.acquire()
    try:
        with pytest.raises(pdf_renderer.RasterPdfBusyError):
            pdf_renderer.generate_map_pdf(_png(8, 8))
    finally:
        gate.release()


def test_cp16_no_pyplot_global_figures_left():
    import matplotlib.pyplot as plt

    before = plt.get_fignums()
    pdf_renderer.generate_map_pdf(_png(16, 16))
    assert plt.get_fignums() == before
