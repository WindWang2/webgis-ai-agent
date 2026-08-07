# 0048. Content-addressed Artifact Cache + raster windowing decisions

**Date:** 2026-08-07
**Status:** Accepted

## Part 1: Raster windowing decisions (goal §5)

`reclassify` and `raster_calculator` (aligned path) were windowed to a fixed
512×512 grid (ADR earlier this session). The four remaining §5 paths were
audited; the verdict for each is **document-and-skip** - they are already
block-streamed by the underlying engine, not full-array in Python:

| Path | Implementation | Why not windowed in our code |
|---|---|---|
| `resample_raster` | `rasterio.warp.reproject(source=rasterio.band(src,i), destination=rasterio.band(dst,i))` | GDAL's `WarpOperation` streams block-by-block internally; `rasterio.band()` does not materialize the full array in Python. Memory is already O(block), not O(full raster). |
| `zonal_statistics` | delegates to `rasterstats.zonal_stats()` (`app/lib/geo_analysis/raster_ops.py:10`) | `rasterstats` reads the raster windowed per feature; our wrapper only opens for CRS. No full-array read in our code. |
| NDVI / spectral | `compute_index_array(**bands)` runs numpy on already-fetched band arrays (`app/services/rs/band_math.py:69`) | Bands are materialized once by the STAC fetch (`stac_client.py`, already `run_in_executor`-offloaded). Windowing the numpy math after fetch buys nothing - the arrays are already in RAM. The real fix would be COG windowed reads at fetch time, a separate workstream. |
| `change_detection` | runs `compute_vegetation_index` twice in Celery, then diffs stats (`app/services/spatial_tasks.py:225`) | Same constraint as NDVI (bands already in memory); runs in Celery (compute-isolated). |

**Consequence**: no refactor for these four; the §5 DoD ("主要 raster math
支持 bounded-memory processing") is met for the paths where our code owns
the array materialization (reclassify, calculator). The GDAL/rasterstats
paths are bounded by their internal block streaming.

## Part 2: Content-addressed Artifact Cache (goal §6)

### Context

`resample_raster` is the most expensive file-producing operation (a CRS warp
can take minutes) and fully deterministic: same source + target resolution +
CRS + resampling -> identical output. Re-running it on a cache miss across
requests/sessions wastes CPU/disk/IO. The existing singleflight (ADR-0045)
protects *concurrent* duplicate compute; §6 is the *persistence* layer that
skips recompute across time.

### Decisions

1. **`app/lib/artifact_cache.py`**: content-addressed disk cache.
   - Key = `sha256(source identity, source mtime+size, operation, params,
     software version namespace)[:16]`. Source identity via mtime+size is
     cheaper than a content hash and sufficient (a rewrite changes mtime;
     `get_artifact` re-verifies the sidecar identity on hit).
   - Storage: `data/artifacts/<key>.tif` + `<key>.meta` sidecar (key, source
     path, identity, created_at for LRU). Path is under `data/` so
     `validate_data_path` accepts the returned path.
   - Atomic publish: compute to the function's own out_path, stream-copy to
     a temp file in the artifact dir, `os.replace` (atomic on POSIX). A
     partial/interrupted build leaves no claim on the cache key.
   - LRU eviction: total bytes capped at `MAX_ARTIFACT_BYTES` (default 5 GiB,
     env-tunable); on write, if exceeded, evict oldest by `.meta` mtime.
   - Invalidation: source mtime/size change -> different key (automatic);
     `ARTIFACT_VERSION_NS` bump -> different key (manual, for algorithm/rasterio
     version changes).
   - Concurrency: the compute closure runs only on a miss; concurrent misses
     are still singleflighted at the `tool_cache` layer (this cache sits
     *below* singleflight - a miss here still singleflights the compute).

2. **`resample_raster` integration** (`raster_math.py`): the warp body is
   wrapped in `publish_artifact(cache_key, raster_path, _compute)`. On a hit
   the cached GeoTIFF path is returned without recomputing; on a miss the
   closure runs and the result is atomically published. The response shape
   (`output_path`, `new_shape`, `target_crs`) is read back from the (possibly
   cached) output so it's always accurate.

### Consequences

- A repeat `resample` call with identical inputs returns in ~ms (file stat +
  read meta) instead of minutes. Memory unaffected (GDAL streams either way).
- Cache lives on disk under `data/artifacts/` (gitignored); LRU bounds it.
- Extending to `reclassify`/`raster_calculator` is a one-line wrap (same
  `publish_artifact` seam) - left as follow-up since those are cheaper.
- STAC-fetched spectral products aren't cached here (the source is a remote
  COG, not a local file with mtime) - a separate remote-artifact cache is
  future work.
