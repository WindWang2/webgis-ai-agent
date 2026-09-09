"""Durable Recovery / Remediation Ledger（ADR-0119 决策 D5）。

V5 基线（`.agent-work/harness-v6/00-baseline.md` G3）：
``failure_taxonomy.RemediationLedger`` 是**进程级内存**账本（LRU≤512 +
TTL 1h，诚实披露口径）。两个结构性缺口：
- **跨 worker 不一致**：多 worker 部署下同 (session, tool, class) 的
  失败计数分散在各自进程，预算永不耗尽 → 重复无限重试；
- **重启归零**：worker 重启后预算复活，恢复后的会话可以再吃满一轮
  重试预算。

V6 契约：
- **durable**：(session, tool, failure_class) → attempts 持久化到
  session-plane JSON（``trace_store`` 同款 DATA_DIR 矩阵 + flock 跨进程
  互斥），write-through —— 每次 record 即落盘，崩溃/重启/换 worker 预算
  不丢。
- **成功回写**：``record_success`` 把该 (session, tool) 的全部 class
  计数清零 —— dispatch 成功路径显式调用；「工具修好了」必须反映到预算。
- **TTL 惰性衰减**：读取时按 last_ts 与 TTL 比较，过期条目按 0 计并清
  除（时间上远离的旧账不渗漏 —— V5 R1 #7 语义的 durable 版）。
- **有界**：每 session ≤ MAX_ENTRIES 条（超出 LRU 淘汰最旧）。
- **resume 续接**：``copy_between_sessions`` 把旧 session 的账本（有界
  截取）拷进 resume 新 session —— 恢复后不重新获得完整重试预算。
- **与进程级账本的关系**：durable 为 authority（有 session_id 时）；
  进程级 ``RemediationLedger`` 保留给无 session 上下文的调用方（
  tool_metrics 聚合器口径不变）。二者不重复记账。
- 任何入口**绝不抛出**：记录面不阻断业务。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: 账本条目 TTL（秒）：与 failure_taxonomy.LEDGER_TTL_S 同值 —— 时间上
#: 远离的旧失败不渗漏到未来 turn（V5 R1 #7 durable 版）。
LEDGER_TTL_S = 3600.0
#: 每 session 条目上限（bounded everything）。
MAX_ENTRIES_PER_SESSION = 256
#: resume 拷贝上限（恢复面有界；超出按最近写入时间截取）。
RESUME_COPY_MAX = 64
_LEDGER_SCHEMA_VERSION = 1


def _enabled() -> bool:
    return os.getenv("GIS_RECOVERY_LEDGER", "1") not in ("0", "false", "False")


def _session_dir(session_id: str) -> Optional[Path]:
    """会话数据目录（与 trace_store/mapspec 同一 DATA_DIR 矩阵）。"""
    if not session_id or not all(
        c.isalnum() or c in "-_" for c in session_id
    ):
        return None
    try:
        from app.core.config import settings

        base = Path(os.environ.get("MAPSPEC_STORAGE_DIR") or Path(settings.DATA_DIR))
        d = base.resolve() / ".webgis-agent" / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:  # noqa: BLE001 — 目录不可得 → 降级进程内（诚实降级）
        return None


def _ledger_path(session_id: str) -> Optional[Path]:
    d = _session_dir(session_id)
    return d / "recovery_ledger.json" if d is not None else None


def _key(tool: str, failure_class: str) -> str:
    return f"{tool}||{failure_class}"


class _FileLock:
    """flock 文件锁（trace_store._FileLock 同款；非 POSIX 降级为无锁 ——
    进程内锁已保证线程安全，跨进程互斥在非 POSIX 上文档化缺席）。"""

    def __init__(self, path: Path):
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        self._fd: Any = None

    def __enter__(self) -> "_FileLock":
        try:
            import fcntl

            self._fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        except ImportError:
            self._fd = None
        except OSError:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._fd is not None:
            try:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except Exception:  # noqa: BLE001
                pass
            finally:
                os.close(self._fd)
                self._fd = None


class RecoveryLedger:
    """(session, tool, failure_class) → attempts 的 durable 账本。

    - 读：``_load``（flock 下整读 JSON，TTL 惰性清除）；
    - 写：``_mutate``（flock 下 读→改→原子写 os.replace）；
    - 进程内读缓存按文件 mtime 失效（跨 worker 写可见）。
    """

    def __init__(self, ttl_s: float = LEDGER_TTL_S,
                 cap: int = MAX_ENTRIES_PER_SESSION):
        self._ttl = ttl_s
        self._cap = cap
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._thread_lock = threading.Lock()

    # -- 存储层 ----------------------------------------------------------
    def _load_locked(self, session_id: str) -> Dict[str, Any]:
        path = _ledger_path(session_id)
        if path is None or not path.exists():
            return {"version": _LEDGER_SCHEMA_VERSION, "entries": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(
                data.get("entries"), dict
            ):
                return {"version": _LEDGER_SCHEMA_VERSION, "entries": {}}
            return data
        except Exception:  # noqa: BLE001 — 损坏账本按空起（不阻断业务）
            logger.debug("[RecoveryLedger] corrupt ledger sid=%s",
                         session_id, exc_info=True)
            return {"version": _LEDGER_SCHEMA_VERSION, "entries": {}}

    def _save_locked(self, session_id: str, data: Dict[str, Any]) -> bool:
        path = _ledger_path(session_id)
        if path is None:
            return False
        try:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(tmp, path)
            return True
        except Exception:  # noqa: BLE001
            logger.debug("[RecoveryLedger] persist failed sid=%s",
                         session_id, exc_info=True)
            return False

    def _mutate(self, session_id: str, fn) -> Optional[Dict[str, Any]]:
        """flock 下 读→fn(entries)->entries'（None=不变）→原子写。"""
        if not session_id or not _enabled():
            return None
        path = _ledger_path(session_id)
        if path is None:
            return None
        with self._thread_lock, _FileLock(path):
            data = self._load_locked(session_id)
            entries = data.get("entries") or {}
            new_entries = fn(entries)
            if new_entries is None:
                return entries
            data["entries"] = new_entries
            self._save_locked(session_id, data)
            try:
                self._cache.pop(session_id, None)
            except Exception:  # noqa: BLE001
                pass
            return new_entries

    def _read(self, session_id: str) -> Dict[str, Any]:
        if not session_id or not _enabled():
            return {}
        path = _ledger_path(session_id)
        if path is None:
            return {}
        with self._thread_lock:
            try:
                mtime = path.stat().st_mtime_ns
            except OSError:
                mtime = None
            cached = self._cache.get(session_id)
            if cached is not None and mtime is not None and cached[0] == mtime:
                # TTL 惰性衰减按**每次读取**时钟判定 —— 缓存只免重复解析，
                # 不免衰减（时间流逝本身让条目过期）。
                return self._decay(cached[1])
            with _FileLock(path):
                data = self._load_locked(session_id)
            entries = self._decay(data.get("entries") or {})
            if mtime is not None:
                self._cache[session_id] = (mtime, entries)
            return entries

    # -- 语义层 ----------------------------------------------------------
    def _decay(self, entries: Dict[str, Any]) -> Dict[str, Any]:
        """TTL 惰性衰减：过期条目剔除（读取面；写入面自然覆盖）。"""
        now = time.time()
        out: Dict[str, Any] = {}
        for k, v in entries.items():
            try:
                if now - float(v.get("ts") or 0) <= self._ttl:
                    out[k] = v
            except (TypeError, ValueError, AttributeError):
                continue
        return out

    def record_failure(self, session_id: str, tool: str,
                       failure_class: str) -> int:
        """失败记账 +1 → 返回当前 attempts（预算裁决输入）。"""
        tool_k = (tool or "")[:128]
        class_k = (failure_class or "")[:48]
        if not tool_k or not class_k:
            return 0
        now = time.time()

        def _fn(entries: Dict[str, Any]) -> Dict[str, Any]:
            entry = entries.get(_key(tool_k, class_k))
            if entry is None:
                entries[_key(tool_k, class_k)] = {"attempts": 1, "ts": now}
            else:
                try:
                    expired = now - float(entry.get("ts") or 0) > self._ttl
                except (TypeError, ValueError):
                    expired = True
                entry["attempts"] = 1 if expired else int(
                    entry.get("attempts") or 0) + 1
                entry["ts"] = now
            # LRU 有界：超限按 ts 淘汰最旧
            while len(entries) > self._cap:
                oldest = min(entries, key=lambda k: entries[k].get("ts") or 0)
                entries.pop(oldest)
            return entries

        entries = self._mutate(session_id, _fn)
        if entries is None:
            return 0
        entry = entries.get(_key(tool_k, class_k))
        return int(entry.get("attempts") or 0) if entry else 0

    def record_success(self, session_id: str, tool: str) -> int:
        """成功回写：清零该 (session, tool) 全部 class 计数。

        返回被清除的条目数（0 = 本来就干净）。dispatch 成功路径调用 ——
        「工具修好了」必须反映到预算，否则历史失败永久压制重试预算。
        """
        tool_k = (tool or "")[:128]
        if not tool_k:
            return 0
        prefix = f"{tool_k}||"
        removed = {"n": 0}

        def _fn(entries: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            doomed = [k for k in entries if k.startswith(prefix)]
            for k in doomed:
                entries.pop(k)
            removed["n"] = len(doomed)
            return entries if doomed else None  # None=无变化（不落盘）

        self._mutate(session_id, _fn)
        return removed["n"]

    def attempts(self, session_id: str, tool: str,
                 failure_class: str) -> int:
        entries = self._read(session_id)
        entry = entries.get(_key((tool or "")[:128], (failure_class or "")[:48]))
        return int(entry.get("attempts") or 0) if entry else 0

    def reset(self, session_id: str) -> None:
        """清空 session 账本（测试/显式运维入口）。"""
        self._mutate(
            session_id,
            lambda entries: {} if entries else None,
        )
        with self._thread_lock:
            self._cache.pop(session_id, None)

    def snapshot(self, session_id: str, *, max_entries: int = 32) -> Dict[str, Any]:
        """有界快照（诊断/披露面；不进 LLM context）。"""
        entries = self._read(session_id)
        items = sorted(
            entries.items(), key=lambda kv: -(kv[1].get("ts") or 0),
        )[:max_entries]
        return {
            k: {"attempts": int(v.get("attempts") or 0),
                "age_s": round(max(0.0, time.time() - float(v.get("ts") or 0)), 1)}
            for k, v in items
        }

    def copy_between_sessions(
        self, old_session_id: str, new_session_id: str, *,
        max_entries: int = RESUME_COPY_MAX,
    ) -> int:
        """resume 预算续接：旧 session 账本（最近 max_entries 条）拷入新
        session —— 恢复后**不**重新获得完整重试预算（防恢复后无限重试）。
        返回拷贝条数。"""
        source = self._read(old_session_id)
        if not source:
            return 0
        items = sorted(
            source.items(), key=lambda kv: -(kv[1].get("ts") or 0),
        )[:max(max_entries, 0)]
        if not items:
            return 0

        def _fn(entries: Dict[str, Any]) -> Dict[str, Any]:
            for k, v in items:
                existing = entries.get(k)
                if existing is None or (existing.get("ts") or 0) < (
                    v.get("ts") or 0
                ):
                    entries[k] = dict(v)
            while len(entries) > self._cap:
                oldest = min(entries, key=lambda k: entries[k].get("ts") or 0)
                entries.pop(oldest)
            return entries

        self._mutate(new_session_id, _fn)
        return len(items)


_global_ledger = RecoveryLedger()


def get_recovery_ledger() -> RecoveryLedger:
    return _global_ledger


def reset_recovery_ledger_for_tests() -> None:
    global _global_ledger
    _global_ledger = RecoveryLedger()


__all__ = [
    "RecoveryLedger",
    "get_recovery_ledger",
    "reset_recovery_ledger_for_tests",
    "LEDGER_TTL_S",
    "MAX_ENTRIES_PER_SESSION",
    "RESUME_COPY_MAX",
]
