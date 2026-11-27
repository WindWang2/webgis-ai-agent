"""WP1 contract tests: CRS discipline, threshold validation, windows."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.services.moving_object.contracts import (
    DwellConfig,
    IngestConfig,
    MovingObjectError,
    RegionEventConfig,
    TimeWindow,
)


def test_ingest_config_rejects_projected_crs() -> None:
    with pytest.raises(ValidationError):
        IngestConfig(source_crs="EPSG:3857")
    # accepted lon/lat aliases
    for crs in ("EPSG:4326", "WGS84", "crs84", "OGC:CRS84"):
        assert IngestConfig(source_crs=crs).source_crs.lower() in (
            "epsg:4326", "wgs84", "crs84", "ogc:crs84"
        )


def test_ingest_config_default_fields() -> None:
    cfg = IngestConfig()
    assert cfg.entity_id_field == "entity_id"
    assert cfg.time_field == "timestamp"
    assert cfg.assumed_timezone == "UTC"
    assert cfg.duplicate_time_policy.value == "keep_all"


def test_segmentation_thresholds_must_be_positive_and_finite() -> None:
    with pytest.raises(ValidationError):
        SegmentationConfig(max_gap_s=0)
    with pytest.raises(ValidationError):
        SegmentationConfig(max_speed_kmh=-1)
    with pytest.raises(ValidationError):
        SegmentationConfig(max_jump_m=float("inf"))


def test_dwell_config_bounds() -> None:
    with pytest.raises(ValidationError):
        DwellConfig(radius_m=0)
    with pytest.raises(ValidationError):
        DwellConfig(min_points=1)
    assert DwellConfig().radius_m == 50.0


def test_time_window_requires_tz_aware() -> None:
    with pytest.raises(ValidationError):
        TimeWindow(start=datetime(2026, 1, 1))  # naive
    w = TimeWindow(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    assert w.start.tzinfo is not None


def test_region_event_config_requires_regions_ref() -> None:
    with pytest.raises(ValidationError):
        RegionEventConfig(regions_ref="")
    cfg = RegionEventConfig(regions_ref="ref:geojson-test")
    # ref_cursor declaration: registry passes the ref string untouched
    extra = RegionEventConfig.model_fields["regions_ref"].json_schema_extra
    assert isinstance(extra, dict) and extra.get("ref_cursor") is True


def test_moving_object_error_is_value_error() -> None:
    assert issubclass(MovingObjectError, ValueError)


from app.services.moving_object.contracts import SegmentationConfig  # noqa: E402
