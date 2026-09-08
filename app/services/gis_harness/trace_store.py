"""证据链持久化（V4 Wave 8 — ADR-0104 决策 9；V5 多 worker 安全化）。

V4 基线（审计 07）：TurnTrace / GisTraceChain 均为进程内 LRU，唯一
durable sink 是本模块的会话级 JSONL，但并发纪律是**进程内锁** ——
多 worker 部署下存在文档化的行交错丢失窗口（trace_store L56-59）。

V5 契约（ADR-0118 决策 D1）：
- **跨进程互斥**：``fcntl.flock`` 锁副作用文件（``<file>.lock``），
  线程锁 + 文件锁双层串行；非 POSIX 平台降级为进程锁（行为同 V4，
  差异在模块 docstring 诚实披露）。
- **单调 seq**：每会话记录带单调递增 ``seq``（锁内 last+1），评测/
  resume 侧可检测丢行与重复。
- **幂等**：``persist_turn_chain`` 按 ``(turn_id, total_records)``
  去重 —— settle 重试不产生重复行。
- **零丢行**：append/trim 全部在文件锁内完成；trim 保护含
  FINAL_VERDICT 的记录（关键证据不因滚动窗口丢失）。
- **防 LRU 驱逐丢链**：registry start 即 pin（有界 FIFO），settle 持
  久化成功后 unpin；即使 LRU 驱逐，链仍可从 pinned 区取到。

有界纪律：
- 每会话文件滚动保留最近 ``MAX_RECORDS_PER_SESSION`` 条（保护记录除外）；
- 单条载荷 = as_dict()（阶段桶 ≤8 条、payload ≤512 字符/键消毒）；
- GIS_TRACE_PERSIST=0 一键关停（默认开）；GIS_TRACE_FSYNC=1 逐条 fsync
  （默认关 —— 崩溃一致性由 append 原子性覆盖，fsync 留给强持久需求）；
- 任何失败静默 False —— 记录面绝不阻断业务路径。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_RECORDS_PER_SESSION = 64
_WRITE_LOCK = threading.Lock()

try:  # POSIX：跨进程文件锁可用
    import fcntl  # type: ignore

    _HAS_FCNTL = True
except ImportError:  # 非 POSIX：降级进程锁（诚实披露，V4 行为）
    fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False

# 永不被 trim 丢弃的记录（证据链关键事件）。
_PROTECTED_STAGE_NAMES = frozenset({"FINAL_VERDICT"})


def _enabled() -> bool:
    return os.getenv("GIS_TRACE_PERSIST", "1") not in ("0", "false", "False")


def _fsync_enabled() -> bool:
    return os.getenv("GIS_TRACE_FSYNC", "0") in ("1", "true", "True")


def _session_dir(session_id: str) -> Optional[Path]:
    """会话数据目录（与 mapspec store 同一 DATA_DIR 矩阵 —— #760）。"""
    if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
        return None
    try:
        from app.core.config import settings

        base = Path(os.environ.get("MAPSPEC_STORAGE_DIR") or Path(settings.DATA_DIR))
        d = base.resolve() / ".webgis-agent" / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:  # noqa: BLE001 — 目录不可得 → 不持久化
        return None


def _chains_path(session_id: str) -> Optional[Path]:
    d = _session_dir(session_id)
    return d / "trace_chains.jsonl" if d is not None else None


def _is_protected(record: Dict[str, Any]) -> bool:
    """FINAL_VERDICT 等关键证据永不 trim 丢弃。"""
    for stage in record.get("stages") or []:
        if isinstance(stage, dict) and stage.get("stage") in _PROTECTED_STAGE_NAMES:
            return True
    return bool(record.get("final"))


def _parse_lines(path: Path) -> List[Tuple[Optional[int], str, Dict[str, Any]]]:
    """读文件 → [(seq, raw_line, parsed_dict)]；损坏行 seq=None 原样保留。"""
    out: List[Tuple[Optional[int], str, Dict[str, Any]]] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for ln in raw.splitlines():
        if not ln.strip():
            continue
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            out.append((None, ln, {}))
            continue
        seq = rec.get("seq")
        out.append((int(seq) if isinstance(seq, int) else None, ln, rec))
    return out


class _FileLock:
    """线程锁 + （POSIX）flock 文件锁双层互斥；任何失败不抛出。"""

    def __init__(self, path: Path):
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        self._fd: Any = None

    def __enter__(self) -> "_FileLock":
        if _HAS_FCNTL:
            try:
                self._fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o644)
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            except OSError:
                if self._fd is not None:
                    os.close(self._fd)
                    self._fd = None
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def persist_chain(chain_dict: Dict[str, Any], session_id: str = "") -> bool:
    """append 一条已序列化的链（``chain.as_dict()``）到会话 JSONL。

    V5：文件锁内完成 读→（trim）→append；记录带单调 ``seq``；
    幂等键 ``(turn_id, total_records)`` 已存在 → 跳过重复写。
    """
    if not _enabled() or not isinstance(chain_dict, dict):
        return False
    sid = str(session_id or chain_dict.get("session_id") or "")
    path = _chains_path(sid)
    if path is None:
        return False
    try:
        with _WRITE_LOCK, _FileLock(path):
            parsed = _parse_lines(path)
            live = [(s, ln, rec) for s, ln, rec in parsed if s is not None]

            # 幂等：同 (turn_id, total_records) 已持久化 → 跳过。
            dup_key = (
                str(chain_dict.get("turn_id") or ""),
                chain_dict.get("total_records"),
            )
            if dup_key[0]:
                for _, _, rec in live:
                    if (
                        str(rec.get("turn_id") or "") == dup_key[0]
                        and rec.get("total_records") == dup_key[1]
                    ):
                        return True

            # 单调 seq：现存最大 seq + 1（含 V4 无 seq 历史 → 从 1 起）。
            max_seq = max((s for s, _, _ in live), default=0)
            chain_dict = dict(chain_dict)
            chain_dict["seq"] = max_seq + 1

            # Trim：超限先丢最旧非保护行；仍超限（保护记录占满）再丢最旧
            # 保护行 —— review R1 #5：窗口必须**无条件有界**（否则长会话
            # 全保护记录时文件无界增长，违背 bounded-everything 纪律）。
            # 原子重写（tmp + os.replace）：无锁读者不会读到撕裂文件
            # （review R1 #6 —— last_seq/read_chains 锁外读的前提）。
            total = len(parsed) + 1
            if total > MAX_RECORDS_PER_SESSION:
                overflow = total - MAX_RECORDS_PER_SESSION
                kept: List[str] = [ln for _, ln, _ in parsed]
                keep_flags = [not _is_protected(rec) for _, _, rec in parsed]
                # 先丢非保护（最旧优先），不够再丢保护（最旧优先）
                for pass_protected in (False, True):
                    for i in range(len(kept)):
                        if overflow <= 0:
                            break
                        if keep_flags[i] is not pass_protected:
                            continue
                        kept[i] = None
                        overflow -= 1
                    if overflow <= 0:
                        break
                kept = [ln for ln in kept if ln is not None]
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_text("".join(ln + "\n" for ln in kept),
                               encoding="utf-8")
                os.replace(tmp, path)

            line = json.dumps(chain_dict, ensure_ascii=False, sort_keys=False,
                              default=str)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                if _fsync_enabled():
                    f.flush()
                    os.fsync(f.fileno())
        return True
    except Exception:  # noqa: BLE001 — 记录面绝不阻断业务
        logger.debug("[TraceStore] persist_chain failed session=%s", sid,
                     exc_info=True)
        return False


def persist_turn_chain(turn_id: str, session_id: str = "") -> bool:
    """按 turn_id 取进程内链并持久化（turn 收尾调用）。

    V5：registry LRU 驱逐后仍可从 pinned 区取链（start 即 pin、持久化
    成功即 unpin）—— 修复 V4「高并发下链被驱逐 → settle 持久化 False
    → 链永久丢失」的窗口。
    """
    if not _enabled() or not turn_id:
        return False
    try:
        from app.lib.runtime.gis_trace import get_gis_trace_registry

        registry = get_gis_trace_registry()
        chain = registry.get(turn_id)
        if chain is None:
            return False
        payload = chain.as_dict()
        if session_id:
            payload["session_id"] = session_id
        ok = persist_chain(payload, session_id=session_id)
        if ok:
            registry.unpin(turn_id)
        return ok
    except Exception:  # noqa: BLE001
        return False


def read_chains(session_id: str) -> List[Dict[str, Any]]:
    """读取会话的全部持久化链（评测门离线消费；损坏行跳过）。"""
    path = _chains_path(session_id)
    if path is None or not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def last_seq(session_id: str) -> int:
    """会话当前最大 seq（无记录/文件缺失 = 0）——resume/丢行检测游标。"""
    path = _chains_path(session_id)
    if path is None or not path.exists():
        return 0
    max_seq = 0
    for seq, _, _ in _parse_lines(path):
        if seq is not None and seq > max_seq:
            max_seq = seq
    return max_seq


__all__ = ["persist_chain", "persist_turn_chain", "read_chains", "last_seq",
           "MAX_RECORDS_PER_SESSION", "_HAS_FCNTL"]
