"""Acquisition-side strategy threshold single point (gap A7, ADR-0170).

Before this module the acquisition surfaces each carried their own inline
feature-count literal (seven sites, three magnitudes: 5000 / 20_000 / 50_000).
They are now consolidated here — one definition, repo-wide import.

Division of labour with ``app/services/data_fabric/limits.py`` (do NOT merge
them): ``limits.py`` is the runtime hard guard for query results (settings
driven, non-zero floors, raises ``ResultTooLargeError``); THIS module holds the
**strategy thresholds** — how many features a given surface (inline ref
payload, MVT tile, profile, mapspec composite, export) is allowed to carry as
a function of feature count × geometry complexity × viewport. ``limits.py``
protects the process; this module shapes the acquisition policy.

All defaults are ``provisional`` (task book §0.5): the DS8 calibration wave
re-fits them from measured distributions and drops the marker. Values here
are pure constants — no settings import — so every site can import them
without import-cycle risk.
"""
from __future__ import annotations

from typing import Optional

# ── Surface constants (the one definition of each magnitude) ────────────────

#: Session ref inline carrier gate (was ``mapspec_source.py INLINE_FEATURE_LIMIT``).
#: Above this a result must go through a ref: payload, not an inline GeoJSON.
INLINE_REF_LIMIT = 5_000

#: MVT features per tile (was ``postgis_adapter.py MVT_MAX_FEATURES_PER_TILE``).
MVT_TILE_FEATURE_LIMIT = 20_000

#: Inline feature gate for quality route / profiling surfaces
#: (was ``data_quality.py`` / ``data_profile/unified.py`` ``_MAX_INLINE_FEATURES``).
PROFILE_INLINE_LIMIT = 20_000

#: mapspec composite/lifecycle maxFeatures (was composite_builder / lifecycle_engine).
MAPSPEC_MAX_FEATURES = 50_000

#: Publication export maxFeatures fallback (was publication_export.py).
EXPORT_MAX_FEATURES = 50_000

#: Profiler scan-row ceiling (was data_profile/unified.py ``max_scan_rows`` cap).
PROFILE_SCAN_ROWS_LIMIT = 50_000

#: Fallback for ``settings.MAP_QUALITY_GATE_MAX_FEATURES`` (was lifecycle_engine 5000).
MAP_QUALITY_GATE_FALLBACK = 5_000

#: Surface-name → base limit registry (for tooling / lint / dashboards).
SURFACE_LIMITS = {
    "inline_ref": INLINE_REF_LIMIT,
    "mvt_tile": MVT_TILE_FEATURE_LIMIT,
    "profile_inline": PROFILE_INLINE_LIMIT,
    "profile_scan_rows": PROFILE_SCAN_ROWS_LIMIT,
    "mapspec": MAPSPEC_MAX_FEATURES,
    "export": EXPORT_MAX_FEATURES,
    "map_quality_gate": MAP_QUALITY_GATE_FALLBACK,
}

# ── Continuous policy (feature count × geometry complexity × viewport) ──────

#: Reference average vertices per feature. At this complexity a surface gets
#: its full base limit; heavier geometry scales the limit down proportionally.
REFERENCE_VERTICES = 20.0

#: The policy never drops below this many features — a plan that cannot carry
#: a meaningful minimum is useless; callers should degrade strategy instead.
_MIN_EFFECTIVE_LIMIT = 1_000

#: Viewport headroom: when the caller knows the expected viewport feature
#: count, the limit is capped at that count × this factor (headroom for
#: zoom-out interactions), never below ``_MIN_EFFECTIVE_LIMIT``.
VIEWPORT_HEADROOM = 1.5


def effective_feature_limit(
    base_limit: int,
    *,
    avg_vertices: Optional[float] = None,
    viewport_features: Optional[int] = None,
) -> int:
    """Continuous strategy limit for a surface (provisional, DS8-calibrated).

    ``effective = min(base / complexity_factor, viewport_headroom_cap)``

    - complexity factor: ``avg_vertices / REFERENCE_VERTICES``; ``None`` or
      ``<= 0`` (no evidence) → factor 1.0, i.e. the base limit unchanged.
      Heavy geometry (factor > 1) scales the limit down; lighter geometry
      never scales it **up** (the base already bounds the carrier).
    - viewport cap: ``viewport_features × VIEWPORT_HEADROOM`` when the caller
      has real viewport evidence; ``None`` → no viewport cap.
    - Result clamped to ``[_MIN_EFFECTIVE_LIMIT, base_limit]``.

    Deterministic and pure — same evidence, same limit (DS3 replay contract).
    """
    limit = float(base_limit)

    if avg_vertices is not None and avg_vertices > 0:
        complexity = float(avg_vertices) / REFERENCE_VERTICES
        if complexity > 1.0:
            limit = limit / complexity

    if viewport_features is not None and viewport_features > 0:
        cap = float(viewport_features) * VIEWPORT_HEADROOM
        limit = min(limit, cap)

    return int(max(_MIN_EFFECTIVE_LIMIT, min(base_limit, limit)))


__all__ = [
    "INLINE_REF_LIMIT",
    "MVT_TILE_FEATURE_LIMIT",
    "PROFILE_INLINE_LIMIT",
    "PROFILE_SCAN_ROWS_LIMIT",
    "MAPSPEC_MAX_FEATURES",
    "EXPORT_MAX_FEATURES",
    "MAP_QUALITY_GATE_FALLBACK",
    "SURFACE_LIMITS",
    "REFERENCE_VERTICES",
    "VIEWPORT_HEADROOM",
    "effective_feature_limit",
]
