"""Region enter/exit/cross/dwell events over trajectories (WP3).

Decision table per consecutive fix pair (a, b) of one entity against one
region R — nothing else fires an event:

- a inside, b inside  → inside run continues (no event)
- a outside, b inside → ``enter`` bracketed by (t_a, t_b]  (inferred: the
  crossing instant lies in the sampling gap; the first *observed* inside
  fix is t_b)
- a inside, b outside → ``exit``  bracketed by [t_a, t_b)  (symmetric)
- a outside, b outside → segment ab may still pass through R. When
  ``detect_cross`` and the segment genuinely reaches the region under the
  active boundary rule → one ``cross`` bracketed by [t_a, t_b] (entry and
  exit both unobserved). No exact instants are ever fabricated.
- first fix already inside → ``enter`` with t_interval_end = first fix
  (state began before the observation window; disclosed, from_row=None).

"Inside" is the active boundary rule evaluated per fix: ``closed`` counts
boundary as inside (shapely ``covers``), ``open`` does not (``contains``).
Holes and MultiPolygons are native shapely semantics. A fix exactly on the
boundary is therefore inside for ``closed`` and outside for ``open`` — the
rule is stated in every result's notes.

Region-level ``dwell`` events merge consecutive inside fixes of one
(entity, region) pair into spans of observed duration >= min_dwell_s —
span bounds are the exact observation times of the first/last inside fix.

Geometry discipline:
- invalid region geometry is made valid once at load (``make_valid``);
  anything still invalid is a typed rejection, never guessed through;
- a region whose longitude extent exceeds 180° is rejected as a
  dateline-crossing polygon (needs pre-splitting; silently wrong answers
  are worse than a typed refusal);
- antimeridian segments are unwrapped (±360 shift) and regions are tested
  in three lon frames (0, ±360) so near-dateline tracks meet near-dateline
  regions correctly;
- predicates are topological in the lon/lat (plate carrée) convention —
  motion quantities elsewhere use geodesics; the convention split is
  disclosed in results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone
from typing import Dict, List, Optional, Tuple

import shapely
from shapely import (
    LineString,
    Point,
    contains as shp_contains,
    covers as shp_covers,
    intersects as shp_intersects,
    make_valid,
    prepare,
)
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from app.services.moving_object.contracts import (
    MAX_ENTITIES,
    MAX_EVENTS,
    MovingObjectError,
    ObsPoint,
    RegionEventConfig,
    RegionEventModel,
    RegionSummaryRow,
    TimeWindow,
)

_LON_FRAMES = (-360.0, 0.0, 360.0)


class RegionGeometryError(MovingObjectError):
    """Typed rejection for unusable region geometry (never guessed)."""


@dataclass
class RegionSet:
    """Validated, prepared regions in three longitude frames."""

    ids: List[str] = field(default_factory=list)
    geoms: List[BaseGeometry] = field(default_factory=list)      # prepared
    interiors: List[BaseGeometry] = field(default_factory=list)  # prepared (open rule)
    shifted: List[List[BaseGeometry]] = field(default_factory=list)
    boundary_rule: str = "closed"
    notes: List[str] = field(default_factory=list)

    def inside(self, region_idx: int, lon: float, lat: float) -> bool:
        """Active-rule inside test for one fix across lon frames."""
        pt = Point(lon, lat)
        rule = self.boundary_rule
        for frame_idx, off in enumerate(_LON_FRAMES):
            geom = self.shifted[region_idx][frame_idx]
            if geom.is_empty:
                continue
            if rule == "closed":
                if shp_covers(geom, pt):
                    return True
            else:
                if shp_contains(geom, pt):
                    return True
        return False

    def segment_reaches(
        self, region_idx: int, lon_a: float, lat_a: float, lon_b: float, lat_b: float
    ) -> bool:
        """Antimeridian-safe segment→region reach test under the active rule."""
        # Unwrap the segment into a continuous lon frame.
        d = lon_b - lon_a
        if d > 180.0:
            lon_b -= 360.0
        elif d < -180.0:
            lon_b += 360.0
        line = LineString([(lon_a, lat_a), (lon_b, lat_b)])
        rule = self.boundary_rule
        for off in _LON_FRAMES:
            target = self.geoms[region_idx] if rule == "closed" else (
                self.interiors[region_idx]
            )
            if target.is_empty:
                continue
            test = line if off == 0.0 else shapely.affinity.translate(
                line, xoff=off
            )
            if shp_intersects(target, test):
                return True
        return False


def load_regions(
    collection: Dict,
    cfg: RegionEventConfig,
) -> RegionSet:
    """Validate and prepare a region FeatureCollection (typed rejections)."""
    if not isinstance(collection, dict) or collection.get("type") != "FeatureCollection":
        raise RegionGeometryError(
            "regions must be a GeoJSON FeatureCollection"
        )
    features = collection.get("features")
    if not isinstance(features, list) or not features:
        raise RegionGeometryError("regions FeatureCollection has no features")

    out = RegionSet(boundary_rule=cfg.boundary_rule)
    seen_ids: Dict[str, int] = {}
    for idx, feat in enumerate(features):
        if not isinstance(feat, dict):
            raise RegionGeometryError(f"region feature {idx} is not an object")
        props = feat.get("properties") or {}
        raw_id = props.get(cfg.region_id_field)
        if raw_id is None or (isinstance(raw_id, str) and not str(raw_id).strip()):
            raise RegionGeometryError(
                f"region feature {idx} missing region_id field "
                f"{cfg.region_id_field!r}"
            )
        rid = str(raw_id).strip()
        if rid in seen_ids:
            raise RegionGeometryError(
                f"duplicate region_id {rid!r} (features "
                f"{seen_ids[rid]} and {idx})"
            )
        seen_ids[rid] = idx

        geom_dict = feat.get("geometry")
        if not isinstance(geom_dict, dict):
            raise RegionGeometryError(f"region {rid!r} has no geometry")
        try:
            geom = shapely.geometry.shape(geom_dict)
        except Exception as e:
            raise RegionGeometryError(
                f"region {rid!r}: unparseable geometry ({e})"
            ) from e
        if geom.geom_type not in ("Polygon", "MultiPolygon"):
            raise RegionGeometryError(
                f"region {rid!r}: expected Polygon/MultiPolygon, got "
                f"{geom.geom_type}"
            )
        if not geom.is_valid:
            geom = make_valid(geom)
        if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
            raise RegionGeometryError(
                f"region {rid!r}: geometry empty/unsalvageable after make_valid"
            )
        _reject_dateline_extent(rid, geom)

        interior = geom.difference(geom.boundary)

        out.ids.append(rid)
        out.geoms.append(geom)
        prepare(out.geoms[-1])
        out.interiors.append(interior)
        prepare(out.interiors[-1])
        out.shifted.append([
            geom if off == 0.0 else shapely.affinity.translate(geom, xoff=off)
            for off in _LON_FRAMES
        ])
        for g in out.shifted[-1]:
            prepare(g)

    out.notes.append(
        f"boundary_rule={cfg.boundary_rule}: a fix exactly on the region "
        "boundary counts as "
        + ("inside" if cfg.boundary_rule == "closed" else "outside")
    )
    out.notes.append(
        "enter/exit/cross predicates use lon/lat (plate carrée) topology; "
        "motion quantities use WGS84 geodesics"
    )
    out.notes.append(
        "enter/exit instants between two fixes are sampling intervals "
        "(inferred=true), never exact times"
    )
    return out


def _reject_dateline_extent(rid: str, geom: BaseGeometry) -> None:
    minx, _, maxx, _ = geom.bounds
    if maxx - minx > 180.0:
        raise RegionGeometryError(
            f"region {rid!r}: longitude extent {maxx - minx:.1f}° exceeds "
            "180° — dateline-crossing polygons must be pre-split into "
            "two single-frame parts (rejected to avoid silently wrong "
            "point-in-polygon results)"
        )


@dataclass
class EventAnalysis:
    """Full-resolution computational result (tool layer stores/refs it)."""

    events: List[RegionEventModel] = field(default_factory=list)
    n_entities: int = 0
    n_regions: int = 0
    unknown_intervals: int = 0
    notes: List[str] = field(default_factory=list)
    #: (entity_id, region_id) -> {entity inside-fix counts} for denominators
    inside_fixes: Dict[Tuple[str, str], int] = field(default_factory=dict)
    entity_ids: List[str] = field(default_factory=list)
    region_ids: List[str] = field(default_factory=list)

    def to_summary(self, cfg_notes: Optional[List[str]] = None) -> Dict:
        by_type: Dict[str, int] = {}
        inferred = 0
        for ev in self.events:
            by_type[ev.event_type] = by_type.get(ev.event_type, 0) + 1
            if ev.inferred:
                inferred += 1
        return {
            "n_entities": self.n_entities,
            "n_regions": self.n_regions,
            "n_events": len(self.events),
            "by_type": by_type,
            "unknown_intervals": self.unknown_intervals,
            "inferred_events": inferred,
            "notes": list(self.notes) + list(cfg_notes or []),
        }


def _filter_window(
    points: List[ObsPoint], window: Optional[TimeWindow]
) -> List[ObsPoint]:
    if window is None or (window.start is None and window.end is None):
        return points
    start = window.start.astimezone(timezone.utc) if window.start else None
    end = window.end.astimezone(timezone.utc) if window.end else None
    out = []
    for p in points:
        if start is not None and p.t < start:
            continue
        if end is not None and p.t > end:
            continue
        out.append(p)
    return out


def _region_idx_by_entity_bbox(
    stream: List[ObsPoint], regions: RegionSet
) -> List[int]:
    """STRtree candidate regions for one entity's track bbox (bounded)."""
    lons = [p.lon for p in stream]
    lats = [p.lat for p in stream]
    # Expand across the antimeridian: test bbox in three frames via the
    # tree on original geoms (shifted copies only matter near ±180).
    minx, maxx = min(lons), max(lons)
    if maxx - minx <= 180.0:
        box = shapely.geometry.box(minx, min(lats), maxx, max(lats))
    else:
        box = shapely.geometry.box(-180.0, min(lats), 180.0, max(lats))
    tree = regions._tree  # type: ignore[attr-defined]
    if tree is None:
        return list(range(len(regions.ids)))
    hits = tree.query(box)
    return sorted(int(i) for i in hits)


def analyze_region_events(
    points: List[ObsPoint],
    regions: RegionSet,
    cfg: RegionEventConfig,
    window: Optional[TimeWindow] = None,
) -> EventAnalysis:
    """Run the region decision table per entity × candidate region.

    Output capped at ``MAX_EVENTS`` with a typed rejection. Sequence
    numbers are 1-based per (entity, region), chronological.
    """
    pts = sorted(_filter_window(points, window), key=lambda p: (p.t, p.order))
    # Build the STRtree once for candidate pruning.
    regions._tree = STRtree(regions.geoms) if regions.geoms else None  # type: ignore[attr-defined]

    analysis = EventAnalysis(
        n_regions=len(regions.ids),
        notes=list(regions.notes),
        region_ids=list(regions.ids),
    )
    by_entity: Dict[str, List[ObsPoint]] = {}
    for p in pts:
        by_entity.setdefault(p.entity_id, []).append(p)
    if len(by_entity) > MAX_ENTITIES:
        raise MovingObjectError(
            f"entity count {len(by_entity)} exceeds hard cap {MAX_ENTITIES}"
        )

    for entity_id in sorted(by_entity.keys()):
        stream = by_entity[entity_id]
        analysis.entity_ids.append(entity_id)
        for ridx in _region_idx_by_entity_bbox(stream, regions):
            rid = regions.ids[ridx]
            _walk_pair(
                analysis, entity_id, rid, ridx, stream, regions, cfg
            )
            if len(analysis.events) > MAX_EVENTS:
                raise MovingObjectError(
                    f"region events exceed hard cap {MAX_EVENTS}; narrow "
                    f"the time window or region set"
                )

    analysis.events.sort(key=lambda ev: (
        ev.t_exact or ev.t_interval_end or ev.t_interval_start or "",
        ev.entity_id,
        ev.region_id,
    ))
    # Assign per (entity, region) chronological sequence numbers.
    counters: Dict[Tuple[str, str], int] = {}
    for ev in analysis.events:
        key = (ev.entity_id, ev.region_id)
        counters[key] = counters.get(key, 0) + 1
        ev.sequence = counters[key]
    if len(analysis.events) > MAX_EVENTS:
        raise MovingObjectError(
            f"region events exceed hard cap {MAX_EVENTS}; narrow the time "
            f"window or region set"
        )
    analysis.n_entities = len(by_entity)
    return analysis


def _walk_pair(
    analysis: EventAnalysis,
    entity_id: str,
    rid: str,
    ridx: int,
    stream: List[ObsPoint],
    regions: RegionSet,
    cfg: RegionEventConfig,
) -> None:
    """Decision-table walk of one entity stream against one region."""
    seq_state_inside = False
    prev: Optional[ObsPoint] = None
    inside_run: List[ObsPoint] = []
    seq = 0
    def close_inside_run() -> None:
        nonlocal inside_run, seq
        if inside_run:
            if cfg.detect_dwell:
                duration = (inside_run[-1].t - inside_run[0].t).total_seconds()
                if duration >= cfg.min_dwell_s:
                    seq += 1
                    analysis.events.append(RegionEventModel(
                        entity_id=entity_id,
                        region_id=rid,
                        event_type="dwell",
                        t_exact=inside_run[0].t.isoformat(),
                        t_interval_start=inside_run[0].t.isoformat(),
                        t_interval_end=inside_run[-1].t.isoformat(),
                        inferred=False,
                        lon=inside_run[0].lon,
                        lat=inside_run[0].lat,
                        from_row=inside_run[0].source_row_id,
                        to_row=inside_run[-1].source_row_id,
                        sequence=seq,
                    ))
            inside_run = []

    for cur in stream:
        try:
            is_inside = regions.inside(ridx, cur.lon, cur.lat)
        except Exception:
            # Predicate failure: count as unknown and keep the last known
            # state/fix — the next evaluated fix forms a wider (honest)
            # bracket that still contains the true transition.
            analysis.unknown_intervals += 1
            continue

        if is_inside:
            analysis.inside_fixes[(entity_id, rid)] = (
                analysis.inside_fixes.get((entity_id, rid), 0) + 1
            )

        if prev is None:
            if is_inside:
                seq += 1
                analysis.events.append(RegionEventModel(
                    entity_id=entity_id,
                    region_id=rid,
                    event_type="enter",
                    t_exact=None,
                    t_interval_start=None,
                    t_interval_end=cur.t.isoformat(),
                    inferred=True,
                    lon=cur.lon,
                    lat=cur.lat,
                    from_row=None,
                    to_row=cur.source_row_id,
                    sequence=seq,
                ))
            prev = cur
            if is_inside:
                inside_run = [cur]
            seq_state_inside = is_inside  # must not be skipped by the continue
            continue

        if is_inside and not seq_state_inside:
            # outside -> inside (or unknown-state -> inside)
            seq += 1
            analysis.events.append(RegionEventModel(
                entity_id=entity_id,
                region_id=rid,
                event_type="enter",
                t_exact=None,
                t_interval_start=prev.t.isoformat(),
                t_interval_end=cur.t.isoformat(),
                inferred=True,
                lon=cur.lon,
                lat=cur.lat,
                from_row=prev.source_row_id,
                to_row=cur.source_row_id,
                sequence=seq,
            ))
            inside_run = [cur]
        elif is_inside and seq_state_inside:
            inside_run.append(cur)
        elif not is_inside and seq_state_inside:
            # inside -> outside
            seq += 1
            analysis.events.append(RegionEventModel(
                entity_id=entity_id,
                region_id=rid,
                event_type="exit",
                t_exact=None,
                t_interval_start=prev.t.isoformat(),
                t_interval_end=cur.t.isoformat(),
                inferred=True,
                lon=prev.lon,
                lat=prev.lat,
                from_row=prev.source_row_id,
                to_row=cur.source_row_id,
                sequence=seq,
            ))
            inside_run.append(prev)
            close_inside_run()
        else:
            # outside -> outside: cross candidate
            if cfg.detect_cross:
                try:
                    reached = regions.segment_reaches(
                        ridx, prev.lon, prev.lat, cur.lon, cur.lat
                    )
                except Exception:
                    analysis.unknown_intervals += 1
                    reached = False
                if reached:
                    seq += 1
                    analysis.events.append(RegionEventModel(
                        entity_id=entity_id,
                        region_id=rid,
                        event_type="cross",
                        t_exact=None,
                        t_interval_start=prev.t.isoformat(),
                        t_interval_end=cur.t.isoformat(),
                        inferred=True,
                        lon=None,
                        lat=None,
                        from_row=prev.source_row_id,
                        to_row=cur.source_row_id,
                        sequence=seq,
                    ))
        seq_state_inside = is_inside
        prev = cur

    if seq_state_inside and cfg.detect_dwell and inside_run:
        close_inside_run()


def region_summaries(
    analysis: EventAnalysis,
) -> List[RegionSummaryRow]:
    """Region-level aggregation with honest denominators (WP3).

    Every loaded region appears — regions with zero hits get
    ``entities_zero_hit == n_entities`` instead of vanishing from the
    denominator.
    """
    per_region: Dict[str, Dict] = {}
    for rid in analysis.region_ids:
        per_region[rid] = {
            "entities": set(), "inside_entities": set(),
            "enter": 0, "exit": 0, "cross": 0,
            "dwell": 0, "dwell_s": 0.0, "inside_fixes": 0,
            "unknown": 0,
        }
    n_analyzed = analysis.n_entities

    from datetime import datetime as _dt

    for ev in analysis.events:
        row = per_region.setdefault(ev.region_id, {
            "entities": set(), "inside_entities": set(),
            "enter": 0, "exit": 0, "cross": 0,
            "dwell": 0, "dwell_s": 0.0, "inside_fixes": 0, "unknown": 0,
        })
        row["entities"].add(ev.entity_id)
        row[ev.event_type] = row.get(ev.event_type, 0) + 1
        if ev.event_type == "dwell":
            t0_s = ev.t_interval_start or ev.t_exact
            t1_s = ev.t_interval_end or ev.t_exact
            if t0_s and t1_s:
                t0 = _dt.fromisoformat(t0_s)
                t1 = _dt.fromisoformat(t1_s)
                row["dwell_s"] += max(0.0, (t1 - t0).total_seconds())

    for (entity_id, rid), n in analysis.inside_fixes.items():
        row = per_region.setdefault(rid, {
            "entities": set(), "inside_entities": set(),
            "enter": 0, "exit": 0, "cross": 0,
            "dwell": 0, "dwell_s": 0.0, "inside_fixes": 0, "unknown": 0,
        })
        row["entities"].add(entity_id)
        row["inside_entities"].add(entity_id)
        row["inside_fixes"] += n

    rows: List[RegionSummaryRow] = []
    for rid in sorted(per_region.keys()):
        row = per_region[rid]
        rows.append(RegionSummaryRow(
            region_id=rid,
            n_entities=len(row["entities"]),
            n_enter=row.get("enter", 0),
            n_exit=row.get("exit", 0),
            n_cross=row.get("cross", 0),
            n_dwell_spans=row.get("dwell", 0),
            total_dwell_s=row["dwell_s"],
            total_inside_fixes=row["inside_fixes"],
            # Exposure honesty: only entities with an observed inside fix
            # count as "hit"; cross-only entities are zero-hit.
            entities_zero_hit=max(
                0, n_analyzed - len(row["inside_entities"])
            ),
            unknown_intervals=analysis.unknown_intervals,
        ))
    return rows


def event_features(
    events: List[RegionEventModel],
    max_features: int = 5000,
) -> List[Dict]:
    """GeoJSON Point features for enter/exit/cross map markers (bounded)."""
    feats: List[Dict] = []
    for ev in events[:max_features]:
        coords = None
        if ev.lon is not None and ev.lat is not None:
            coords = [ev.lon, ev.lat]
        elif ev.event_type == "cross":
            coords = None  # crossing markers have no observed fix position
        if coords is None:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": coords},
            "properties": {
                "entity_id": ev.entity_id,
                "region_id": ev.region_id,
                "event_type": ev.event_type,
                "t_exact": ev.t_exact,
                "t_interval_start": ev.t_interval_start,
                "t_interval_end": ev.t_interval_end,
                "inferred": ev.inferred,
                "sequence": ev.sequence,
                "from_row": ev.from_row,
                "to_row": ev.to_row,
            },
        })
    return feats
