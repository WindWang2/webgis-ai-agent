
"""#1416: GDAL env refuses HTTP redirects (no Location revalidation hook)."""
from pathlib import Path

def test_rasterio_env_sets_max_redirect_zero():
    src = Path("app/lib/geo_raster/env.py").read_text()
    assert "GDAL_HTTP_MAX_REDIRECT=0" in src
    assert "GDAL_HTTP_MAX_REDIRECT=0" in src.split("rasterio.Env")[1][:400]
