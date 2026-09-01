"""Deterministic fixtures for benchmark execute-tier cases.

Every builder is seeded and offline: no network, no LLM, no wall-clock input.
Builders return session-storable documents (GeoJSON FeatureCollections,
tables) or (path, arrays) pairs for raster goldens.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple

_DISTRICTS = ["锦江区", "青羊区", "金牛区", "武侯区", "成华区", "龙泉驿区"]


def chengdu_schools(n: int = 60) -> Dict[str, Any]:
    """Chengdu-range mock school POI (same shape as the golden tests)."""
    rng = random.Random(42)
    features: List[Dict[str, Any]] = []
    for i in range(n):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [
                104.0 + rng.random() * 0.25, 30.55 + rng.random() * 0.2,
            ]},
            "properties": {
                "name": f"小学{i}",
                "district": _DISTRICTS[i % len(_DISTRICTS)],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def chengdu_schools_large(n: int = 150_000) -> Dict[str, Any]:
    """150k-feature POI fixture (large-data contract, B2 G4)."""
    rng = random.Random(7)
    features: List[Dict[str, Any]] = []
    coords = [
        (104.0 + rng.random() * 0.25, 30.55 + rng.random() * 0.2)
        for _ in range(n)
    ]
    for i, (x, y) in enumerate(coords):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [x, y]},
            "properties": {"name": f"poi{i}", "district": _DISTRICTS[i % len(_DISTRICTS)]},
        })
    return {"type": "FeatureCollection", "features": features}


def admin_boundaries_chengdu() -> Dict[str, Any]:
    """Six coarse district polygons covering the schools extent."""
    base_x, base_y = 104.0, 30.55
    w, h = 0.125, 0.1
    features: List[Dict[str, Any]] = []
    for i, name in enumerate(_DISTRICTS):
        x = base_x + (i % 2) * w
        y = base_y + (i // 2) * h
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y],
            ]]},
            "properties": {"name": name, "population": 500_000 + i * 37_000},
        })
    return {"type": "FeatureCollection", "features": features}


def od_edges(k: int = 2_000) -> Dict[str, Any]:
    """Synthetic OD edge table: {origin_id, destination_id, origin_lng/lat,
    destination_lng/lat, weight} — flow vertical-slice input (D1).

    Rows are laid out on an origin×destination grid (⌈√(2k)⌉ origins) so the
    k rows cover k *distinct* pairs — top-N selection is meaningful.
    """
    import math as _math

    rng = random.Random(11)
    n_o = max(2, int(_math.ceil(_math.sqrt(k * 2))))
    n_d = max(2, int(_math.ceil(k / n_o)))
    origins = [(104.0 + rng.random() * 0.3, 30.55 + rng.random() * 0.25) for _ in range(n_o)]
    dests = [(104.05 + rng.random() * 0.3, 30.6 + rng.random() * 0.25) for _ in range(n_d)]
    rows: List[Dict[str, Any]] = []
    for i in range(k):
        if i >= n_o * n_d:
            break  # grid exhausted (distinct-pair guarantee holds for covered rows)
        o = origins[i % n_o]
        d = dests[i // n_o]
        rows.append({
            "origin_id": f"o{i % n_o}",
            "destination_id": f"d{i // n_o}",
            "origin_lng": o[0], "origin_lat": o[1],
            "destination_lng": d[0], "destination_lat": d[1],
            "weight": 1 + (i % 13),
        })
    return {"type": "od_table", "rows": rows}


def od_edges_50k() -> Dict[str, Any]:
    """≥50k flow edges (large-OD contract, Scenario E)."""
    return od_edges(50_000)


def ndvi_pair() -> Tuple[List[List[float]], List[List[float]], float]:
    """Red/NIR reflectance grids with a known NDVI mean (offline raster golden).

    red = 0.3, nir = 0.5 everywhere → ndvi = (nir-red)/(nir+red) = 0.25.
    Returns (red_grid, nir_grid, expected_mean).
    """
    red = [[0.3 for _ in range(8)] for _ in range(8)]
    nir = [[0.5 for _ in range(8)] for _ in range(8)]
    return red, nir, 0.25


#: alias → callable registry (runner resolves by name)
FIXTURE_BUILDERS = {
    "chengdu_schools": chengdu_schools,
    "chengdu_schools_large": chengdu_schools_large,
    "admin_boundaries_chengdu": admin_boundaries_chengdu,
    "od_edges": od_edges,
    "od_edges_50k": od_edges_50k,
}
