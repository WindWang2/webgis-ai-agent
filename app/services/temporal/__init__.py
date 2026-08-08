"""Temporal GIS Domain & Runtime Engine Module."""

from app.services.temporal.models import (
    TimeInstant,
    TimeInterval,
    TemporalExtent,
    TimeField,
    TemporalDatasetProfile,
    TemporalFilter,
    TemporalWindow,
    TemporalAggregation,
    TemporalSlice,
    TemporalChangeResult,
    TemporalTrendResult,
    SpatiotemporalHotspotResult,
    TemporalRasterResult,
    TemporalFieldType,
    TemporalGranularity,
    TemporalOperator,
    TemporalUnit,
    TemporalMetric,
    WindowType,
)
from app.services.temporal.profiler import profile_temporal_dataset, TemporalProfiler
from app.services.temporal.filter import TemporalFilterEngine
from app.services.temporal.aggregation import TemporalAggregationEngine
from app.services.temporal.change import TemporalChangeEngine
from app.services.temporal.trend import TemporalTrendEngine
from app.services.temporal.spatiotemporal import SpatiotemporalClusterEngine
from app.services.temporal.raster import TemporalRasterEngine
from app.services.temporal.engine import TemporalEngine

__all__ = [
    # Models & Enums
    "TimeInstant",
    "TimeInterval",
    "TemporalExtent",
    "TimeField",
    "TemporalDatasetProfile",
    "TemporalFilter",
    "TemporalWindow",
    "TemporalAggregation",
    "TemporalSlice",
    "TemporalChangeResult",
    "TemporalTrendResult",
    "SpatiotemporalHotspotResult",
    "TemporalRasterResult",
    "TemporalFieldType",
    "TemporalGranularity",
    "TemporalOperator",
    "TemporalUnit",
    "TemporalMetric",
    "WindowType",
    # Engines & Profiler
    "profile_temporal_dataset",
    "TemporalProfiler",
    "TemporalFilterEngine",
    "TemporalAggregationEngine",
    "TemporalChangeEngine",
    "TemporalTrendEngine",
    "SpatiotemporalClusterEngine",
    "TemporalRasterEngine",
    "TemporalEngine",
]
