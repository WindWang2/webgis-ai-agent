"""Moving-object trajectory product orchestration (WP5 service seam).

One domain service shared verbatim by the Pi tool face and the REST API
(same discipline as flow_product / linked_exploration). Responsibilities:

- resolve the session dataset ref + descriptor (revision + fingerprint);
- ingest → build → dwell → region events under bounded workers
  (``asyncio.to_thread``; the kernels are pure CPU, no I/O);
- persist the product dict + derived FeatureCollections as session refs
  (LLM sees bounded summaries; the frontend fetches refs);
- cache identical (source+config) rebuilds behind session aliases;
- apply via one CAS MapSpec mutation (ApplyTrajectoryProductIntent);
- paged query over stored products.

Staleness contract: a product records ``source_revision``; apply refuses
(TrajectoryProductStale) when the source has moved on — no silent reuse.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from app.services.moving_object.builder import (
    SegmentCapExceeded,
    TrajectoryBuild,
    build_entity_trajectories,
    trajectory_line_features,
)
from app.services.moving_object.contracts import (
    MAX_PREVIEW_FEATURES,
    DwellConfig,
    IngestConfig,
    MovingObjectError,
    RegionEventConfig,
)
from app.services.moving_object.dwell import detect_dwells, dwell_point_features
from app.services.moving_object.events import (
    RegionGeometryError,
    event_features,
    load_regions,
    analyze_region_events,
    region_summaries,
)
from app.services.moving_object.ingest import (
    ingest_point_collection,
    preview_features,
)
from app.services.moving_object.presentation import build_map_fragment
from app.services.moving_object.quality import diagnose
from app.services.session_data import session_data_manager

SCHEMA_VERSION = "moving_object_trajectory.v1"
FAMILY_PREFIX = "traj"
#: Segment mean-speed sample kept inline for legend domain computation.
_SPEED_SAMPLE_CAP = 5000
#: Quality rows kept inline per product (top entities by observation count).
_QUALITY_ROWS_INLINE = 50


class TrajectoryServiceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TrajectoryProductConfig(BaseModel):
    """Declarative configuration for one trajectory product build."""

    dataset: str = Field(..., min_length=1, max_length=160,
                         json_schema_extra={"ref_cursor": True})
    # ingest
    entity_id_field: str = "entity_id"
    time_field: str = "timestamp"
    source_row_field: Optional[str] = None
    assumed_timezone: str = "UTC"
    duplicate_time_policy: Literal["keep_all", "keep_first", "keep_last"] = "keep_all"
    # segmentation
    max_gap_s: float = Field(3600.0, gt=0)
    max_speed_kmh: float = Field(300.0, gt=0)
    max_jump_m: float = Field(50_000.0, gt=0)
    min_points_per_segment: int = Field(2, ge=2, le=1000)
    # dwell
    detect_dwells: bool = True
    dwell_radius_m: float = Field(50.0, gt=0)
    dwell_min_dwell_s: float = Field(120.0, gt=0)
    dwell_min_points: int = Field(3, ge=2, le=10_000)
    # region events (optional)
    regions_ref: Optional[str] = Field(None, max_length=160,
                                       json_schema_extra={"ref_cursor": True})
    region_id_field: str = "region_id"
    boundary_rule: Literal["closed", "open"] = "closed"
    detect_cross: bool = True
    region_dwell_min_s: float = Field(60.0, gt=0)
    detect_region_dwell: bool = True
    # window + filter
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    entity_filter: Optional[List[str]] = Field(None, max_length=100)
    # style
    palette: Optional[List[str]] = None
    width_min_px: float = Field(1.5, gt=0)
    width_max_px: float = Field(7.0, ge=1.0, le=20.0)
    show_direction: bool = True
    product_id: Optional[str] = None

    def ingest_config(self) -> IngestConfig:
        return IngestConfig(
            entity_id_field=self.entity_id_field,
            time_field=self.time_field,
            source_row_field=self.source_row_field,
            source_crs="EPSG:4326",
            assumed_timezone=self.assumed_timezone,
            duplicate_time_policy=self.duplicate_time_policy,
        )

    def seg_config(self) -> "object":
        from app.services.moving_object.contracts import SegmentationConfig
        return SegmentationConfig(
            max_gap_s=self.max_gap_s,
            max_speed_kmh=self.max_speed_kmh,
            max_jump_m=self.max_jump_m,
            min_points_per_segment=self.min_points_per_segment,
        )

    def dwell_config(self) -> DwellConfig:
        return DwellConfig(
            radius_m=self.dwell_radius_m,
            min_dwell_s=self.dwell_min_dwell_s,
            min_points=self.dwell_min_points,
        )

    def region_config(self) -> RegionEventConfig:
        if not self.regions_ref:
            raise TrajectoryServiceError(
                "TRAJECTORY_REGIONS_REQUIRED",
                "region events requested without regions_ref",
            )
        return RegionEventConfig(
            regions_ref=self.regions_ref,
            region_id_field=self.region_id_field,
            boundary_rule=self.boundary_rule,
            detect_cross=self.detect_cross,
            detect_dwell=self.detect_region_dwell,
            min_dwell_s=self.region_dwell_min_s,
        )


async def _resolve_dataset(session_id: str, ref: str) -> Tuple[str, Any, Dict[str, Any]]:
    from app.tools._utils import resolve_ref_payload

    resolved = await session_data_manager.resolve_alias(session_id, ref)
    payload = await resolve_ref_payload(session_id, ref)
    descriptor = await session_data_manager.get_ref_descriptor(session_id, resolved)
    if descriptor is None:
        raise TrajectoryServiceError(
            "TRAJECTORY_SOURCE_NOT_FOUND",
            f"dataset {ref} is unavailable in this session",
        )
    return resolved, payload, descriptor


def _extract_features(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        feats = payload.get("features")
        if isinstance(feats, list):
            return feats
    if isinstance(payload, list):
        return payload
    raise TrajectoryServiceError(
        "TRAJECTORY_SOURCE_INVALID",
        "trajectory dataset must be a GeoJSON FeatureCollection or feature list",
    )


def _fingerprint(obj: Any) -> str:
    try:
        blob = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        blob = repr(obj)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:32]


def _family_id(source_ref: str, entity_id_field: str, time_field: str,
               regions_ref: Optional[str]) -> str:
    """Stable binding identity (G21 discipline): one trajectory family per
    (dataset binding + regions binding). Config/window iterations REPLACE
    the family on apply instead of stacking layer families."""
    binding = f"{source_ref}|{entity_id_field}|{time_field}|{regions_ref or ''}"
    return f"{FAMILY_PREFIX}_{_fingerprint(binding).split(':')[-1][:12]}"


def _cache_alias(kind: str, key: str) -> str:
    return f"{FAMILY_PREFIX}-{kind}-{key.split(':')[-1][:40]}"


def _filter_entities(
    feats: List[Dict[str, Any]], entity_ids: Optional[List[str]]
) -> List[Dict[str, Any]]:
    if not entity_ids:
        return feats
    wanted = set(entity_ids)
    out = []
    for f in feats:
        props = f.get("properties") or {}
        if props.get("entity_id") in wanted:
            out.append(f)
    return out


def _apply_window(feats: List[Dict[str, Any]], cfg: TrajectoryProductConfig) -> List[Dict[str, Any]]:
    if not cfg.time_start and not cfg.time_end:
        return feats
    from app.services.moving_object.ingest import parse_event_time
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(cfg.assumed_timezone)
    if cfg.time_start:
        start = parse_event_time(cfg.time_start, tz)[0]
        if start is None:
            raise TrajectoryServiceError(
                "TRAJECTORY_WINDOW_INVALID",
                f"time_start {cfg.time_start!r} is not a parseable instant",
            )
    else:
        start = None
    if cfg.time_end:
        end = parse_event_time(cfg.time_end, tz)[0]
        if end is None:
            raise TrajectoryServiceError(
                "TRAJECTORY_WINDOW_INVALID",
                f"time_end {cfg.time_end!r} is not a parseable instant",
            )
    else:
        end = None
    kept: List[Dict[str, Any]] = []
    for f in feats:
        props = f.get("properties") or {}
        t = parse_event_time(props.get(cfg.time_field), tz)[0]
        if t is None:
            kept.append(f)  # unparseable times flow to ingest diagnostics
            continue
        if start is not None and t < start:
            continue
        if end is not None and t > end:
            continue
        kept.append(f)
    return kept


async def inspect_moving_object_source(
    session_id: str,
    dataset: str,
    entity_id_field: str = "entity_id",
    time_field: str = "timestamp",
    assumed_timezone: str = "UTC",
) -> Dict[str, Any]:
    """Read-only source inspection: fields, sample, quick ingest health."""
    source_ref, payload, descriptor = await _resolve_dataset(session_id, dataset)
    feats = _extract_features(payload)

    def _probe() -> Tuple[Any, Any]:
        cfg = IngestConfig(
            entity_id_field=entity_id_field,
            time_field=time_field,
            source_row_field=None,
            source_crs="EPSG:4326",
            assumed_timezone=assumed_timezone,
        )
        return ingest_point_collection(_extract_features(payload), cfg)

    pts, diag = await asyncio.to_thread(_probe)
    field_names: Dict[str, str] = {}
    for f in feats[:50]:
        props = f.get("properties") or {}
        for k, v in props.items():
            field_names.setdefault(str(k)[:64], type(v).__name__)
    sample = [
        {str(k)[:64]: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v)
         for k, v in list((f.get("properties") or {}).items())[:24]}
        for f in feats[:8]
    ]
    return {
        "success": True,
        "operation": "inspect",
        "source_ref": source_ref,
        "source_revision": int(descriptor.get("content_revision") or 0),
        "feature_count": len(feats),
        "fields": field_names,
        "sample": sample,
        "sample_truncated": len(feats) > len(sample),
        "ingest_health": diag.model_dump(),
        "entity_preview": preview_features(pts, 20),
    }


async def preview_trajectory_product(
    session_id: str,
    config: TrajectoryProductConfig,
) -> Dict[str, Any]:
    """Build (or reuse) the trajectory product and return the envelope.

    Region events (WP3) run as part of the same build when
    ``config.regions_ref`` is set — one build, one product, one artifact
    family; no partial second entry.
    """
    source_ref, payload, descriptor = await _resolve_dataset(session_id, config.dataset)
    regions_fc: Optional[Dict[str, Any]] = None
    regions_revision = 0
    if config.regions_ref:
        _, regions_fc, regions_desc = await _resolve_dataset(
            session_id, config.regions_ref
        )
        regions_revision = int(regions_desc.get("content_revision") or 0)
    feats = _filter_entities(_extract_features(payload), config.entity_filter)
    feats = _apply_window(feats, config)
    source_revision = int(descriptor.get("content_revision") or 0)
    # Serializing 500k-row feature lists is CPU-bound: keep it off the loop.
    def _keys() -> Tuple[str, str]:
        source_key = _fingerprint({"feats": feats, "revision": source_revision})
        full_key = _fingerprint({
            "source": source_key, "config": config.model_dump(mode="json"),
            "regions_revision": regions_revision,
        })
        return source_key, full_key
    source_key, full_key = await asyncio.to_thread(_keys)
    full_alias = _cache_alias("preview", full_key)
    cached_ref = await session_data_manager.resolve_alias(session_id, full_alias)
    if cached_ref != full_alias:
        cached = await session_data_manager.get(session_id, cached_ref)
        if isinstance(cached, dict) and cached.get("schema_version") == SCHEMA_VERSION \
                and cached.get("fingerprint") == full_key:
            return await _envelope(session_id, cached, cache_hit="preview")

    family_id = _family_id(
        source_ref, config.entity_id_field, config.time_field, config.regions_ref
    )
    try:
        product, line_fc, dwell_fc, event_fc = await asyncio.to_thread(
            _build_product_payload, feats, config, source_ref, source_revision,
            source_key, full_key, regions_fc, family_id,
        )
    except RegionGeometryError as e:
        # must precede MovingObjectError (subclass)
        raise TrajectoryServiceError("TRAJECTORY_REGION_INVALID", str(e)) from e
    except SegmentCapExceeded as e:
        raise TrajectoryServiceError("TRAJECTORY_SCALE_EXCEEDED", str(e)) from e
    except MovingObjectError as e:
        raise TrajectoryServiceError("TRAJECTORY_SCALE_EXCEEDED", str(e)) from e
    except ValidationError as e:
        raise TrajectoryServiceError("TRAJECTORY_INPUT_INVALID", str(e)) from e
    product = await _store_product(session_id, product, line_fc, dwell_fc, event_fc)
    await session_data_manager.set_alias(session_id, product["product_ref"], full_alias)
    return await _envelope(session_id, product, cache_hit="miss")


def _build_product_payload(
    feats: List[Dict[str, Any]],
    config: TrajectoryProductConfig,
    source_ref: str,
    source_revision: int,
    source_key: str,
    full_key: str,
    regions_fc: Optional[Dict[str, Any]] = None,
    family_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Pure-CPU product assembly (runs inside asyncio.to_thread)."""
    try:
        points, diag = ingest_point_collection(feats, config.ingest_config())
    except MovingObjectError as e:
        raise TrajectoryServiceError("TRAJECTORY_INPUT_INVALID", str(e)) from e

    build: TrajectoryBuild = build_entity_trajectories(
        points, config.seg_config(), config.ingest_config(),  # type: ignore[arg-type]
    )

    dwell_build = None
    if config.detect_dwells and points:
        dwell_build = detect_dwells(points, config.dwell_config())

    analysis = None
    if regions_fc is not None:
        region_cfg = config.region_config()
        regions = load_regions(regions_fc, region_cfg)
        analysis = analyze_region_events(points, regions, region_cfg, None)

    line_fc = {"type": "FeatureCollection",
               "features": trajectory_line_features(build)}
    dwell_fc = {"type": "FeatureCollection",
                "features": dwell_point_features(dwell_build) if dwell_build else []}
    event_fc = {"type": "FeatureCollection",
                "features": event_features(analysis.events) if analysis else []}

    zero_extent = sum(
        1 for traj in build.trajectories.values() for seg in traj.segments
        if seg.path_length_m <= 0.0
    )
    speed_sample: List[float] = []
    for traj in build.trajectories.values():
        for seg in traj.segments:
            if isinstance(seg.mean_speed_kmh, (int, float)) and seg.mean_speed_kmh >= 0:
                speed_sample.append(float(seg.mean_speed_kmh))
            if len(speed_sample) >= _SPEED_SAMPLE_CAP:
                break
        if len(speed_sample) >= _SPEED_SAMPLE_CAP:
            break

    break_counts: Dict[str, int] = {}
    for br in build.breaks:
        break_counts[br.reason] = break_counts.get(br.reason, 0) + 1

    now = datetime.now(timezone.utc).isoformat()
    product: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "family_id": family_id or _family_id(
            source_ref, config.entity_id_field, config.time_field,
            config.regions_ref),
        "source_ref": source_ref,
        "source_revision": source_revision,
        "source_fingerprint": source_key,
        "fingerprint": full_key,
        "created_at": now,
        "config": config.model_dump(mode="json"),
        "coverage": {
            "crs": "EPSG:4326 (WGS84 lon/lat; geodesic distances)",
            "assumed_timezone": config.assumed_timezone,
            "time_min": diag.time_min,
            "time_max": diag.time_max,
            "sampling": (
                None if not points or len(points) < 2 else {
                    "note": "global median/p90 observation gap (s)",
                }
            ),
        },
        "stats": {
            "observations": diag.accepted,
            "input_rows": diag.input_rows,
            "entities": len(build.trajectories),
            "segments": build.n_segments,
            "segments_zero_extent": zero_extent,
            "breaks_total": len(build.breaks),
            "break_reasons": break_counts,
            "dropped_runs": sum(build.dropped_runs.values()),
            "degenerate_intervals": sum(build.degenerate_intervals.values()),
            "dwells": len(dwell_build.events) if dwell_build else 0,
            "dwell_near_miss_runs": dwell_build.diagnostics.near_miss_runs if dwell_build else 0,
            "total_path_length_m": sum(
                t.total_path_length_m for t in build.trajectories.values()
            ),
            "speed_sample": speed_sample,
        },
        "quality": {
            "ingest": diag.model_dump(),
            "notes": [],  # filled by diagnose() below for consistency
        },
        "assumptions": [
            "运动量均为相邻观测间的区间估计（非瞬时值）；dt=0 对不产生速度",
            "乱序/重复/缺失行按计数披露，不做插值补齐",
            "same-time 不同位置视为数据矛盾并分段（zero_dt_conflict）",
        ],
        "warnings": [],
    }
    report = diagnose(points, diag, build, config.ingest_config())
    product["quality"] = {
        "ingest": diag.model_dump(),
        "notes": report.notes,
        "break_reason_counts": report.break_reason_counts,
        "overall_sampling": (
            report.overall_sampling.model_dump() if report.overall_sampling else None
        ),
        "top_entities": [
            row.model_dump()
            for row in sorted(
                report.entities, key=lambda r: -r.n_observations
            )[:_QUALITY_ROWS_INLINE]
        ],
    }
    if analysis is not None:
        product["events"] = {
            "summary": analysis.to_summary(),
            "region_summaries": [r.model_dump() for r in region_summaries(analysis)],
            "boundary_rule": config.boundary_rule,
            "regions_ref": config.regions_ref,
            "sample_events": [
                ev.model_dump() for ev in analysis.events[:MAX_PREVIEW_FEATURES]
            ],
        }
    if zero_extent:
        product["warnings"].append(
            f"{zero_extent} segments have zero path extent (coincident fixes) "
            "and are excluded from the line layer (counted, not drawn)"
        )
    return product, line_fc, dwell_fc, event_fc


async def _store_product(
    session_id: str,
    product: Dict[str, Any],
    line_fc: Dict[str, Any],
    dwell_fc: Dict[str, Any],
    event_fc: Dict[str, Any],
) -> Dict[str, Any]:
    product = copy.deepcopy(product)
    product.pop("mapspec", None)
    product_ref = product.get("product_ref") or await session_data_manager.store(
        session_id, product, prefix="traj-product"
    )
    product["product_ref"] = product_ref
    for fc, focus in ((line_fc, "line"), (dwell_fc, "dwell"), (event_fc, "event")):
        for feature in fc.get("features", []):
            props = feature.setdefault("properties", {})
            props["product_ref"] = product_ref
            props["trajectory_focus"] = focus
    if line_fc.get("features") and not product.get("line_ref"):
        product["line_ref"] = await session_data_manager.store(
            session_id, line_fc, prefix="geojson"
        )
    if dwell_fc.get("features") and not product.get("dwell_ref"):
        product["dwell_ref"] = await session_data_manager.store(
            session_id, dwell_fc, prefix="geojson"
        )
    if event_fc.get("features") and not product.get("event_ref"):
        product["event_ref"] = await session_data_manager.store(
            session_id, event_fc, prefix="geojson"
        )
    product["mapspec"] = build_map_fragment(
        product, product.get("config") or {}
    )
    if not await session_data_manager.overwrite(session_id, product_ref, product):
        raise TrajectoryServiceError(
            "TRAJECTORY_PRODUCT_STORE_FAILED",
            "trajectory product could not be persisted",
        )
    return product


async def _envelope(
    session_id: str, product: Dict[str, Any], *, cache_hit: str
) -> Dict[str, Any]:
    state = await session_data_manager.get_map_state(session_id) or {}
    stats = product.get("stats", {})
    can_apply = bool(product.get("line_ref"))
    blocking = [] if can_apply else ["no_observable_motion"]
    events = product.get("events")
    return {
        "success": True,
        "operation": "preview",
        "product_ref": product["product_ref"],
        "family_id": product["family_id"],
        "source_ref": product["source_ref"],
        "source_revision": product["source_revision"],
        "source_fingerprint": product["source_fingerprint"],
        "product_fingerprint": product["fingerprint"],
        "cache_hit": cache_hit,
        "line_ref": product.get("line_ref"),
        "dwell_ref": product.get("dwell_ref"),
        "event_ref": product.get("event_ref"),
        "stats": {k: v for k, v in stats.items() if k != "speed_sample"},
        "quality": product.get("quality"),
        "coverage": product.get("coverage"),
        "assumptions": product.get("assumptions"),
        "warnings": product.get("warnings"),
        "events_summary": (events or {}).get("summary"),
        "region_summaries": (events or {}).get("region_summaries"),
        "can_apply": can_apply,
        "blocking_reasons": blocking,
        "mapspec_fragment": product.get("mapspec"),
        "mapspec_revision": int(state.get("_cartographic_mutation_revision") or 0),
    }


async def apply_trajectory_product_preview(
    session_id: str,
    product_ref: str,
    *,
    expected_revision: int,
    mutation_id: Optional[str] = None,
    origin: str = "agent",
) -> Dict[str, Any]:
    product = await session_data_manager.get(session_id, product_ref)
    if not isinstance(product, dict) or product.get("schema_version") != SCHEMA_VERSION:
        raise TrajectoryServiceError(
            "TRAJECTORY_PRODUCT_NOT_FOUND",
            "trajectory product was not found in this session",
        )
    from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine

    result = await MapSpecLifecycleEngine().apply_mutation(
        session_id,
        _intent_for(product),
        origin=origin,  # type: ignore[arg-type]
        expected_revision=expected_revision,
        mutation_id=mutation_id,
    )
    payload = result.to_dict()
    payload.update({
        "success": not result.is_error and not result.superseded,
        "operation": "apply",
        "product_ref": product_ref,
        "family_id": product["family_id"],
        "stats": {
            k: v for k, v in (product.get("stats") or {}).items()
            if k != "speed_sample"
        },
    })
    return payload


def _intent_for(product: Dict[str, Any]) -> Any:
    from app.services.mapspec.intents import ApplyTrajectoryProductIntent
    return ApplyTrajectoryProductIntent(
        product_ref=str(product.get("product_ref")),
        family_id=str(product.get("family_id")),
    )


async def query_trajectory_product(
    session_id: str,
    product_ref: str,
    *,
    kind: str = "product",
    entity_id: str = "",
    offset: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    product = await session_data_manager.get(session_id, product_ref)
    if not isinstance(product, dict) or product.get("schema_version") != SCHEMA_VERSION:
        raise TrajectoryServiceError(
            "TRAJECTORY_PRODUCT_NOT_FOUND",
            "trajectory product was not found in this session",
        )
    limit = max(1, min(200, limit))
    offset = max(0, offset)

    def _page(items: List[Any]) -> Dict[str, Any]:
        return {
            "items": items[offset:offset + limit],
            "total": len(items),
            "offset": offset,
            "limit": limit,
        }

    if kind == "product":
        return {"success": True, "kind": "product",
                "product": {k: v for k, v in product.items() if k != "mapspec"}}
    if kind == "entity":
        line_fc = await session_data_manager.get(session_id, product.get("line_ref"))
        feats = (line_fc or {}).get("features", []) if isinstance(line_fc, dict) else []
        if entity_id:
            feats = [f for f in feats
                     if (f.get("properties") or {}).get("entity_id") == entity_id]
        return {"success": True, "kind": "entity",
                "entity_id": entity_id, "page": _page(feats)}
    if kind == "dwell":
        dwell_fc = await session_data_manager.get(session_id, product.get("dwell_ref"))
        feats = (dwell_fc or {}).get("features", []) if isinstance(dwell_fc, dict) else []
        if entity_id:
            feats = [f for f in feats
                     if (f.get("properties") or {}).get("entity_id") == entity_id]
        return {"success": True, "kind": "dwell", "entity_id": entity_id,
                "page": _page(feats)}
    if kind == "event":
        events = (product.get("events") or {}).get("sample_events", [])
        if entity_id:
            events = [e for e in events if e.get("entity_id") == entity_id]
        n_total = int((product.get("events") or {}).get(
            "summary", {}).get("n_events") or 0)
        return {"success": True, "kind": "event", "entity_id": entity_id,
                "total_is_sample": len(events) < n_total,
                "page": _page(events)}
    raise TrajectoryServiceError(
        "TRAJECTORY_QUERY_KIND_INVALID",
        "kind must be one of product|entity|dwell|event",
    )
