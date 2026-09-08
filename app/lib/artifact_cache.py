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
import threading
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
    those. Uses ``st_mtime_ns`` (round-1 review MINOR): ``int(st.st_mtime)``
    truncates sub-second rewrites — two builds within the same second of a
    same-size source would collide on one cache key.
    """
    try:
        st = os.stat(source_path)
        return f"{source_path}|{st.st_mtime_ns}|{st.st_size}"
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

    # PERF MAJOR-3: the published size is known — bump the advisory counter
    # (the eviction check below full-scans only when the counter crosses cap).
    try:
        _bump_advisory_total(ARTIFACT_DIR, os.path.getsize(final))
    except OSError:
        _invalidate_advisory_total(ARTIFACT_DIR)

    _evict_if_needed()
    return final


# ── Chunk cache (Wave 6, audit 05 §7.3) ─────────────────────────────
#
# Per-chunk persistence for windowed execution: same chassis (atomic
# publish + meta sidecar + byte-cap LRU), a DEDICATED directory and a
# DEDICATED byte cap so chunk sweeps can never evict whole-artifact
# entries (and vice versa). Key derivation mirrors ``make_artifact_key``:
# sha256(source mtime+size identity, chunk descriptor canonical JSON,
# operation, ARTIFACT_VERSION_NS) truncated to 16 hex.
#
# Scope honesty: chunk entries are element-wise windowed outputs. They are
# only sound for ops the runtime has gate-checked as window_safe with no
# halo / global stat (see app/lib/geo_raster/chunk.ChunkCacheBackend).

CHUNK_DIR = os.path.join(ARTIFACT_DIR, "chunks")

#: Dedicated chunk-cache cap (default 1 GiB), env-overridable. Read per
#: call (not import time) so deployments/tests can retune without a
#: process restart.
CHUNK_CACHE_DEFAULT_BYTES = 1 * 1024 ** 3


def _chunk_cache_cap() -> int:
    raw = os.environ.get("WEBGIS_CHUNK_CACHE_BYTES", "")
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return CHUNK_CACHE_DEFAULT_BYTES


def make_chunk_cache_key(
    source_path: str,
    descriptor: dict,
    operation: str,
) -> str:
    """Content-addressed key for one chunk result.

    ``descriptor`` is the chunk descriptor's canonical projection (see
    ``RasterChunkDescriptor.canonical_json``); the source side uses the
    same mtime+size identity as ``make_artifact_key`` — one derivation
    discipline, no new scheme.
    """
    canonical = json.dumps(
        {
            "src": _source_identity(source_path),
            "op": operation,
            "params": descriptor,
            "ns": f"{ARTIFACT_VERSION_NS}|chunk-v1",
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _chunk_path(key: str) -> str:
    return os.path.join(CHUNK_DIR, f"{key}.npy")


def _chunk_meta_path(key: str) -> str:
    return os.path.join(CHUNK_DIR, f"{key}.meta")


def get_chunk(key: str) -> Optional[str]:
    """Return the cached chunk (.npy) path if present and still fresh.

    Same invalidation defense as :func:`get_artifact`: the recorded source
    identity is re-verified so an out-of-band same-mtime source rewrite
    degrades to a miss. Any meta corruption → miss (never raise).
    """
    path = _chunk_path(key)
    meta = _chunk_meta_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(meta, "r", encoding="utf-8") as f:
            recorded = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if recorded.get("src_identity") != _source_identity(recorded.get("src_path", "")):
        return None
    os.utime(meta, None)  # LRU recency bump
    return path


def publish_chunk(
    key: str,
    payload: "bytes | bytearray | str",
    *,
    source_path: str = "",
) -> Optional[str]:
    """Atomically publish one chunk payload; return the stored path.

    ``payload`` is raw .npy bytes or a path to copy from. Atomicity mirrors
    :func:`publish_artifact` (mkstemp → write → ``os.replace``): a crash
    leaves the previous entry (or none), never a half chunk. The meta
    sidecar carries the source identity for the fresh check in
    :func:`get_chunk`. Best-effort: callers treat failure as a future miss.
    """
    os.makedirs(CHUNK_DIR, exist_ok=True)
    final = _chunk_path(key)
    fd, tmp = tempfile.mkstemp(suffix=".npy", dir=CHUNK_DIR)
    try:
        os.close(fd)
        if isinstance(payload, (bytes, bytearray)):
            with open(tmp, "wb") as dst_f:
                dst_f.write(payload)
        else:
            with open(payload, "rb") as src_f, open(tmp, "wb") as dst_f:
                while True:
                    block = src_f.read(1024 * 1024)
                    if not block:
                        break
                    dst_f.write(block)
        os.replace(tmp, final)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        logger.warning(f"[artifact_cache] chunk publish failed for {key}", exc_info=True)
        return None
    try:
        with open(_chunk_meta_path(key), "w", encoding="utf-8") as f:
            json.dump({
                "key": key,
                "src_path": source_path,
                "src_identity": _source_identity(source_path) if source_path else "",
                "created_at": time.time(),
            }, f)
    except OSError:
        pass
    # PERF MAJOR-3: bump the advisory counter with the stored size (chunk
    # publishes are frequent small writes — never full-scan here unless the
    # counter crosses the dedicated cap).
    if isinstance(payload, (bytes, bytearray)):
        _bump_advisory_total(CHUNK_DIR, len(payload))
    else:
        try:
            _bump_advisory_total(CHUNK_DIR, os.path.getsize(final))
        except OSError:
            _invalidate_advisory_total(CHUNK_DIR)
    _evict_chunks_if_needed()
    return final


# ── Advisory running byte totals（round-1 review PERF MAJOR-3）───────────
#
# Per-cache-dir IN-PROCESS byte counters, replacing the "full directory stat
# scan on every publish" eviction precondition:
#   - lazily initialized from ONE full scan on the first publish per process;
#   - bumped by the published size on publish;
#   - reset to the scanned post-eviction total by the (rare) full eviction
#     scan, and invalidated (→ None) by every out-of-band mutator
#     (clear_*, sweep_orphan_*).
# Eviction therefore full-scans ONLY when the counter crosses the cap.
#
# Multi-process honesty: the counter is ADVISORY. Other processes'
# publishes/evictions are invisible here, so worst case is one extra scan
# or a slightly-late eviction (eviction still enforces the cap whenever the
# counter — always an under-estimate in that scenario — crosses it); the
# periodic orphan sweep independently reconciles the real directory state.
_ADVISORY_DIR_BYTES: dict = {}

#: round-2 review MINOR：get-or-scan / read-modify-write 各自是两条字节码，
#: 线程交错会丢更新（计数漂移 → 过早/过晚逐出）。模块级锁把 RMW 收敛为
#: 临界区（进程内精确；跨进程仍按上文的 advisory 语义兜底）。锁本身廉价
#: （无竞争路径一次 acquire/release），eviction 测试行为不变。
_ADVISORY_LOCK = threading.Lock()


def _scan_dir_total(dir_path: str, body_path_for: Callable[[str], str]) -> int:
    """One bounded-memory pass: total bytes of complete meta+body entries."""
    total = 0
    for name in os.listdir(dir_path):
        if not name.endswith(".meta"):
            continue
        body_p = body_path_for(name[:-5])
        try:
            if os.path.exists(body_p):
                total += os.path.getsize(body_p)
        except OSError:
            continue
    return total


def _advisory_total(dir_path: str, body_path_for: Callable[[str], str]) -> int:
    """Counter value, lazily initialized from one full scan (per process)."""
    with _ADVISORY_LOCK:
        total = _ADVISORY_DIR_BYTES.get(dir_path)
        if total is None:
            total = _scan_dir_total(dir_path, body_path_for)
            _ADVISORY_DIR_BYTES[dir_path] = total
        return total


def _bump_advisory_total(dir_path: str, delta: int) -> None:
    """Publish-side counter bump (no-op while unknown → next call lazily inits).

    round-2 review MINOR：RMW 在锁内完成 —— 并发 publish 不再丢更新。
    """
    with _ADVISORY_LOCK:
        current = _ADVISORY_DIR_BYTES.get(dir_path)
        if current is not None:
            _ADVISORY_DIR_BYTES[dir_path] = current + int(delta)


def _invalidate_advisory_total(dir_path: str) -> None:
    """Out-of-band mutation (sweep/clear): counter unknown until next publish."""
    with _ADVISORY_LOCK:
        _ADVISORY_DIR_BYTES[dir_path] = None


def _scan_and_evict(
    dir_path: str,
    cap: int,
    body_path_for: Callable[[str], str],
    *,
    heal_orphans: bool,
    label: str,
) -> int:
    """The (single) full LRU scan: evict oldest entries until under cap.

    Returns the post-eviction total (the new advisory counter value).
    ``heal_orphans=True`` (chunk dir) drops meta whose body vanished so
    accounting stays true; the artifact dir keeps them visible to LRU
    (historical behavior)."""
    entries = []
    for name in os.listdir(dir_path):
        if not name.endswith(".meta"):
            continue
        meta_p = os.path.join(dir_path, name)
        key = name[:-5]
        body_p = body_path_for(key)
        try:
            st = os.stat(meta_p)
            size = os.path.getsize(body_p) if os.path.exists(body_p) else 0
            if size == 0:
                # orphaned meta (body gone): drop so accounting stays true
                if heal_orphans:
                    os.unlink(meta_p)
                    continue
            entries.append((st.st_mtime, key, size, meta_p, body_p))
        except OSError:
            continue
    total = sum(e[2] for e in entries)
    if total > cap:
        entries.sort(key=lambda e: e[0])  # oldest first
        for _, key, size, meta_p, body_p in entries:
            if total <= cap:
                break
            for p in (body_p, meta_p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
            total -= size
            logger.info(
                f"[artifact_cache] evicted {label}{key} ({size} bytes) for LRU")
    return total


def _evict_chunks_if_needed() -> None:
    """LRU eviction under the DEDICATED chunk cap; also drops meta entries
    whose .npy has vanished (self-healing accounting).

    Round-1 review PERF MAJOR-3: a full directory scan runs ONLY when the
    advisory byte counter (see ``_ADVISORY_DIR_BYTES``) exceeds the cap —
    a burst of small-chunk publishes pays one scan (lazy init) instead of
    one scan per publish.
    """
    try:
        cap = _chunk_cache_cap()
        if _advisory_total(CHUNK_DIR, _chunk_path) <= cap:
            return
        _ADVISORY_DIR_BYTES[CHUNK_DIR] = _scan_and_evict(
            CHUNK_DIR, cap, _chunk_path,
            heal_orphans=True, label="chunk",
        )
    except OSError:
        pass


def clear_chunk_cache() -> int:
    """Remove all cached chunks; returns the count removed (test helper)."""
    _invalidate_advisory_total(CHUNK_DIR)
    removed = 0
    try:
        for name in os.listdir(CHUNK_DIR):
            p = os.path.join(CHUNK_DIR, name)
            try:
                if os.path.isfile(p):
                    os.unlink(p)
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed


def sweep_orphan_chunk_cache(*, now: Optional[float] = None) -> dict:
    """Orphan/aged sweep for the chunk directory (same policy family as
    :func:`sweep_orphan_disk_artifacts`, applied to ``.npy``/``.meta``)."""
    result = {"chunk_temp_leftovers": 0, "chunk_orphan_npy": 0,
              "chunk_orphan_meta": 0, "chunk_aged": 0}
    current = time.time() if now is None else now
    cutoff = current - _disk_retention_seconds()
    grace_cutoff = current - _sweep_grace_seconds()
    _invalidate_advisory_total(CHUNK_DIR)
    try:
        names = os.listdir(CHUNK_DIR)
    except OSError:
        return result
    npy_keys: set = set()
    meta_keys: set = set()
    for name in names:
        stem, _, ext = name.rpartition(".")
        if ext == "npy" and _KEY_RE.match(stem):
            npy_keys.add(stem)
        elif ext == "meta" and _KEY_RE.match(stem):
            meta_keys.add(stem)
        else:
            p = os.path.join(CHUNK_DIR, name)
            try:
                if os.path.isfile(p) and os.stat(p).st_mtime < grace_cutoff:
                    os.unlink(p)
                    result["chunk_temp_leftovers"] += 1
            except OSError:
                continue
    for stem in npy_keys - meta_keys:
        try:
            if os.stat(_chunk_path(stem)).st_mtime >= grace_cutoff:
                continue
            os.unlink(_chunk_path(stem))
            result["chunk_orphan_npy"] += 1
        except OSError:
            continue
    for stem in meta_keys - npy_keys:
        try:
            if os.stat(_chunk_meta_path(stem)).st_mtime >= grace_cutoff:
                continue
            os.unlink(_chunk_meta_path(stem))
            result["chunk_orphan_meta"] += 1
        except OSError:
            continue
    for stem in npy_keys & meta_keys:
        try:
            if os.stat(_chunk_meta_path(stem)).st_mtime < cutoff:
                for p in (_chunk_path(stem), _chunk_meta_path(stem)):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
                result["chunk_aged"] += 1
        except OSError:
            continue
    return result


def _evict_if_needed() -> None:
    """LRU eviction: if total bytes exceed the cap, remove oldest until under.

    Round-1 review PERF MAJOR-3: advisory-counter discipline (see
    ``_ADVISORY_DIR_BYTES``) — the full scan runs only when the running
    byte total exceeds ``MAX_ARTIFACT_BYTES``.
    """
    try:
        if _advisory_total(ARTIFACT_DIR, _artifact_path) <= MAX_ARTIFACT_BYTES:
            return
        _ADVISORY_DIR_BYTES[ARTIFACT_DIR] = _scan_and_evict(
            ARTIFACT_DIR, MAX_ARTIFACT_BYTES, _artifact_path,
            heal_orphans=False, label="",
        )
    except OSError:
        pass


def clear_artifact_cache() -> int:
    """Remove all artifacts; returns the count removed (test helper)."""
    _invalidate_advisory_total(ARTIFACT_DIR)
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


def _sweep_grace_seconds() -> float:
    raw = os.environ.get("ARTIFACT_SWEEP_GRACE_S", "")
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return 3600.0


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

    宽限期：temp/孤儿半边分支跳过 mtime 在 ``ARTIFACT_SWEEP_GRACE_S``
    （默认 1h）内的新文件 —— publish 在 ``os.replace`` 与 .meta 落盘
    之间存在毫秒级窗口，.meta 写失败也可能留下刚发布的合法 .tif；
    刚出生的文件绝不因「暂时配不上对」而被误删。
    """
    result = {"temp_leftovers": 0, "orphan_tif": 0, "orphan_meta": 0, "aged": 0}
    current = time.time() if now is None else now
    cutoff = current - _disk_retention_seconds()
    grace_cutoff = current - _sweep_grace_seconds()
    _invalidate_advisory_total(ARTIFACT_DIR)
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
            # 非 <16hex>.tif/.meta 命名 = publish 临时件遗留（宽限期内跳过）
            p = os.path.join(ARTIFACT_DIR, name)
            try:
                if os.path.isfile(p) and os.stat(p).st_mtime < grace_cutoff:
                    os.unlink(p)
                    result["temp_leftovers"] += 1
            except OSError:
                continue
    for stem in tif_keys - meta_keys:
        try:
            if os.stat(_artifact_path(stem)).st_mtime >= grace_cutoff:
                continue  # 新发布的 .tif：.meta 可能尚未落盘 —— 宽限
            os.unlink(_artifact_path(stem))
            result["orphan_tif"] += 1
        except OSError:
            continue
    for stem in meta_keys - tif_keys:
        try:
            if os.stat(_meta_path(stem)).st_mtime >= grace_cutoff:
                continue
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
    # Chunk directory: same discipline, dedicated namespace (Wave 6). Run
    # INSIDE the periodic sweep (its only caller) but keep the returned
    # dict shape pinned to the root keys — the contract is asserted
    # verbatim by tests/data/test_gc.py. Chunk keys are logged separately.
    try:
        chunk_result = sweep_orphan_chunk_cache(now=current)
        if any(chunk_result.values()):
            logger.info("[artifact_cache] chunk sweep: %s", chunk_result)
    except Exception:  # noqa: BLE001 — sweep is best-effort by contract
        logger.warning("[artifact_cache] chunk sweep failed", exc_info=True)
    return result
