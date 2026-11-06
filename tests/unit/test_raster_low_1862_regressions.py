"""Regression boundaries for #1862 S3d-008/011/012."""
from types import SimpleNamespace

import numpy as np
import pytest


def test_missing_minimum_does_not_shift_maximum_into_minimum(tmp_path, monkeypatch):
    import rasterio
    from rasterio.transform import from_origin
    from app.services.nature_resource_analyzer import NatureResourceAnalyzer

    path = tmp_path / "input.tif"
    with rasterio.open(path, "w", driver="GTiff", width=2, height=2, count=2,
                       dtype="float32", crs="EPSG:4326", transform=from_origin(110, 30, .1, .1)) as target:
        target.write(np.ones((2, 2, 2), dtype="float32"))
    monkeypatch.setattr("app.utils.path.validate_data_path", lambda value: value)
    monkeypatch.setattr("app.services.scoped_raster_paths.scoped_raster_roots", lambda **kwargs: [tmp_path])
    descriptor = SimpleNamespace(width=2, height=2, to_dict=lambda: {})
    monkeypatch.setattr("app.lib.geo_analysis.raster_windowed.windowed_band_index", lambda *args, **kwargs: {
        "stats": {"min": None, "max": 5, "mean": 3}, "algorithm": "fixture", "band_map": {"red": 1, "nir": 2},
        "descriptor": descriptor, "output_path": str(tmp_path / "out.tif"), "content_fingerprint": "fixture",
    })
    result = NatureResourceAnalyzer.calculate_index(str(path), "ndvi", red_band=1, nir_band=2, output_dir=str(tmp_path / "output"), session_id="owner")
    assert result["success"], result
    assert result["stats"] == {"min": None, "max": 5, "mean": 3}


@pytest.mark.parametrize("classes", [1, 2, 4, 3.5, True])
def test_fixed_bivariate_matrix_refuses_other_class_counts_with_diagnostic(classes):
    from app.services.raster_cartography_converter import build_raster_layer

    array = np.arange(16, dtype=float).reshape(4, 4)
    layer, legend, png = build_raster_layer(source_id="source", bounds=[0, 0, 1, 1], array=array,
        second_array=array[::-1], render_mode="bivariate", render_params={"n": classes})
    assert png is None and legend is None
    assert any("require n=3" in warning for warning in layer["provenance"]["warnings"])


def test_save_png_rejects_path_traversal_before_creating_any_files(tmp_path):
    from app.services.raster_store import save_png

    for raster_id in ["../outside", "../../outside", "/tmp/outside", "a/b", r"a\b"]:
        with pytest.raises(ValueError, match="raster_id"):
            save_png(tmp_path, raster_id, b"bytes")
    assert list(tmp_path.iterdir()) == []
    assert save_png(tmp_path, "safe-id_3", b"bytes") == "ref:raster/safe-id_3"
    assert (tmp_path / "raster" / "safe-id_3.png").read_bytes() == b"bytes"


def test_raster_write_records_only_active_task_capture(tmp_path):
    from app.lib.runtime.ref_capture import capture_created_refs
    from app.services.raster_store import save_png
    with capture_created_refs() as parent:
        save_png(tmp_path, "parent", b"png")
        with capture_created_refs() as sibling:
            save_png(tmp_path, "sibling", b"png")
        save_png(tmp_path, "parent2", b"png")
    assert parent.snapshot() == ["ref:raster/parent", "ref:raster/parent2"]
    assert sibling.snapshot() == ["ref:raster/sibling"]
