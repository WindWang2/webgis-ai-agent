"""WP6 scale verification: ingest+build+dwell+events at 1k/10k/100k fixes.

Deterministic timing harness (no network, no randomness beyond seeded
oracles). Asserts bounded-output and no-crash semantics at each scale and
records wall-clock per stage so baselines are reproducible. This is a
scale-honesty test, not a throughput benchmark: it fails only on
correctness violations (caps, stale-free behavior, integrity) — the
printed numbers are the recorded measurement.

Run:  pytest tests/unit/moving_object/test_scale.py -q --no-cov -s
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from pyproj import Geod

from app.services.moving_object.builder import build_entity_trajectories
from app.services.moving_object.contracts import DwellConfig, IngestConfig, SegmentationConfig
from app.services.moving_object.dwell import detect_dwells
from app.services.moving_object.events import (
    RegionEventConfig,
    analyze_region_events,
    load_regions,
)
from app.services.moving_object.ingest import ingest_point_collection

_GEOD = Geod(ellps="WGS84")

_T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)


def _synthetic_tracks(n_points: int, n_entities: int = 20, seed: int = 5):
    """n_points total fixes spread over n_entities moving objects."""
    rng = random.Random(seed)
    feats: List[Dict[str, Any]] = []
    per_entity = max(2, n_points // n_entities)
    step_s = 30
    for e_idx in range(n_entities):
        lon, lat = 116.0 + e_idx * 0.001, 39.0 + e_idx * 0.001
        t = _T0 + timedelta(seconds=e_idx)
        for i in range(per_entity):
            t_iso = (t + timedelta(seconds=i * step_s)).isoformat()
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"entity_id": f"T{e_idx:03d}", "timestamp": t_iso},
            })
            az = rng.choice((90.0, 90.0, 90.0, 270.0))
            lon, lat = (lambda c: (_GEOD.fwd(c[0], c[1], az, 120.0)[:2]))((lon, lat))
    return feats


def _region_fc():
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"region_id": "Z1"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [115.9, 38.9], [116.2, 38.9], [116.2, 39.2], [115.9, 39.2],
             [115.9, 38.9]]]}},
        {"type": "Feature", "properties": {"region_id": "Z2"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [116.0, 39.0], [116.05, 39.0], [116.05, 39.05],
             [116.0, 39.05], [116.0, 39.0]]]}},
    ]}


def _run_scale(n_points: int) -> Dict[str, float]:
    feats = _synthetic_tracks(n_points)
    timings: Dict[str, float] = {}
    t0 = time.perf_counter()
    pts, diag = ingest_point_collection(feats, IngestConfig())
    timings["ingest"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    build = build_entity_trajectories(pts, SegmentationConfig(), IngestConfig())
    timings["build"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    dwells = detect_dwells(pts, DwellConfig())
    timings["dwell"] = time.perf_counter() - t0

    regions = load_regions(_region_fc(), RegionEventConfig(regions_ref="r"))
    t0 = time.perf_counter()
    analysis = analyze_region_events(pts, regions, RegionEventConfig(regions_ref="r"))
    timings["events"] = time.perf_counter() - t0

    # integrity, not throughput gates
    assert diag.accepted == len(feats)
    assert analysis.n_entities == diag.entity_count
    assert len(analysis.events) <= 100_000
    print(
        f"scale n={n_points:>7} entities={diag.entity_count:>4} "
        f"segments={build.n_segments:>6} dwells={len(dwells.events):>4} "
        f"events={len(analysis.events):>5} | "
        + " ".join(f"{k}={v * 1000:.0f}ms" for k, v in timings.items())
    )
    return timings


def test_scale_1k_10k_100k_bounded_and_recorded() -> None:
    for n in (1_000, 10_000, 100_000):
        timings = _run_scale(n)
        # soft regression bounds (documented hardware: local dev box);
        # these catch accidental O(n²) regressions, not machine speed.
        assert timings["ingest"] < 30.0
        assert timings["build"] < 60.0
        assert timings["dwell"] < 30.0
        assert timings["events"] < 120.0
