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
    """The 3 live Celery tasks (with real .delay/.apply_async callers) must still exist.

    The 6 dead wrappers (run_buffer_analysis / run_spatial_stats / run_nearest_neighbor /
    run_overlay_analysis / run_attribute_filter / run_path_analysis) were deleted (D1):
    agent tools call SpatialAnalyzer directly (ADR-0013 deleted the dispatch seam that
    routed to them). These watchdogs lock in that they stay gone.
    """
    from app.services import spatial_tasks
    assert hasattr(spatial_tasks, "run_heatmap_generation")
    assert hasattr(spatial_tasks, "run_ndvi_analysis")
    assert hasattr(spatial_tasks, "run_change_detection")
    # Dead wrappers must stay deleted
    for dead in (
        "run_buffer_analysis", "run_spatial_stats", "run_nearest_neighbor",
        "run_overlay_analysis", "run_attribute_filter", "run_path_analysis",
    ):
        assert not hasattr(spatial_tasks, dead), f"{dead} should have been deleted (D1)"
