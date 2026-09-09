"""Inference reuse cache —— fingerprint 精确匹配的结果复用（ADR-0119 §3.6；Epic §M）。

只有 InferenceFingerprint 全字段精确匹配才复用（模型 checksum/version、
provider 身份/语义、输入内容身份、预处理、tile plan、thresholds、
postprocess、输出 schema、owner policy）。owner 隔离：条目按 owner
scope 目录存放，跨 owner 永不可见（含存在性）。

条目内容：manifest + 产物文件副本（reuse 目录自管，LRU+TTL+bytes 上界）。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

REUSE_ENTRY_VERSION = "modelops.reuse-entry/v1"


class ReuseStore:
    """有界磁盘 reuse 缓存（进程锁；并发由原子目录发布保证）。"""

    def __init__(
        self,
        root: Path,
        *,
        max_entries: int = 128,
        max_bytes: int = 2 * 1024**3,
        ttl_s: float = 7 * 86400.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._root = root
        self._max_entries = max(1, max_entries)
        self._max_bytes = max(1, max_bytes)
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.RLock()

    # ── 路径 ────────────────────────────────────────────────────────
    def _owner_dir(self, owner_scope: Dict[str, str]) -> Path:
        scope_key = "_".join(f"{k}-{v}" for k, v in sorted(owner_scope.items()))
        return self._root / scope_key

    def _entry_dir(self, owner_scope: Dict[str, str], key: str) -> Path:
        return self._owner_dir(owner_scope) / key[:2] / key

    # ── API ─────────────────────────────────────────────────────────
    def lookup(self, key: str, *, owner_scope: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """精确匹配查找（跨 owner 永不命中；过期/损坏条目视为 miss）。"""
        entry_dir = self._entry_dir(owner_scope, key)
        meta_path = entry_dir / "entry.json"
        if not meta_path.exists():
            return None
        try:
            entry = json.loads(meta_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        if entry.get("entry_version") != REUSE_ENTRY_VERSION:
            return None
        if self._clock() - float(entry.get("created_at", 0)) > self._ttl_s:
            return None
        artifacts = entry.get("artifacts") or []
        for art in artifacts:
            if not (entry_dir / str(art.get("file", ""))).exists():
                return None
        # touch（LRU）
        with self._lock:
            try:
                meta_path.touch()
            except OSError:
                pass
        entry["entry_dir"] = str(entry_dir)
        return entry

    def store(
        self,
        key: str,
        *,
        owner_scope: Dict[str, str],
        manifest: Dict[str, Any],
        artifacts: List[Dict[str, Any]],
    ) -> bool:
        """发布 reuse 条目（产物文件复制进自管目录；原子目录替换）。

        ``artifacts`` 成员：``{"role", "file"(绝对路径), "data_object_id"?}``。
        manifest/model 不符时调用方不会走到这里（key 不匹配即 miss）。
        """
        if any(a.get("file") is None for a in artifacts):
            return False
        entry_dir = self._entry_dir(owner_scope, key)
        staging = entry_dir.with_name(entry_dir.name + ".tmp")
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        stored_artifacts: List[Dict[str, Any]] = []
        total = 0
        for art in artifacts:
            src = Path(str(art["file"]))
            if not src.exists():
                shutil.rmtree(staging, ignore_errors=True)
                return False
            dst = staging / src.name
            shutil.copy2(src, dst)
            size = dst.stat().st_size
            total += size
            stored = {k: v for k, v in art.items() if k != "file"}
            stored["file"] = src.name
            stored["size"] = size
            stored_artifacts.append(stored)
        entry = {
            "entry_version": REUSE_ENTRY_VERSION,
            "key": key,
            "created_at": self._clock(),
            "manifest": manifest,
            "artifacts": stored_artifacts,
            "total_bytes": total,
        }
        (staging / "entry.json").write_text(
            json.dumps(entry, ensure_ascii=False), encoding="utf-8"
        )
        with self._lock:
            if entry_dir.exists():
                shutil.rmtree(entry_dir, ignore_errors=True)
            os.replace(staging, entry_dir)
            self._evict()
        return True

    def invalidate(self, key: str, *, owner_scope: Dict[str, str]) -> bool:
        entry_dir = self._entry_dir(owner_scope, key)
        if entry_dir.exists():
            shutil.rmtree(entry_dir, ignore_errors=True)
            return True
        return False

    def stats(self, *, owner_scope: Dict[str, str]) -> Dict[str, Any]:
        owner_dir = self._owner_dir(owner_scope)
        entries = 0
        total = 0
        if owner_dir.exists():
            for entry_path in owner_dir.glob("*/*/entry.json"):
                entries += 1
                try:
                    entry = json.loads(entry_path.read_text(encoding="utf-8"))
                    total += int(entry.get("total_bytes", 0))
                except (ValueError, OSError):
                    continue
        return {"entries": entries, "bytes": total}

    # ── internal ────────────────────────────────────────────────────
    def _evict(self) -> None:
        """LRU+TTL 驱逐（锁内调用；全局视角——超界先删最旧条目）。"""
        now = self._clock()
        candidates: list = []
        for entry_path in self._root.glob("*/*/entry.json"):
            try:
                entry = json.loads(entry_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            created = float(entry.get("created_at", 0))
            if now - created > self._ttl_s:
                shutil.rmtree(entry_path.parent, ignore_errors=True)
                continue
            candidates.append(
                (entry_path.stat().st_mtime, int(entry.get("total_bytes", 0)), entry_path.parent)
            )
        candidates.sort(reverse=True)  # 新的在前
        kept = 0
        kept_bytes = 0
        for _mtime, size, entry_dir in candidates:
            kept += 1
            kept_bytes += size
            if kept > self._max_entries or kept_bytes > self._max_bytes:
                shutil.rmtree(entry_dir, ignore_errors=True)
