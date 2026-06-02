"""Tests for spatial_tasks Celery task definitions."""
import pytest


def test_no_spatial_join_task():
    """run_spatial_join should not exist — SpatialAnalyzer.spatial_join was never implemented."""
    from app.services import spatial_tasks
    assert not hasattr(spatial_tasks, "run_spatial_join"), (
        "run_spatial_join references nonexistent SpatialAnalyzer.spatial_join"
    )


def test_no_zonal_stats_task():
    """run_zonal_stats should not exist — SpatialAnalyzer.zonal_statistics was never implemented."""
    from app.services import spatial_tasks
    assert not hasattr(spatial_tasks, "run_zonal_stats"), (
        "run_zonal_stats references nonexistent SpatialAnalyzer.zonal_statistics"
    )


def test_valid_tasks_exist():
    """Core tasks that call real SpatialAnalyzer methods must still exist."""
    from app.services import spatial_tasks
    assert hasattr(spatial_tasks, "run_buffer_analysis")
    assert hasattr(spatial_tasks, "run_spatial_stats")
    assert hasattr(spatial_tasks, "run_heatmap_generation")
    assert hasattr(spatial_tasks, "run_nearest_neighbor")
    assert hasattr(spatial_tasks, "run_overlay_analysis")
    assert hasattr(spatial_tasks, "run_attribute_filter")
    assert hasattr(spatial_tasks, "run_path_analysis")
