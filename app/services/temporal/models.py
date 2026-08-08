"""
Temporal Domain Models for Temporal GIS Runtime.
Provides strongly typed data structures for representing time points, intervals,
extents, field profiling, filtering, windowing, aggregation, and temporal slices.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator


class TemporalFieldType(str, Enum):
    DATETIME = "datetime"
    DATE = "date"
    TIMESTAMP = "timestamp"
    YEAR = "year"
    YEAR_MONTH = "year_month"
    EPOCH_SEC = "epoch_sec"
    EPOCH_MS = "epoch_ms"
    UNKNOWN = "unknown"


class TemporalGranularity(str, Enum):
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    IRREGULAR = "irregular"


class TemporalOperator(str, Enum):
    EQUALS = "equals"
    BEFORE = "before"
    AFTER = "after"
    BETWEEN = "between"
    IN_INTERVAL = "in_interval"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class TemporalUnit(str, Enum):
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class TemporalMetric(str, Enum):
    MEAN = "mean"
    SUM = "sum"
    MIN = "min"
    MAX = "max"
    COUNT = "count"
    FIRST = "first"
    LAST = "last"
    STDDEV = "stddev"


class WindowType(str, Enum):
    SLIDING = "sliding"
    TUMBLING = "tumbling"
    EXPANDING = "expanding"


class TimeInstant(BaseModel):
    """Represents a single discrete point in time."""
    model_config = ConfigDict(frozen=True)

    iso_string: str = Field(..., description="Canonical ISO-8601 string, e.g. 2026-08-08T10:00:00Z")
    epoch_seconds: float = Field(..., description="Unix epoch timestamp in seconds")
    tz_name: str = Field(default="UTC", description="Timezone identifier or offset string")

    @classmethod
    def from_datetime(cls, dt: datetime) -> "TimeInstant":
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        epoch = dt.timestamp()
        iso = dt.isoformat()
        tz_str = str(dt.tzinfo)
        return cls(iso_string=iso, epoch_seconds=epoch, tz_name=tz_str)

    @classmethod
    def from_epoch(cls, seconds: float, tz_name: str = "UTC") -> "TimeInstant":
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
        return cls(iso_string=dt.isoformat(), epoch_seconds=seconds, tz_name=tz_name)

    def to_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.epoch_seconds, tz=timezone.utc)


class TimeInterval(BaseModel):
    """Represents a temporal interval bounded by start and end instants."""
    start: TimeInstant
    end: TimeInstant
    start_inclusive: bool = True
    end_inclusive: bool = True

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end.epoch_seconds - self.start.epoch_seconds)

    @model_validator(mode="after")
    def validate_order(self) -> "TimeInterval":
        if self.end.epoch_seconds < self.start.epoch_seconds:
            raise ValueError("TimeInterval end instant cannot precede start instant.")
        return self


class TemporalExtent(BaseModel):
    """Summarizes the min/max bounds and record counts of a temporal dataset."""
    min_time: Optional[TimeInstant] = None
    max_time: Optional[TimeInstant] = None
    total_records: int = 0
    valid_time_records: int = 0
    missing_time_records: int = 0

    @property
    def span_seconds(self) -> float:
        if self.min_time and self.max_time:
            return max(0.0, self.max_time.epoch_seconds - self.min_time.epoch_seconds)
        return 0.0


class TimeField(BaseModel):
    """Metadata describing a temporal property field found in a dataset."""
    field_name: str
    field_type: TemporalFieldType = TemporalFieldType.UNKNOWN
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    sample_values: List[Any] = Field(default_factory=list)
    detected_format: Optional[str] = None
    has_timezone: bool = False


class TemporalDatasetProfile(BaseModel):
    """Complete temporal profiling report for a vector or raster dataset."""
    primary_time_field: Optional[TimeField] = None
    secondary_time_field: Optional[TimeField] = None
    temporal_extent: Optional[TemporalExtent] = None
    granularity: TemporalGranularity = TemporalGranularity.IRREGULAR
    is_regular: bool = False
    detected_gaps: List[Dict[str, Any]] = Field(default_factory=list)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def temporal_field(self) -> str:
        return self.primary_time_field.field_name if self.primary_time_field else ""

    @property
    def extent(self) -> Optional[TemporalExtent]:
        return self.temporal_extent

    @property
    def confidence(self) -> float:
        return self.overall_confidence


class TemporalFilter(BaseModel):
    """Specification for filtering data along the temporal dimension."""
    field_name: str = Field(default="timestamp")
    operator: TemporalOperator = Field(default=TemporalOperator.BETWEEN)
    instant: Optional[TimeInstant] = None
    interval: Optional[TimeInterval] = None
    values: List[TimeInstant] = Field(default_factory=list)
    filter_type: Optional[str] = None
    start_time: Optional[Any] = None
    end_time: Optional[Any] = None
    relative_window: Optional[str] = None


class TemporalWindow(BaseModel):
    """Specification for windowing or sliding over temporal datasets."""
    window_size: float = Field(..., gt=0, description="Window duration length")
    step_size: float = Field(..., gt=0, description="Step size for sliding window")
    unit: TemporalUnit = TemporalUnit.DAY
    type: WindowType = WindowType.SLIDING


class TemporalAggregation(BaseModel):
    """Specification for Temporal Rollup or Resampling."""
    group_by_unit: Union[TemporalUnit, str] = TemporalUnit.DAY
    metrics: List[Union[TemporalMetric, str]] = Field(default_factory=lambda: [TemporalMetric.MEAN])
    time_field: Optional[str] = None
    target_fields: List[str] = Field(default_factory=list)
    interval_unit: Optional[str] = None
    aggregation_func: Optional[str] = None
    metric_fields: Optional[List[str]] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, values: Any) -> Any:
        if isinstance(values, dict):
            if "interval_unit" in values and "group_by_unit" not in values:
                values["group_by_unit"] = values["interval_unit"]
            if "aggregation_func" in values and "metrics" not in values:
                values["metrics"] = [values["aggregation_func"]]
            if "metric_fields" in values and "target_fields" not in values:
                values["target_fields"] = values["metric_fields"]
        return values


class TemporalSlice(BaseModel):
    """Represents a bounded slice of data along the time axis."""
    slice_id: str
    interval: TimeInterval
    feature_count: int = 0
    layer_reference: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TemporalChangeResult(BaseModel):
    """Result of multi-snapshot temporal change analysis."""
    snapshot_count: int = 0
    time_points: List[str] = Field(default_factory=list)
    count_deltas: List[Dict[str, Any]] = Field(default_factory=list)
    attribute_deltas: List[Dict[str, Any]] = Field(default_factory=list)
    geometry_deltas: Optional[Dict[str, Any]] = None
    feature_changes: Optional[List[Dict[str, Any]]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TemporalTrendResult(BaseModel):
    """Result of temporal trend analysis on a metric time series."""
    metric_name: str
    total_points: int = 0
    moving_average: List[Optional[float]] = Field(default_factory=list)
    slope: float = 0.0
    intercept: float = 0.0
    r_squared: float = 0.0
    direction: str = "stable"  # "increasing", "decreasing", "stable"
    anomalies: List[Dict[str, Any]] = Field(default_factory=list)
    values: List[float] = Field(default_factory=list)
    timestamps: List[str] = Field(default_factory=list)


class SpatiotemporalHotspotResult(BaseModel):
    """Result of space-time clustering (ST-DBSCAN)."""
    success: bool = True
    total_clusters: int = 0
    clustered_points: int = 0
    noise_points: int = 0
    temporal_span_hours: float = 0.0
    cluster_stats: Dict[str, Any] = Field(default_factory=dict)
    clusters: List[Dict[str, Any]] = Field(default_factory=list)
    features: List[Dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    error_message: Optional[str] = None


class TemporalRasterResult(BaseModel):
    """Result of windowed raster time series operations."""
    selected_slices: List[Dict[str, Any]] = Field(default_factory=list)
    raster_statistics: Optional[Dict[str, Any]] = None
    raster_difference: Optional[Dict[str, Any]] = None
    raster_trend: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TemporalAnalysisResult(BaseModel):
    """Unified master output model for Temporal GIS operations."""
    analysis_type: str
    status: str = "success"
    profile: Optional[TemporalDatasetProfile] = None
    slices: List[TemporalSlice] = Field(default_factory=list)
    change_summary: Dict[str, Any] = Field(default_factory=dict)
    trend_metrics: Dict[str, Any] = Field(default_factory=dict)
    hotspots: List[SpatiotemporalHotspotResult] = Field(default_factory=list)
    result_geojson: Dict[str, Any] = Field(default_factory=dict)
    raster_series: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

