"""Point-table ingestion for moving-object trajectories (WP1).

Turns a GeoJSON FeatureCollection of Point features into per-entity
normalized observation streams plus anomaly diagnostics. Nothing is dropped
silently: every rejection class is counted in ``IngestDiagnostics`` and
carries its share of the input back-reference discipline (stable
``source_row_id`` per row).

Time semantics:
- timezone-aware inputs are converted to UTC as declared;
- naive inputs are interpreted in ``assumed_timezone`` (IANA via zoneinfo,
  DST-correct with fold=0); every such row is flagged and the count is
  disclosed downstream — this covers cross-timezone and DST datasets;
- epoch seconds/milliseconds are UTC by definition.
- dates (no time component) are midnight in ``assumed_timezone``.

Ordering: canonical ``time_asc`` sorts by (time, input order) — stable, and
never uses the array index as identity. ``keep_input`` trusts input order
and counts out-of-order rows for disclosure.

Duplicate times: policy per ``DuplicateTimePolicy``; ``keep_all`` keeps both
and the resulting zero-dt interval is marked degenerate downstream (no fake
zero speed, no inf).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.services.moving_object.contracts import (
    MAX_INPUT_ROWS,
    MAX_PREVIEW_FEATURES,
    IngestConfig,
    IngestDiagnostics,
    MovingObjectError,
    ObsPoint,
)

logger = logging.getLogger(__name__)

_EPOCH_MIN = 3.0e8      # ~1979 (seconds) — same acceptance window as profiler
_EPOCH_MAX = 3.0e10     # ~2920 (seconds)
_EPOCH_MS_MIN = 3.0e11
_EPOCH_MS_MAX = 3.0e13


def _coerce_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def parse_event_time(
    val: Any, tz: ZoneInfo, field_name: str = "timestamp"
) -> Tuple[Optional[datetime], bool]:
    """Parse one raw time value → (aware UTC datetime, naive_was_assumed).

    Returns (None, False) when unparseable. Raises nothing; callers count
    failures per row.
    """
    if val is None:
        return None, False
    if isinstance(val, datetime):
        if val.tzinfo is not None:
            return _coerce_utc(val), False
        return _coerce_utc(val.replace(tzinfo=tz)), True
    if isinstance(val, date):
        # Date-only: midnight in the declared assumed timezone.
        return _coerce_utc(
            datetime(val.year, val.month, val.day, tzinfo=tz)
        ), True
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None, False
        # Fast path: ISO-8601 (Z / offset / naive).
        txt = s.replace("Z", "+00:00").replace("z", "+00:00")
        try:
            dt = datetime.fromisoformat(txt)
        except ValueError:
            dt = None
        if dt is not None:
            if dt.tzinfo is not None:
                return _coerce_utc(dt), False
            return _coerce_utc(dt.replace(tzinfo=tz)), True
        # Space-separated "YYYY-MM-DD HH:MM[:SS]" (common GPS log format).
        try:
            dt = datetime.fromisoformat(s.replace(" ", "T", 1))
        except ValueError:
            return None, False
        if dt.tzinfo is not None:
            return _coerce_utc(dt), False
        return _coerce_utc(dt.replace(tzinfo=tz)), True
    if isinstance(val, bool):
        return None, False
    if isinstance(val, (int, float)):
        f = float(val)
        if _EPOCH_MIN <= f <= _EPOCH_MAX:
            return datetime.fromtimestamp(f, tz=timezone.utc), False
        if _EPOCH_MS_MIN <= f <= _EPOCH_MS_MAX:
            return datetime.fromtimestamp(f / 1000.0, tz=timezone.utc), False
        return None, False
    return None, False


def _extract_point(geometry: Any) -> Optional[Tuple[float, float]]:
    """Return (lon, lat) for a valid non-degenerate Point geometry."""
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        return None
    coords = geometry.get("coordinates")
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None
    lon, lat = coords[0], coords[1]
    if isinstance(lon, bool) or isinstance(lat, bool):
        return None
    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        return None
    lon_f, lat_f = float(lon), float(lat)
    # Degenerate (NaN/inf) and out-of-domain positions are rejected per row
    # and counted — they never silently become a valid fix.
    if lon_f != lon_f or lat_f != lat_f:
        return None
    if lon_f in (float("inf"), float("-inf")) or lat_f in (
        float("inf"), float("-inf")
    ):
        return None
    if not (-180.0 <= lon_f <= 180.0) or not (-90.0 <= lat_f <= 90.0):
        return None
    return lon_f, lat_f


def _row_identity(props: Dict[str, Any], cfg: IngestConfig, order: int) -> str:
    """Stable per-row identity: declared field when present, else derived
    from the immutable input position (disclosed via diagnostics note)."""
    if cfg.source_row_field:
        v = props.get(cfg.source_row_field)
        if v is None or (isinstance(v, str) and not v.strip()):
            raise MovingObjectError(
                f"source_row_field {cfg.source_row_field!r} is empty on row "
                f"{order}"
            )
        return str(v)
    return f"row:{order}"


def ingest_point_collection(
    features: List[Dict[str, Any]],
    cfg: IngestConfig,
) -> Tuple[List[ObsPoint], IngestDiagnostics]:
    """Normalize a Point FeatureCollection into per-entity sorted streams.

    Raises ``MovingObjectError`` for contract-level failures (over-cap
    input, non-list payload). Row-level anomalies (missing ID, bad time,
    degenerate geometry) are counted and skipped — never silent.
    """
    if not isinstance(features, list):
        raise MovingObjectError(
            "moving-object ingest expects a list of Point features"
        )
    if len(features) > MAX_INPUT_ROWS:
        raise MovingObjectError(
            f"input rows {len(features)} exceed hard cap {MAX_INPUT_ROWS}"
        )

    try:
        tz = ZoneInfo(cfg.assumed_timezone)
    except Exception as e:
        raise MovingObjectError(
            f"invalid assumed_timezone {cfg.assumed_timezone!r}: {e}"
        ) from e

    diag = IngestDiagnostics(input_rows=len(features))
    notes: List[str] = []
    out: List[ObsPoint] = []
    entity_ids: set = set()
    times: List[datetime] = []

    for order, feat in enumerate(features):
        if not isinstance(feat, dict):
            diag.missing_or_degenerate_geometry += 1
            continue
        props = feat.get("properties")
        if not isinstance(props, dict):
            props = {}

        raw_id = props.get(cfg.entity_id_field)
        if raw_id is None or (isinstance(raw_id, str) and not raw_id.strip()):
            diag.missing_entity_id += 1
            continue
        entity_id = str(raw_id).strip()

        t, naive = parse_event_time(props.get(cfg.time_field), tz, cfg.time_field)
        if t is None:
            diag.unparseable_time += 1
            continue
        if naive:
            diag.naive_time_rows += 1

        pt = _extract_point(feat.get("geometry"))
        if pt is None:
            diag.missing_or_degenerate_geometry += 1
            continue

        try:
            source_row_id = _row_identity(props, cfg, order)
        except MovingObjectError:
            diag.notes.append(
                f"row {order}: missing source_row_field value — row skipped"
            )
            diag.missing_entity_id += 1  # identity-critical, same class
            continue

        accuracy = props.get("accuracy_m")
        accuracy_f: Optional[float] = None
        if isinstance(accuracy, (int, float)) and not isinstance(accuracy, bool):
            f = float(accuracy)
            if f >= 0 and f == f and f not in (float("inf"),):
                accuracy_f = f

        out.append(ObsPoint(
            entity_id=entity_id,
            t=t,
            lon=pt[0],
            lat=pt[1],
            source_row_id=source_row_id,
            order=order,
            accuracy_m=accuracy_f,
            naive_time_assumed=naive,
        ))
        entity_ids.add(entity_id)
        times.append(t)

    diag.accepted = len(out)
    diag.entity_count = len(entity_ids)

    if out:
        # Count input-order inversions BEFORE sorting (the count describes
        # the user's data, not our output), then order canonically —
        # (time, input position): stable, and never index-as-identity.
        diag.out_of_order_rows = _count_out_of_order(out)
        out.sort(key=lambda p: (p.t, p.order))

        # Duplicate-time policy (adjacent equal times within one entity).
        diag.duplicate_time_pairs = _apply_duplicate_policy(out, cfg)

        diag.time_min = min(times).isoformat()
        diag.time_max = max(times).isoformat()
    else:
        diag.empty_after_ingest = True

    if diag.naive_time_rows:
        notes.append(
            f"{diag.naive_time_rows} naive timestamps interpreted in "
            f"declared timezone {cfg.assumed_timezone} (DST fold=0)"
        )
    if not cfg.source_row_field:
        notes.append(
            "source_row_field not declared: derived stable identity "
            "'row:<input position>' used (input version immutable)"
        )
    diag.notes = notes
    return out, diag


def _count_out_of_order(points: List[ObsPoint]) -> int:
    """Adjacent inversions within one entity under canonical ordering."""
    prev: Dict[str, datetime] = {}
    count = 0
    for p in points:
        last = prev.get(p.entity_id)
        if last is not None and p.t < last:
            count += 1
        prev[p.entity_id] = p.t
    return count


def _apply_duplicate_policy(points: List[ObsPoint], cfg: IngestConfig) -> int:
    """Apply the duplicate-time policy in place; returns affected pair count."""
    policy = cfg.duplicate_time_policy
    if policy.value == "keep_all":
        return _count_duplicate_pairs(points)
    keep: List[ObsPoint] = []
    duplicates = 0
    i = 0
    n = len(points)
    while i < n:
        j = i + 1
        while j < n and points[j].entity_id == points[i].entity_id \
                and points[j].t == points[i].t:
            j += 1
        group = points[i:j]
        if len(group) > 1:
            duplicates += len(group) - 1
            if policy.value == "keep_first":
                keep.append(group[0])
            else:  # keep_last
                keep.append(group[-1])
        else:
            keep.append(group[0])
        i = j
    points[:] = keep
    return duplicates


def _count_duplicate_pairs(points: List[ObsPoint]) -> int:
    duplicates = 0
    for a, b in zip(points, points[1:]):
        if a.entity_id == b.entity_id and a.t == b.t:
            duplicates += 1
    return duplicates


def group_by_entity(
    points: List[ObsPoint],
) -> List[Tuple[str, List[ObsPoint]]]:
    """Group sorted points into per-entity streams (insertion-ordered by
    first appearance). Points must already be globally sorted so each
    entity's stream is time-ascending."""
    streams: Dict[str, List[ObsPoint]] = {}
    for p in points:
        streams.setdefault(p.entity_id, []).append(p)
    return list(streams.items())


def preview_features(points: List[ObsPoint], max_n: int = MAX_PREVIEW_FEATURES) -> List[Dict[str, Any]]:
    """Bounded GeoJSON preview of normalized observations (full data lives
    behind refs; the LLM sees at most ``max_n`` sampled rows)."""
    feats: List[Dict[str, Any]] = []
    step = max(1, len(points) // max_n) if points else 1
    for p in points[::step][:max_n]:
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p.lon, p.lat]},
            "properties": {
                "entity_id": p.entity_id,
                "time": p.t.isoformat(),
                "source_row": p.source_row_id,
                **({"accuracy_m": p.accuracy_m} if p.accuracy_m is not None else {}),
            },
        })
    return feats
