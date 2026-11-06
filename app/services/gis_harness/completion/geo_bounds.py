"""Geographic bounding boxes retain wrapped dateline intervals."""
import math
from typing import Any, Iterable, Sequence


def normalize_bbox(value: Any) -> list[float] | None:
    try:
        west, south, east, north = map(float, value)
    except (TypeError, ValueError):
        return None
    if not all(map(math.isfinite, (west, south, east, north))) or not -90 <= south <= north <= 90:
        return None
    width = east - west if east >= west else east + 360 - west
    if width >= 360:
        return [-180.0, south, 180.0, north]
    return [west if -180 <= west <= 180 else (west + 180) % 360 - 180, south, east if -180 <= east <= 180 else (east + 180) % 360 - 180, north]


def longitude_intervals(box: Sequence[float]) -> list[tuple[float, float]]:
    west, _, east, _ = box
    return [(west, east)] if west <= east else [(west, 180.0), (-180.0, east)]


def bboxes_intersect(first: Any, second: Any) -> bool:
    first, second = normalize_bbox(first), normalize_bbox(second)
    if first is None or second is None or first[3] < second[1] or second[3] < first[1]:
        return False
    return any(a <= d and c <= b for a, b in longitude_intervals(first) for c, d in longitude_intervals(second))


def union_bboxes(boxes: Iterable[Sequence[float]]) -> list[float] | None:
    boxes = [box for value in boxes if (box := normalize_bbox(value)) is not None]
    if not boxes:
        return None
    south, north = min(box[1] for box in boxes), max(box[3] for box in boxes)
    if len(boxes) == 1:
        return boxes[0]
    west, east = min(box[0] for box in boxes), max(box[2] for box in boxes)
    if all(box[0] <= box[2] for box in boxes) and east - west <= 180:
        return [west, south, east, north]
    if any(box[2] - box[0] >= 360 for box in boxes):
        return [-180.0, south, 180.0, north]
    best = None
    for west in (box[0] for box in boxes):
        east = max(west + (box[0] - west) % 360 + ((box[2] - box[0]) % 360) for box in boxes)
        if best is None or east - west < best[2] - best[0]:
            best = [west, south, east, north]
    return normalize_bbox(best)
