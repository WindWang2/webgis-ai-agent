"""Field role inference (DS6, ADR-0176): which column is time / geography /
measure / id — the field-role view the cartography line consumes via D1.

Reuses the existing profilers where they apply (``temporal.profiler`` for
time-column evidence, ``lib.gis.semantic_profile`` for semantic hints) and
adds declaration-level name/type heuristics so the function is useful even
before any data is fetched (registry-declared fields only).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_TIME_NAME = re.compile(
    r"(date|time|year|month|quarter|week|day|日期|时间|年份|年度|月份|季度)", re.I
)
_GEO_NAME = re.compile(
    r"(province|city|county|district|town|street|adcode|admin|region|basin|"
    r"省|市|县|区|乡镇|街道|流域|行政区|区划)", re.I
)
_ID_NAME = re.compile(r"(^id$|_id$|^uuid$|code$|编码|编号)", re.I)
_MEASURE_TYPES = re.compile(r"(int|float|double|numeric|decimal|real)", re.I)


def infer_field_roles(
    fields: List[Dict[str, Any]],
    *,
    profiler: Optional[Any] = None,
) -> Dict[str, List[str]]:
    """Classify declared fields into roles.

    ``fields``: [{name, type}, …] (registry declaration or describe output).
    ``profiler``: optional pre-built ``temporal.profiler`` column evaluator
    (reuse seam); when provided its verdict on a candidate time column wins.

    Returns {"time": […], "geo": […], "measure": […], "id": […]} — a field
    may appear under one primary role only (first match by precedence
    time > geo > id > measure).
    """
    roles: Dict[str, List[str]] = {"time": [], "geo": [], "measure": [], "id": []}
    for f in fields or []:
        name = str(f.get("name") or "")
        if not name:
            continue
        ftype = str(f.get("type") or "")
        if _is_time(name, ftype, profiler):
            roles["time"].append(name)
        elif _GEO_NAME.search(name):
            roles["geo"].append(name)
        elif _ID_NAME.search(name):
            roles["id"].append(name)
        elif _MEASURE_TYPES.search(ftype):
            roles["measure"].append(name)
    return roles


def _is_time(name: str, ftype: str, profiler: Optional[Any]) -> bool:
    if _TIME_NAME.search(name):
        return True
    if profiler is not None:
        try:
            return bool(profiler.is_time_column(name))
        except Exception:  # noqa: BLE001 — profiler failure falls back to names
            pass
    return bool(re.search(r"(date|datetime|timestamp)", ftype, re.I))


__all__ = ["infer_field_roles"]
