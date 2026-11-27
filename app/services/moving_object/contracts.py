"""Moving-object domain contracts (pure domain, no I/O, no stores).

Design rules (GOAL-20261127-02 WP1):
- Raw observations are immutable: every derived artifact carries
  ``source_ref`` + ``source_row_id`` back-references; cleaning never rewrites
  the input, it produces a *new* version with a preview.
- Trajectory identity is ``entity_id`` (stable string), never an array index.
- Time is canonical UTC internally; input timezones are honored via zoneinfo;
  naive timestamps get an explicitly declared ``assumed_timezone`` that is
  disclosed in every result.
- Only WGS84 geographic coordinates are accepted as lon/lat; projected
  meters are rejected (same discipline as flow_product contracts).
- Distances/bearings are geodesic (WGS84 ellipsoid, pyproj.Geod) — degrees
  are never treated as meters.
- Derived interval quantities (speed, bearing) are *interval estimates
  between observations*, always labeled with their bounding observations.
- Uncertainty: when an event can only be located to an observation interval
  (e.g. "crossed the region boundary between t1 and t2"), the result says
  exactly that and never fabricates an exact instant.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

#: Hard caps — bounded ingest / bounded derived output.
MAX_INPUT_ROWS = 500_000
MAX_ENTITIES = 20_000
MAX_TRAJECTORY_SEGMENTS = 100_000
MAX_EVENTS = 100_000
#: Points emitted inline in previews (full data lives behind refs).
MAX_PREVIEW_FEATURES = 200

SUPPORTED_CRS = {"epsg:4326", "wgs84", "crs84", "ogc:crs84"}

SCHEMA_VERSION = "moving_object.v1"
FAMILY_ID = "moving_object"

#: Event vocabulary (WP3). ``unknown`` covers intervals where behavior is
#: not determinable from the samples (e.g. region crossed entirely between
#: two consecutive observations of a too-coarse sampling).
EVENT_TYPES = ("enter", "exit", "cross", "dwell", "unknown")


class MovingObjectError(ValueError):
    """Bounded, explicit domain rejection: bad CRS, missing ID/time fields,
    unparseable timestamps, over-cap input, degenerate geometry."""


class DuplicateTimePolicy(str, Enum):
    """Resolution for two observations of one entity at the identical time."""

    KEEP_ALL = "keep_all"          # keep both; gap=0 segments stay broken
    KEEP_FIRST = "keep_first"      # stable: lower source order wins
    KEEP_LAST = "keep_last"        # stable: higher source order wins


class IngestConfig(BaseModel):
    """Point-table ingestion contract (WP1).

    The input is a GeoJSON FeatureCollection of Point features (or a ref to
    one). Identity/time fields are read from feature properties. Observations
    are ALWAYS canonically ordered by (time, input position) — input-order
    inversions are counted and disclosed, never trusted downstream.
    """

    entity_id_field: str = Field("entity_id", min_length=1, max_length=64)
    time_field: str = Field("timestamp", min_length=1, max_length=64)
    #: Explicit property carrying a stable source row identity. When absent,
    #: the ingest derives one from (input feature order) — disclosed, and
    #: stable for the same immutable input version.
    source_row_field: Optional[str] = Field(None, max_length=64)
    #: IANA zone applied to naive timestamps only ("UTC" keeps UTC semantics).
    assumed_timezone: str = Field("UTC", max_length=64)
    duplicate_time_policy: DuplicateTimePolicy = DuplicateTimePolicy.KEEP_ALL
    source_crs: str = Field("EPSG:4326", max_length=64)

    @field_validator("source_crs")
    @classmethod
    def _check_crs(cls, v: str) -> str:
        if v.lower() not in SUPPORTED_CRS:
            raise MovingObjectError(
                f"unsupported source_crs {v!r}: moving-object ingest accepts "
                f"WGS84 lon/lat only ({sorted(SUPPORTED_CRS)})"
            )
        return v


class SegmentationConfig(BaseModel):
    """Trajectory construction contract (WP2).

    A segment break is emitted when ANY threshold fires between two
    consecutive observations of one entity; every break is traceable
    (reason + the two bounding source_row_ids).
    """

    #: Time gap above which a break is emitted (seconds).
    max_gap_s: float = Field(3600.0, gt=0, le=86400 * 366)
    #: Speed above which the move is treated as a positional jump, not
    #: motion (km/h between the two observations).
    max_speed_kmh: float = Field(300.0, gt=0, le=1_000_000)
    #: Distance above which a break is emitted regardless of speed (meters).
    max_jump_m: float = Field(50_000.0, gt=0, le=20_000_000)
    #: Minimum observations per emitted segment (shorter ones are disclosed
    #: as dropped_degenerate, never silently kept).
    min_points_per_segment: int = Field(2, ge=2, le=1000)

    @field_validator("max_speed_kmh", "max_jump_m", "max_gap_s")
    @classmethod
    def _finite(cls, v: float) -> float:
        if v != v or v in (float("inf"), float("-inf")):
            raise MovingObjectError("thresholds must be finite")
        return v


class DwellConfig(BaseModel):
    """Dwell (stop) detection contract (WP2).

    A dwell is a maximal run of consecutive observations within ``radius_m``
    of the run's first fix lasting >= ``min_dwell_s`` with at least
    ``min_points`` fixes. Radius is geodesic meters; this is an explicit,
    verifiable threshold method (not clustering).

    ``max_gap_s`` bounds unverifiable presence: a run is SPLIT when two
    consecutive fixes are more than ``max_gap_s`` apart, so a reported
    dwell duration never silently spans an unobserved gap (arrival and
    departure inside a gap remain interval semantics, not measured
    presence).
    """

    radius_m: float = Field(50.0, gt=0, le=10_000)
    min_dwell_s: float = Field(120.0, gt=0, le=86400 * 30)
    min_points: int = Field(3, ge=2, le=10_000)
    max_gap_s: float = Field(3600.0, gt=0, le=86400 * 366)


class RegionEventConfig(BaseModel):
    """Region interaction contract (WP3).

    Boundary semantics are explicit: a fix exactly on the boundary counts as
    inside (``closed``) — stated in results. When two consecutive fixes
    straddle a region (one in, one out), enter/exit instants are *inferred
    to lie in the open interval* between them (linear-assumption disclosed);
    a region fully traversed between two consecutive outside fixes yields a
    ``cross`` with only the bracketing interval, never an exact time.
    """

    regions_ref: str = Field(..., min_length=1, max_length=160,
                             json_schema_extra={"ref_cursor": True})
    region_id_field: str = Field("region_id", min_length=1, max_length=64)
    boundary_rule: Literal["closed", "open"] = "closed"
    #: Also detect regions fully traversed between two outside fixes (cross).
    detect_cross: bool = True
    #: Merge consecutive inside fixes of one entity+region into dwell spans
    #: >= min_dwell_s (event-level dwell, distinct from fix-level dwell).
    detect_dwell: bool = True
    min_dwell_s: float = Field(60.0, gt=0, le=86400 * 30)
    #: A region dwell span is split when consecutive inside fixes are more
    #: than max_gap_s apart (same unverifiable-presence bound as WP2).
    dwell_max_gap_s: float = Field(3600.0, gt=0, le=86400 * 366)


class TimeWindow(BaseModel):
    """Optional analysis window; both bounds inclusive, canonical UTC."""

    start: Optional[datetime] = None
    end: Optional[datetime] = None

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is not None and v.tzinfo is None:
            raise MovingObjectError(
                "time window bounds must be timezone-aware ISO-8601"
            )
        return v


# --- Normalized observation (computational record, not pydantic) ------------


class ObsPoint:
    """One normalized observation. Lightweight on purpose: ingestion of
    100k+ points must not instantiate pydantic models per row.

    ``source_row_id`` is the immutable back-reference into the input version.
    ``order`` is the ingest sequence (0-based, per input FeatureCollection)
    used as the final stable tie-break; it is never used as identity.
    ``naive_time_assumed`` flags rows whose wall time needed the declared
    assumed timezone (disclosed downstream)."""

    __slots__ = (
        "entity_id", "t", "lon", "lat", "source_row_id", "order",
        "accuracy_m", "naive_time_assumed",
    )

    def __init__(
        self,
        entity_id: str,
        t: datetime,
        lon: float,
        lat: float,
        source_row_id: str,
        order: int,
        accuracy_m: Optional[float] = None,
        naive_time_assumed: bool = False,
    ) -> None:
        self.entity_id = entity_id
        self.t = t
        self.lon = lon
        self.lat = lat
        self.source_row_id = source_row_id
        self.order = order
        self.accuracy_m = accuracy_m
        self.naive_time_assumed = naive_time_assumed

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"ObsPoint(entity={self.entity_id!r}, t={self.t.isoformat()}, "
            f"row={self.source_row_id!r})"
        )


class SegmentBreak:
    """One explicit, traceable segmentation break."""

    __slots__ = ("entity_id", "reason", "from_row", "to_row", "t_from", "t_to")

    def __init__(
        self,
        entity_id: str,
        reason: str,
        from_row: str,
        to_row: str,
        t_from: datetime,
        t_to: datetime,
    ) -> None:
        self.entity_id = entity_id
        self.reason = reason  # time_gap | speed_jump | distance_jump | zero_dt_conflict
        self.from_row = from_row
        self.to_row = to_row
        self.t_from = t_from
        self.t_to = t_to

    def as_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "reason": self.reason,
            "from_source_row": self.from_row,
            "to_source_row": self.to_row,
            "t_from": self.t_from.isoformat(),
            "t_to": self.t_to.isoformat(),
        }


# --- Result envelopes (bounded) ---------------------------------------------


class IngestDiagnostics(BaseModel):
    """Counts of every anomaly class; nothing is dropped silently."""

    input_rows: int = 0
    accepted: int = 0
    missing_entity_id: int = 0
    missing_time: int = 0
    unparseable_time: int = 0
    missing_or_degenerate_geometry: int = 0
    duplicate_time_pairs: int = 0
    out_of_order_rows: int = 0
    naive_time_rows: int = 0
    entity_count: int = 0
    time_min: Optional[str] = None
    time_max: Optional[str] = None
    empty_after_ingest: bool = False
    notes: List[str] = Field(default_factory=list)


class MotionInterval(BaseModel):
    """Motion quantities for ONE observation interval (between two fixes).

    Interval estimates: ``speed_kmh``/``bearing_deg`` describe displacement
    between the two named fixes, not an instantaneous state. ``dt_s == 0``
    (duplicate-time pair kept by policy) yields ``speed_kmh=None`` — never a
    fake zero or infinity — with ``degenerate=True``.
    """

    entity_id: str
    from_row: str
    to_row: str
    t_from: str
    t_to: str
    dt_s: float
    distance_m: float
    speed_kmh: Optional[float] = None
    bearing_deg: Optional[float] = None
    cumulative_distance_m: float = 0.0
    degenerate: bool = False


class TrajectorySegmentModel(BaseModel):
    """One trajectory segment of one entity."""

    entity_id: str
    segment_index: int
    n_points: int
    t_start: str
    t_end: str
    duration_s: float
    path_length_m: float
    mean_speed_kmh: Optional[float] = None  # None when duration_s == 0
    max_speed_kmh: Optional[float] = None
    displacement_m: float  # first→last fix geodesic distance
    start_lon: float
    start_lat: float
    end_lon: float
    end_lat: float


class EntityTrajectory(BaseModel):
    entity_id: str
    n_observations: int
    n_segments: int
    segments: List[TrajectorySegmentModel]
    total_path_length_m: float
    observation_span_s: float  # first→last fix age, independent of gaps


class DwellEventModel(BaseModel):
    entity_id: str
    dwell_index: int
    t_start: str
    t_end: str
    duration_s: float
    n_points: int
    center_lon: float
    center_lat: float
    radius_m: float
    #: Max geodesic distance of any fix from the dwell anchor (first fix):
    #: the actual extent under the detection radius, O(n), never fabricated.
    extent_from_anchor_m: float
    from_row: str
    to_row: str
    note: str = ""


class RegionEventModel(BaseModel):
    entity_id: str
    region_id: str
    event_type: str  # enter|exit|cross|dwell|unknown (EVENT_TYPES)
    #: Exact when determined by an inside fix; None when only an interval
    #: can be stated (sampling between fixes).
    t_exact: Optional[str] = None
    t_interval_start: Optional[str] = None
    t_interval_end: Optional[str] = None
    #: True when the event instant is bounded by an observation interval
    #: under the linear-sampling assumption (disclosed, not exact).
    inferred: bool = False
    lon: Optional[float] = None
    lat: Optional[float] = None
    from_row: Optional[str] = None
    to_row: Optional[str] = None
    sequence: int = 0  # per (entity, region) occurrence number, 1-based


class RegionSummaryRow(BaseModel):
    region_id: str
    n_entities: int  # distinct entities with >=1 observed inside fix
    n_enter: int
    n_exit: int
    n_cross: int
    n_dwell_spans: int
    total_dwell_s: float
    total_inside_fixes: int
    #: Exposure honesty: entities with zero inside fixes are counted here,
    #: not silently dropped from the denominator.
    entities_zero_hit: int
    unknown_intervals: int


class EventAnalysisSummary(BaseModel):
    n_entities: int
    n_regions: int
    n_events: int
    by_type: dict = Field(default_factory=dict)
    unknown_intervals: int
    inferred_events: int
    notes: List[str] = Field(default_factory=list)
