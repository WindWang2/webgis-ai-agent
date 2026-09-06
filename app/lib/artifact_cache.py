"""Content-addressed disk artifact cache for expensive deterministic GIS outputs.

Goal §6: reprojected/resampled rasters, NDVI, terrain products, and other
file-producing operations are expensive and deterministic. Re-running them
on the same (source + operation + params) wastes CPU/disk/IO. This cache
stores the output GeoTIFF under ``data/artifacts/<key>.tif`` keyed by a
content hash of the inputs; a hit returns the cached path without recomputing.

Design (ADR-0048):
  - Key = sha256(source identity, source mtime+size, operation, params,
    target CRS/res, software version namespace). Stable across sessions.
  - Storage: ``data/artifacts/``; one file per key (``<key>.tif``) + a
    ``.meta`` sidecar with the key + created_at (for LRU eviction).
  - Atomic publish: compute to a temp file, then ``os.replace`` (atomic on
    POSIX). A partial/interrupted build leaves no claim on the cache key.
  - LRU eviction: total bytes capped at ``MAX_ARTIFACT_BYTES``; on write, if
    the cap is exceeded, evict oldest (by ``.meta`` mtime) until under cap.
  - Concurrency: the *compute* is protected by the existing singleflight
    (``app.lib.tool_cache``); this cache is the *persistence* layer that
    sits below singleflight - a miss here still singleflights the compute.
  - Invalidation: source mtime/size change -> different key (automatic);
    software version namespace bump (e.g. rasterio/raster_math version) ->
    different key (manual bump of ``ARTIFACT_VERSION_NS``).

Not cached: session-scoped refs (those live in session_data); non-file
outputs (in-memory arrays/GeoJSON stay in tool_cache).
"""
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Where cached artifacts live (under the project data/ dir so validate_data_path
# accepts the returned path). Created lazily on first write.
ARTIFACT_DIR = os.path.join("data", "artifacts")

# Total bytes cap for the artifact cache. ~5 GiB is generous for a dev/single-
# host box; tune via env for production. LRU evicts oldest when exceeded.
MAX_ARTIFACT_BYTES = int(os.environ.get("WEBGIS_ARTIFACT_CACHE_BYTES", 5 * 1024 ** 3))

# Software version namespace: bump when the compute algorithm / rasterio version
# changes in a way that would invalidate existing artifacts.
# v2 (Runtime V3, ADR-0089): calculator/indices moved to budget-derived windows
# + WarpedVRT alignment (bilinear for continuous B instead of nearest) + tiled
# LZW outputs with overviews — cached v1 outputs no longer match recompute.
ARTIFACT_VERSION_NS = "raster_math-v2"


def _source_identity(source_path: str) -> str:
    """Stable identity for a source file: path + mtime + size.

    mtime+size is cheaper than a content hash and sufficient for cache
    invalidation (a rewrite changes mtime; a same-size rewrite is vanishingly
    unlikely to produce identical GIS output). Falls back to the path string
    when the file is missing (e.g. a remote ref) - callers should not cache
    those.
    """
    try:
        st = os.stat(source_path)
        return f"{source_path}|{int(st.st_mtime)}|{st.st_size}"
    except OSError:
        return f"{source_path}|unstatable"


def make_artifact_key(
    source_path: str,
    operation: str,
    params: dict,
    extra_ns: str = "",
) -> str:
    """Compute a content-addressed cache key for a file-producing operation.

    Args:
        source_path: input file path (identity via mtime+size).
        operation: e.g. "resample", "reclassify", "raster_calculator".
        params: the operation's parameters (CRS, resolution, scheme, ...).
        extra_ns: extra namespace string (e.g. expression for calculator).

    Returns:
        16-hex key; the cached file lives at ``data/artifacts/<key>.tif``.
    """
    identity = _source_identity(source_path)
    canonical = json.dumps(
        {
            "src": identity,
            "op": operation,
            "params": params,
            "ns": f"{ARTIFACT_VERSION_NS}|{extra_ns}",
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _artifact_path(key: str) -> str:
    return os.path.join(ARTIFACT_DIR, f"{key}.tif")


def _meta_path(key: str) -> str:
    return os.path.join(ARTIFACT_DIR, f"{key}.meta")


def get_artifact(key: str) -> Optional[str]:
    """Return the cached artifact path if present and the source still matches.

    Verifies the source identity recorded in the ``.meta`` sidecar still
    matches (mtime/size unchanged) - defends against a same-key collision
    after an out-of-band source rewrite that didn't change mtime granularity.
    """
    path = _artifact_path(key)
    meta = _meta_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(meta, "r", encoding="utf-8") as f:
            recorded = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None  # corrupt meta -> treat as miss
    if recorded.get("src_identity") != _source_identity(recorded.get("src_path", "")):
        return None  # source changed -> stale, miss
    # bump atime on the meta for LRU recency
    os.utime(meta, None)
    return path


def publish_artifact(key: str, src_path: str, compute: Callable[[], str]) -> str:
    """Compute (if missing) and publish an artifact; return its path.

    ``compute`` is called only on a miss and must return the path to the
    freshly produced output file (typically the function's own out_path).
    The result is atomically copied to ``data/artifacts/<key>.tif`` via a
    temp file + ``os.replace``; the source output is left in place (callers
    may clean it). On any failure the cache stays clean (no partial file).
    """
    cached = get_artifact(key)
    if cached is not None:
        return cached

    out_path = compute()
    if not out_path or not os.path.exists(out_path):
        # compute returned nothing or failed silently - don't cache
        return out_path

    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    final = _artifact_path(key)
    # Atomic publish: copy to temp, then rename.
    fd, tmp = tempfile.mkstemp(suffix=".tif", dir=ARTIFACT_DIR)
    try:
        os.close(fd)
        with open(out_path, "rb") as src_f, open(tmp, "wb") as dst_f:
            # Stream copy (artifacts can be large); 1 MiB chunks.
            while True:
                chunk = src_f.read(1024 * 1024)
                if not chunk:
                    break
                dst_f.write(chunk)
        os.replace(tmp, final)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        logger.warning(f"[artifact_cache] publish failed for {key}", exc_info=True)
        return out_path  # fall back to the direct output

    # Sidecar meta for LRU + invalidation.
    try:
        with open(_meta_path(key), "w", encoding="utf-8") as f:
            json.dump({
                "key": key,
                "src_path": src_path,
                "src_identity": _source_identity(src_path),
                "operation": key,  # informational
                "created_at": time.time(),
            }, f)
    except OSError:
        pass

    _evict_if_needed()
    return final


def _evict_if_needed() -> None:
    """LRU eviction: if total bytes exceed the cap, remove oldest until under."""
    try:
        entries = []
        for name in os.listdir(ARTIFACT_DIR):
            if not name.endswith(".meta"):
                continue
            meta_p = os.path.join(ARTIFACT_DIR, name)
            try:
                st = os.stat(meta_p)
                key = name[:-5]
                art_p = _artifact_path(key)
                art_size = os.path.getsize(art_p) if os.path.exists(art_p) else 0
                entries.append((st.st_mtime, key, art_size, meta_p, art_p))
            except OSError:
                continue
        total = sum(e[2] for e in entries)
        if total <= MAX_ARTIFACT_BYTES:
            return
        entries.sort(key=lambda e: e[0])  # oldest first
        for _, key, art_size, meta_p, art_p in entries:
            if total <= MAX_ARTIFACT_BYTES:
                break
            for p in (art_p, meta_p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
            total -= art_size
            logger.info(f"[artifact_cache] evicted {key} ({art_size} bytes) for LRU")
    except OSError:
        pass


def clear_artifact_cache() -> int:
    """Remove all artifacts; returns the count removed (test helper)."""
    removed = 0
    try:
        for name in os.listdir(ARTIFACT_DIR):
            p = os.path.join(ARTIFACT_DIR, name)
            try:
                os.unlink(p)
                removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed


# ── V3 data foundation：孤儿/超龄清扫（audit #D-gap：data/artifacts 此前
# 只在写路径做字节上限 LRU —— .meta 缺失的 .tif 对 LRU 不可见（永久泄漏）、
# 崩溃遗留的 mkstemp 临时文件无人清扫、超龄条目永不老化。本函数由
# artifact_lifecycle.sweep_aged_artifacts 周期调用；只删自己目录族，
# 每个删除独立容错。〕

_KEY_RE = re.compile(r"^[0-9a-f]{16}$")


def _disk_retention_seconds() -> float:
    raw = os.environ.get("ARTIFACT_DISK_RETENTION_DAYS", "")
    try:
        days = float(raw)
    except (TypeError, ValueError):
        days = 30.0
    return max(days, 1.0) * 86400.0


def sweep_orphan_disk_artifacts(*, now: Optional[float] = None) -> dict:
    """清扫 data/artifacts 的三类孤儿 + 超龄条目（dry-run 诊断见返回值）。

    - ``temp_leftovers``：不匹配 ``<16hex>.tif/.meta`` 命名的一切文件
      （publish 崩溃遗留的 mkstemp 临时件）；
    - ``orphan_tif`` / ``orphan_meta``：配对缺失的半边（对 LRU 记账不可
      见 / 指向已消失的产物）；
    - ``aged``：mtime 超过 ARTIFACT_DISK_RETENTION_DAYS 的完整条目
      （写路径 LRU 只保字节上限，没有时间下限）。
    """
    result = {"temp_leftovers": 0, "orphan_tif": 0, "orphan_meta": 0, "aged": 0}
    current = time.time() if now is None else now
    cutoff = current - _disk_retention_seconds()
    try:
        names = os.listdir(ARTIFACT_DIR)
    except OSError:
        return result
    tif_keys: set = set()
    meta_keys: set = set()
    for name in names:
        stem, _, ext = name.rpartition(".")
        if ext == "tif" and _KEY_RE.match(stem):
            tif_keys.add(stem)
        elif ext == "meta" and _KEY_RE.match(stem):
            meta_keys.add(stem)
        else:
            # 非 <16hex>.tif/.meta 命名 = publish 临时件遗留
            p = os.path.join(ARTIFACT_DIR, name)
            try:
                if os.path.isfile(p):
                    os.unlink(p)
                    result["temp_leftovers"] += 1
            except OSError:
                continue
    for stem in tif_keys - meta_keys:
        try:
            os.unlink(_artifact_path(stem))
            result["orphan_tif"] += 1
        except OSError:
            continue
    for stem in meta_keys - tif_keys:
        try:
            os.unlink(_meta_path(stem))
            result["orphan_meta"] += 1
        except OSError:
            continue
    for stem in tif_keys & meta_keys:
        try:
            if os.stat(_meta_path(stem)).st_mtime < cutoff:
                for p in (_artifact_path(stem), _meta_path(stem)):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
                result["aged"] += 1
        except OSError:
            continue
    if any(result.values()):
        logger.info("[artifact_cache] orphan sweep: %s", result)
    return result
