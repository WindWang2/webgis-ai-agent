"""Embedding cache —— (model, asset, grid, window) 键控的有界特征缓存。

Platform 11 / WP-D（ADR-0198 §后续）。与 :class:`ReuseStore` 的分工：
reuse 缓存**整 run 产物**（InferenceFingerprint 精确匹配）；本缓存键控
**单 tile/window 的 embedding 向量**，支持部分失效（模型升级 / 资产内容
变更）与 resume（跨 run 逐窗命中即跳过 provider infer）。

纪律（全部可静态审计）：
- 键 = sha256(canonical JSON)：model fingerprint payload、provider
  ``semantic_version``、asset 内容 sha、preprocess payload、grid 身份
  （w/h/crs/transform 六元组）、window (row,col,h,w)、owner scope、
  条目语义版本——同键必同语义；
- 条目 = ``<key>.npy``（``allow_pickle=False`` 读写，不 mmap）+
  ``<key>.json`` sidecar（shape/dtype/digest/model_id/model_fp/asset_sha/
  last_used）。**sidecar 是提交标记**：先原子落 .npy 再落 sidecar，get
  无 sidecar 即 miss（半写条目不可见）；
- 完整性：get 时对 .npy 重新流式 sha256，失配 → 驱逐 + miss（篡改/截断
  不产出污染向量）；
- 有界：entries/bytes 双上界 LRU 驱逐；单条目字节上限（超限拒绝存储）；
- 原子：tmp + ``os.replace``；任何失败路径清理 tmp 残件；
- owner 隔离：条目按 owner scope 目录存放（与 reuse store 同白名单规则），
  跨 owner 永不可见；
- 确定性门：只服务 ``random_seed_policy ∈ {deterministic, fixed_seed}``
  的模型（引擎侧把关；非确定性模型不查不存）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

EMBED_CACHE_ENTRY_VERSION = "modelops.embed-cache-entry/v1"

_SCOPE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


def build_embed_cache_key(
    *,
    model_digest: str,
    provider_semantic_version: str,
    asset_sha256: str,
    preprocess_digest: str,
    grid: Dict[str, Any],
    window: Tuple[int, int, int, int],
    owner_scope: Dict[str, str],
) -> str:
    """canonical 键（同键 ⇔ 同模型语义 × 同资产内容 × 同网格 × 同窗口 × 同 owner）。

    ``model_digest``/``preprocess_digest`` 是调用方对 canonical payload 的
    sha256（每 run 一次；避免逐窗重复序列化重量级 descriptor payload）。
    """
    payload = {
        "entry_version": EMBED_CACHE_ENTRY_VERSION,
        "model_digest": model_digest,
        "provider_semantic_version": provider_semantic_version,
        "asset_sha256": asset_sha256,
        "preprocess_digest": preprocess_digest,
        "grid": {
            "width": int(grid["width"]),
            "height": int(grid["height"]),
            "crs": grid.get("crs"),
            "transform": [float(v) for v in grid["transform"]],
        },
        "window": [int(v) for v in window],
        "owner_scope": dict(sorted(owner_scope.items())),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EmbeddingCache:
    """有界磁盘 embedding 缓存（进程锁；条目原子发布）。"""

    def __init__(
        self,
        root: Path,
        *,
        max_entries: int = 256,
        max_bytes: int = 1024**3,
        max_entry_bytes: int = 64 * 1024**2,
        clock=time.time,
    ) -> None:
        self._root = Path(root)
        self._max_entries = max(1, max_entries)
        self._max_bytes = max(1, max_bytes)
        self._max_entry_bytes = max(1, max_entry_bytes)
        self._clock = clock
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._digest_failures = 0
        self._index: Dict[str, Dict[str, Any]] = {}
        self._bytes = 0
        self._root.mkdir(parents=True, exist_ok=True)
        self._load_index()

    # ── 索引 ────────────────────────────────────────────────────────
    def _load_index(self) -> None:
        """启动扫描（≤ 上界条目；损坏/孤儿条目直接清除）。"""
        scanned = 0
        for sidecar in self._root.rglob("*.json"):
            if scanned >= self._max_entries * 2:
                break
            scanned += 1
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                sidecar.unlink(missing_ok=True)
                continue
            if meta.get("entry_version") != EMBED_CACHE_ENTRY_VERSION:
                sidecar.unlink(missing_ok=True)
                continue
            npy = sidecar.with_suffix(".npy")
            if not npy.exists():
                sidecar.unlink(missing_ok=True)
                continue
            key = meta.get("key")
            if not key or len(key) != 64:
                continue
            self._index[key] = {
                "npy": npy, "sidecar": sidecar, "meta": meta,
                "bytes": int(npy.stat().st_size),
            }
            self._bytes += int(npy.stat().st_size)
        # 第二遍：孤儿 .npy（sidecar 缺失 = 未提交/残骸）清除。
        for orphan in self._root.rglob("*.npy"):
            if not orphan.with_suffix(".json").exists():
                try:
                    orphan.unlink()
                except OSError:
                    pass
        self._enforce_bounds()

    # ── 路径 ────────────────────────────────────────────────────────
    def _owner_dir(self, owner_scope: Dict[str, str]) -> Path:
        from app.lib.modelops.errors import ModelOpsError

        for key, value in owner_scope.items():
            if not isinstance(value, str) or not self._SCOPE_RE_MATCH(value):
                raise ModelOpsError(f"invalid owner scope value for {key!r}")
        scope_key = "_".join(f"{k}-{v}" for k, v in sorted(owner_scope.items()))
        return self._root / scope_key

    @staticmethod
    def _SCOPE_RE_MATCH(value: str) -> bool:
        return bool(_SCOPE_VALUE_RE.match(value))

    def _entry_paths(self, owner_scope: Dict[str, str], key: str) -> Tuple[Path, Path]:
        entry_dir = self._owner_dir(owner_scope) / key[:2]
        return entry_dir / f"{key}.npy", entry_dir / f"{key}.json"

    # ── API ─────────────────────────────────────────────────────────
    def get(self, key: str, *, owner_scope: Dict[str, str]) -> Optional[np.ndarray]:
        """命中 → 向量副本；miss/损坏/失配 → None（损坏条目驱逐）。"""
        with self._lock:
            entry = self._index.get(key)
            npy, sidecar = self._entry_paths(owner_scope, key)
            if entry is None or not sidecar.exists():
                self._misses += 1
                return None
            try:
                array = np.load(npy, allow_pickle=False)
            except Exception:  # noqa: BLE001 — 损坏条目按 miss 处理并驱逐
                self._evict_key(key)
                self._misses += 1
                return None
            meta = entry["meta"]
            if _file_sha256(npy) != meta.get("digest"):
                self._digest_failures += 1
                self._evict_key(key)
                self._misses += 1
                return None
            if tuple(array.shape) != tuple(meta.get("shape", ())) or str(
                array.dtype
            ) != meta.get("dtype"):
                self._evict_key(key)
                self._misses += 1
                return None
            meta["last_used"] = self._clock()
            try:
                sidecar.write_text(
                    json.dumps(meta, sort_keys=True, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError:
                pass  # LRU 时间戳更新失败不影响正确性
            self._hits += 1
            return array

    def put(
        self,
        key: str,
        array: np.ndarray,
        *,
        owner_scope: Dict[str, str],
        model_id: str = "",
        model_fp: str = "",
        asset_sha: str = "",
    ) -> bool:
        """存储（原子）；超单条目上限/IO 失败 → False（不入缓存）。"""
        data = np.ascontiguousarray(array)
        if data.nbytes > self._max_entry_bytes:
            logger.debug(
                "embed cache entry %s.. exceeds per-entry cap (%d > %d)",
                key[:12], data.nbytes, self._max_entry_bytes,
            )
            return False
        with self._lock:
            if len(self._index) >= self._max_entries and key not in self._index:
                self._evict_lru(until_slots=1)
            npy, sidecar = self._entry_paths(owner_scope, key)
            npy.parent.mkdir(parents=True, exist_ok=True)
            tmp_npy = npy.with_suffix(f".npy.tmp{uuid.uuid4().hex[:8]}")
            tmp_meta = sidecar.with_suffix(f".json.tmp{uuid.uuid4().hex[:8]}")
            try:
                with open(tmp_npy, "wb") as fh:
                    np.save(fh, data, allow_pickle=False)
                    fh.flush()
                    os.fsync(fh.fileno())
                meta = {
                    "entry_version": EMBED_CACHE_ENTRY_VERSION,
                    "key": key,
                    "shape": list(data.shape),
                    "dtype": str(data.dtype),
                    "digest": _file_sha256(tmp_npy),
                    "model_id": model_id,
                    "model_fp": model_fp,
                    "asset_sha": asset_sha,
                    "created": self._clock(),
                    "last_used": self._clock(),
                }
                tmp_meta.write_text(
                    json.dumps(meta, sort_keys=True, ensure_ascii=False),
                    encoding="utf-8",
                )
                # 提交序：.npy 先入位，sidecar 最后入位（存在性 = 提交标记）。
                os.replace(tmp_npy, npy)
                os.replace(tmp_meta, sidecar)
            except OSError as exc:
                logger.warning("embed cache put failed for %s..: %s", key[:12], exc)
                for tmp in (tmp_npy, tmp_meta):
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
                return False
            old = self._index.pop(key, None)
            if old is not None:
                self._bytes -= int(old["bytes"])
                self._remove_files_quietly(old)
            self._index[key] = {
                "npy": npy, "sidecar": sidecar, "meta": meta,
                "bytes": int(npy.stat().st_size),
            }
            self._bytes += int(npy.stat().st_size)
            self._enforce_bounds()
            return True

    def invalidate(
        self,
        *,
        model_fp: Optional[str] = None,
        asset_sha: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> int:
        """部分失效（模型升级 / 资产变更）；返回清除条数。"""
        with self._lock:
            victims = []
            for key, entry in self._index.items():
                meta = entry["meta"]
                if model_fp is not None and meta.get("model_fp") == model_fp:
                    victims.append(key)
                elif asset_sha is not None and meta.get("asset_sha") == asset_sha:
                    victims.append(key)
                elif model_id is not None and meta.get("model_id") == model_id:
                    victims.append(key)
            for key in victims:
                self._evict_key(key)
            return len(victims)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._index),
                "bytes": self._bytes,
                "max_entries": self._max_entries,
                "max_bytes": self._max_bytes,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "digest_failures": self._digest_failures,
            }

    def clear(self) -> None:
        """清空（测试/运维面）。"""
        with self._lock:
            for key in list(self._index):
                self._evict_key(key)

    # ── 内部 ────────────────────────────────────────────────────────
    def _evict_key(self, key: str) -> None:
        entry = self._index.pop(key, None)
        if entry is None:
            return
        self._bytes -= int(entry["bytes"])
        self._evictions += 1
        self._remove_files_quietly(entry)

    def _remove_files_quietly(self, entry: Dict[str, Any]) -> None:
        # Windows：np.load 已整读关闭句柄；unlink 前无需额外 unmap。
        for path_key in ("npy", "sidecar"):
            try:
                entry[path_key].unlink(missing_ok=True)
            except OSError:
                logger.debug("embed cache file remove failed: %s", entry[path_key])
        try:
            entry["npy"].parent.rmdir()
        except OSError:
            pass  # 目录非空（同前缀邻居）或已删

    def _evict_lru(self, *, until_slots: int = 0) -> None:
        allowed = max(0, self._max_entries - until_slots)
        if len(self._index) <= allowed:
            return
        ordered = sorted(
            self._index.items(),
            key=lambda kv: kv[1]["meta"].get("last_used", 0.0),
        )
        for key, _entry in ordered[: len(self._index) - allowed]:
            self._evict_key(key)

    def _enforce_bounds(self) -> None:
        if len(self._index) > self._max_entries:
            self._evict_lru()
        while self._bytes > self._max_bytes and self._index:
            oldest = min(
                self._index.items(),
                key=lambda kv: kv[1]["meta"].get("last_used", 0.0),
            )
            self._evict_key(oldest[0])


__all__ = ["EmbeddingCache", "build_embed_cache_key", "EMBED_CACHE_ENTRY_VERSION"]
