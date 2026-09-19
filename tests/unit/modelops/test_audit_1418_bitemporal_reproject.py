
"""#1418: bitemporal + reproject must not silently desync A/B grids."""
from pathlib import Path

def test_engine_rejects_bitemporal_reproject():
    src = Path("app/services/modelops/engine.py").read_text()
    idx = src.find("if report.reproject:")
    assert idx > 0
    window = src[idx: idx + 900]
    assert "source_path_b is not None" in window
    assert "bitemporal change_detection cannot reproject" in window
