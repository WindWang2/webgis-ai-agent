"""Unit tests for RasterResourceGuard and RasterResourceExceededError."""
import pytest

from app.lib.geo_analysis.raster_guard import (
    RasterResourceGuard,
    RasterResourceExceededError,
)


def test_guard_allows_valid_raster_size():
    # 1000x1000 pixels is ~4 MB for 1 band float32 — well within limits
    RasterResourceGuard.check_grid(1000, 1000, bounds=(0, 0, 10, 10))


def test_guard_rejects_negative_or_zero_dimensions():
    with pytest.raises(ValueError, match="must be positive integers"):
        RasterResourceGuard.check_grid(-100, 100)

    with pytest.raises(ValueError, match="must be positive integers"):
        RasterResourceGuard.check_grid(100, 0)


def test_guard_rejects_degree_to_meter_unit_explosion():
    # 3°x3° EPSG:4326 to EPSG:3857 @ 1m -> ~334,000 x 334,000 px = ~111 Billion pixels
    width = 334_035
    height = 334_035
    bounds = (116.0, 39.0, 119.0, 42.0)

    with pytest.raises(RasterResourceExceededError) as exc_info:
        RasterResourceGuard.check_grid(width, height, bounds=bounds)

    err = exc_info.value
    assert err.requested_width == width
    assert err.requested_height == height
    assert err.total_pixels == width * height
    assert len(err.suggested_resolutions) >= 3
    # Check structured error output for LLM self-healing
    err_dict = err.to_dict()
    assert err_dict["success"] is False
    assert err_dict["error"] == "RasterResourceExceeded"
    assert "correction_hint" in err_dict
    assert "Suggested target_resolution values" in err_dict["correction_hint"]


def test_suggest_safe_resolutions_scaling():
    bounds = (116.0, 39.0, 119.0, 42.0)  # 3° x 3°
    suggestions = RasterResourceGuard.suggest_safe_resolutions(bounds)
    assert len(suggestions) >= 3
    assert all(s > 0 for s in suggestions)
    assert suggestions == sorted(suggestions)
