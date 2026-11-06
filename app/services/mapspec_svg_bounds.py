"""Bounded coordinate traversal for SVG extent discovery."""
from __future__ import annotations

import math
import time
from typing import Any, Callable, Iterable


def bounded_source_bounds(
    sources: Any,
    source_ids: Iterable[str],
    source_geojson: Callable[[Any], Any],
    valid_coordinates: Callable[[Any], bool],
    *, max_features: int, deadline: float,
) -> tuple[tuple[float, float, float, float], bool, bool]:
    west, south, east, north = math.inf, math.inf, -math.inf, -math.inf
    examined = vertices = 0
    truncated = timed_out = False
    for source_id in source_ids:
        source = sources.get(source_id) if isinstance(sources, dict) else None
        data = source_geojson(source) if isinstance(source, dict) else None
        if not isinstance(data, dict):
            continue
        features = data.get("features") if data.get("type") == "FeatureCollection" else [data]
        if not isinstance(features, list):
            continue
        for feature in features:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            if examined >= max_features or vertices >= 100_000:
                truncated = True
                break
            examined += 1
            geom = feature.get("geometry") if isinstance(feature, dict) else None
            coords = geom.get("coordinates") if isinstance(geom, dict) else None
            if not valid_coordinates(coords):
                continue
            stack = [iter([coords])]
            while stack:
                if vertices >= 100_000 or time.monotonic() >= deadline:
                    truncated = vertices >= 100_000
                    timed_out = not truncated
                    break
                try:
                    point = next(stack[-1])
                except StopIteration:
                    stack.pop()
                    continue
                if point and isinstance(point[0], (int, float)):
                    x, y = float(point[0]), float(point[1])
                    west, south, east, north = min(west, x), min(south, y), max(east, x), max(north, y)
                    vertices += 1
                elif isinstance(point, (list, tuple)):
                    stack.append(iter(point))
            if truncated or timed_out:
                break
        if truncated or timed_out:
            break
    return (west, south, east, north), truncated, timed_out
