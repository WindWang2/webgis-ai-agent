"""Meaningful numeric failure regressions found while reviewing generic lint."""
import numpy as np
import pytest


@pytest.mark.parametrize("box", [(float("nan"), 0, 1, 1), (0, float("inf"), 1, 1), (0, 0, float("inf"), 1), (0, 0, 1, float("nan"))])
def test_prompt_box_requires_finite_origins_and_extents(box):
    from app.lib.modelops.promptable import PromptSpec
    from app.lib.modelops.errors import DescriptorError
    with pytest.raises(DescriptorError, match="non-finite"):
        PromptSpec(boxes=(box,))


def test_resampling_does_not_reuse_source_bounds_as_target_coordinates(tmp_path, monkeypatch):
    import rasterio
    from rasterio.transform import from_origin
    from app.lib.geo_analysis import raster_math
    path = tmp_path / "bounds.tif"
    with rasterio.open(path, "w", driver="GTiff", width=4, height=4, count=1, dtype="float32", crs="EPSG:4326", transform=from_origin(10, 40, .01, .01)) as dataset:
        dataset.write(np.ones((1, 4, 4), dtype=np.float32))

    def unavailable(*args, **kwargs):
        raise ValueError("projection unavailable")

    monkeypatch.setattr(raster_math, "transform_bounds", unavailable)
    with pytest.raises(ValueError, match="Cannot transform raster bounds"):
        raster_math.resample_raster(str(path), 1000, target_crs="EPSG:3857")
    assert not (tmp_path / "bounds_resampled.tif").exists()
