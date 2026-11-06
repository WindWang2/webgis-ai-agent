"""Bounded workflow checks for complete populations and live input identity."""
from __future__ import annotations

import math
import logging
from pathlib import Path
import uuid


def network_facts(result: dict) -> dict[str, object]:
    summary = result.get('summary')
    if not isinstance(summary, dict):
        return {}
    facts: dict[str, object] = {}
    for key in ('demand_count', 'facility_count', 'routes_found', 'target_facility_count', 'unmatched_demand_count'):
        value = summary.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            facts[key] = value
    ids = summary.get('unmatched_demand_ids')
    if isinstance(ids, list):
        facts['unmatched_demand_ids'] = [str(value)[:128] for value in ids[:16]]
        facts['unmatched_ids_truncated'] = len(ids) > 16
    if facts.get('unmatched_demand_count', 0):
        facts['population_scope'] = 'matched_demand_only'
    return facts


def incomplete_routes(result: dict) -> bool:
    """Transport previews/descriptors are never complete scientific inputs."""
    if result.get('_routes_trimmed'):
        return True
    routes = result.get('routes')
    if isinstance(routes, list):
        summary = result.get('summary')
        expected = summary.get('routes_found') if isinstance(summary, dict) else None
        if isinstance(expected, (int, float)) and expected > len(routes):
            return True
        for route in routes:
            if (not isinstance(route, dict)
                    or not isinstance(route.get('geometry'), dict)
                    or not route['geometry'].get('coordinates')):
                return True
    return False


def local_raster_identity(ref: str) -> str:
    """Content + live file identity; unverifiable inputs disable reuse.

    Hashing is bounded to the canonical V5 corner windows. Live inode/size/
    nanosecond ctime+mtime additionally invalidate mid-raster overwrites. This
    is not a cryptographic claim against a privileged filesystem adversary.
    Never open a remote URI or a path outside the existing tool sandbox.
    """
    if not ref.startswith('ref:') and '://' not in ref:
        try:
            from app.utils.path import validate_data_path
            from app.lib.geo_raster.fingerprint import raster_content_fingerprint_v5
            path = Path(validate_data_path(ref))
            before = path.stat()
            digest = raster_content_fingerprint_v5(str(path))
            after = path.stat()
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            previous = tuple(getattr(before, key) for key in fields)
            current = tuple(getattr(after, key) for key in fields)
            if previous == current:
                return f'{digest}:{current}'
        except Exception:  # Identity failure disables reuse, never grants it.
            logging.getLogger(__name__).debug("Raster identity unavailable; disabling workflow reuse", exc_info=True)
    return 'unverifiable:' + uuid.uuid4().hex
