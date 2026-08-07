# 0042. Raster Output Resource Guard (resample/warp grid budget)

**Date:** 2026-08-07
**Status:** Accepted

## Context

`resample_raster()` (`app/lib/geo_analysis/raster_math.py`) is the only CRS-warp
path in the app. It passes `target_resolution` straight into
`calculate_default_transform` with no validation of the *output* grid. A
unit-confusion request — `target_resolution=1` (meaning 1 m) on a degree-based
EPSG:4326 source warped to EPSG:3857 — produces a grid whose pixel count scales
with `1/res²`: a 3°×3° source (9 px) becomes **334,035 × 334,035 px
(~111.6 billion pixels, ~415.7 GiB uncompressed float32)**.

Observed consequences before the guard:

- The unit test `test_raster_resample_with_crs_change` had been performing this
  warp on **every run** since the raster tools were introduced (commit `912bc02`):
  a multi-minute accidental benchmark, not a correctness test. The 300s → 600s
  timeout bump (`c9df713`) masked it instead of fixing it.
- 45 such outputs (4.3 GB each, ~176 GB total) accumulated as residue in `data/`.
- A real user request with a large uploaded raster (up to the 200 MB upload
  cap) at 1 m in EPSG:3857 could reach **TiB-scale** output — multi-hour hang
  behind a 60s request timeout, disk fill, or worker OOM.

The existing `_MAX_GRID_CELLS` guard in `density.py` established the pattern:
module-level constant + early `ValueError` rejection.

## Decisions

1. **Guard the output grid at the single warp seam** — immediately after
   `calculate_default_transform`, before any profile creation or `reproject`
   call, in `resample_raster()`. Two call branches (GCP and bounds) converge
   there, so one check covers both.

2. **Three thresholds** (module constants in `raster_math.py`, grounded in
   legitimate workloads):

   | Constant | Value | Grounding |
   |---|---|---|
   | `MAX_OUTPUT_PIXELS` | 250,000,000 | ≈1 GiB float32 single-band. Passes: 30 m DEM over 3° (1.24e8), Sentinel-2 tile (1.2e8), 10k×10k grid (1e8). Rejects the pathological case (1.1e11) by 3 orders of magnitude. |
   | `MAX_OUTPUT_DIMENSION` | 100,000 per side | Catches extreme aspect ratios; real rasters are ≤ ~11k px/side. |
   | `MAX_OUTPUT_UPSCALE_RATIO` | 10,000× (out px / in px) | Catches unit confusion even when absolute output is small. Legitimate resampling is ≤ ~100×. |

3. **Agent-actionable rejection**: raise `ValueError` with estimated output
   dimensions, total pixels, uncompressed size, which limit(s) were exceeded,
   and *suggested coarser `target_resolution` values* (auto-derived from pixel
   budgets of 1e6 / 1e7 / 2.5e8, rounded up to 1/2/5×10^k). The registry's
   existing `ValueError → std_error_response` path surfaces this as a
   self-healing `correction_hint`; the Agent can retry with a suggested
   resolution.

4. **`target_resolution <= 0` is rejected** before the transform is computed.

5. **The pathological test is fixed, not time-tolerated**: the CRS-change test
   now uses `target_resolution=10000` (→ 34×34 px) and asserts the transform
   math (`new_shape == [34, 34]`, `target_crs == "EPSG:3857"`) instead of
   `_assert_ok` on a 5-minute warp. The `@pytest.mark.heavy`/`timeout(600)`
   markers were removed; regression tests in
   `tests/unit/test_raster_resource_guard.py` pin the rejection behavior.

## Consequences

- Pathological requests now fail in **~10 ms** with a correction hint instead of
  allocating hundreds of GiB / filling disk / OOMing the worker.
- The raster test suite dropped from minutes to seconds; ~176 GB of residue
  cleaned from `data/`.
- Output-side guard only; `reclassify`/`raster_calculator` remain input-bounded
  (full-array reads) — windowed streaming for those is tracked separately
  (raster streaming workstream).
- Thresholds are module constants, easy to tune if deployment memory grows.
