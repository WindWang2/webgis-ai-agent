
"""#1404: NaN-aware overview_statistics; dasymetric degenerate continue."""
from __future__ import annotations

import numpy as np



def test_overview_statistics_filters_nan_nodata(monkeypatch):
    from app.lib.geo_raster import windowed as W

    class Meta:
        width = 2
        height = 2
        nodata = float("nan")

    class DS:
        def read(self, band, out_shape=None):
            return np.array([[1.0, np.nan], [3.0, np.nan]], dtype=np.float32)

    class Reader:
        def metadata(self):
            return Meta()
        def _ds(self):
            return DS()

    stats = W.overview_statistics(Reader(), band=1, max_pixels=100)
    assert stats["min"] == 1.0
    assert stats["max"] == 3.0
    assert np.isfinite(stats["mean"])


def test_overview_statistics_filters_nan_pixels_with_numeric_nodata():
    from app.lib.geo_raster import windowed as W

    class Meta:
        width = 2
        height = 2
        nodata = -9999.0

    class DS:
        def read(self, band, out_shape=None):
            return np.array([[1.0, np.nan], [-9999.0, 4.0]], dtype=np.float32)

    class Reader:
        def metadata(self):
            return Meta()
        def _ds(self):
            return DS()

    stats = W.overview_statistics(Reader(), band=1, max_pixels=100)
    assert stats["min"] == 1.0
    assert stats["max"] == 4.0
    assert stats["sample_pixels"] == 2


def test_dasymetric_degenerate_continue_in_source():
    src = open("app/lib/geo_analysis/dasymetric.py").read()
    idx = src.find("n_degenerate_dropped += 1")
    window = src[idx: idx + 120]
    assert "continue" in window
