"""Unit tests for Unified Raster Cartography Converter."""
import numpy as np
from app.services.raster_cartography_converter import (
    convert_raster_to_mapspec_layer,
    is_raster_source,
)


def test_is_raster_source_detection():
    payload = {
        "array": np.zeros((10, 10)),
        "bounds": [100.0, 20.0, 105.0, 25.0],
    }
    assert is_raster_source(payload) is True
    assert is_raster_source({"type": "FeatureCollection"}) is False


def test_convert_raster_without_session_dir():
    payload = {
        "array": np.array([[0.1, 0.5], [0.8, 0.9]]),
        "bounds": [100.0, 20.0, 105.0, 25.0],
    }
    layer = {"id": "ndvi-layer", "source": "src-ndvi"}

    raster_layer, legend, png, source_data = convert_raster_to_mapspec_layer(payload, layer)

    assert raster_layer["id"] == "ndvi-layer"
    assert raster_layer["type"] == "raster"
    assert "legend_spec" in raster_layer
    assert raster_layer["legend_spec"]["type"] == "continuous"
    assert png is not None
    assert source_data is not None
    assert source_data["imageRef"] is None
    assert source_data["bounds"] == [100.0, 20.0, 105.0, 25.0]


def test_convert_raster_with_session_dir_persists_png(tmp_path):
    payload = {
        "array": np.array([[0.2, 0.4], [0.6, 0.8]]),
        "bounds": [116.0, 39.0, 117.0, 40.0],
    }
    layer = {"id": "dem-layer", "source": "src-dem"}

    raster_layer, legend, png, source_data = convert_raster_to_mapspec_layer(
        payload, layer, session_dir=tmp_path
    )

    assert source_data["imageRef"] == "ref:raster/src-dem"
    saved_png_path = tmp_path / "raster" / "src-dem.png"
    assert saved_png_path.exists()
    assert saved_png_path.stat().st_size > 0
