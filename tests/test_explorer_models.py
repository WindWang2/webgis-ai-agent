"""Explorer core data models tests"""
import pytest
from pydantic import ValidationError
from datetime import datetime

from app.services.explorer.models import (
    DataPackage,
    DataSourceQualityScore,
    ExplorerPerceptionEvent,
    SearchContext,
    FieldInfo,
    RawContent,
    StructuredData,
)


def test_data_package_minimal():
    """Create a DataPackage with all required fields, assert values"""
    quality = DataSourceQualityScore(
        temporal_score=0.8,
        thematic_score=0.7,
        spatial_score=0.9,
        field_score=0.6,
        precision_score=0.85,
        overall=0.81,
    )
    pkg = DataPackage(
        source_layer="L3_api",
        source_name="test_api",
        quality=quality,
    )
    assert pkg.source_layer == "L3_api"
    assert pkg.source_name == "test_api"
    assert pkg.quality.overall == 0.81
    assert pkg.source_url == ""
    assert pkg.geojson is None
    assert pkg.features_count == 0
    assert pkg.is_fusion_result is False
    assert pkg.has_conflicts is False
    assert pkg.fusion_sources == []
    assert pkg.conflict_fields == []
    assert pkg.available_fields == []


def test_perception_event_validation():
    """Create an ExplorerPerceptionEvent, assert defaults"""
    event = ExplorerPerceptionEvent(
        stage="discover",
        task_id="task-001",
        status="started",
    )
    assert event.stage == "discover"
    assert event.task_id == "task-001"
    assert event.status == "started"
    assert event.context == {}
    assert event.available_actions == []
    assert event.recommended_action == ""
    assert event.requires_intervention is False
    assert event.confidence == 1.0


def test_quality_score_bounds():
    """Verify that values outside 0.0-1.0 raise ValidationError"""
    with pytest.raises(ValidationError):
        DataSourceQualityScore(
            temporal_score=1.5,
            thematic_score=0.5,
            spatial_score=0.5,
            field_score=0.5,
            precision_score=0.5,
            overall=0.5,
        )
    with pytest.raises(ValidationError):
        DataSourceQualityScore(
            temporal_score=-0.1,
            thematic_score=0.5,
            spatial_score=0.5,
            field_score=0.5,
            precision_score=0.5,
            overall=0.5,
        )
