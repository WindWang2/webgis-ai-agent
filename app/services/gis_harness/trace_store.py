"""证据链持久化（V4 Wave 8 — ADR-0104 决策 9）。

Phase-0 审计 07：TurnTrace / GisTraceChain 均为进程内 LRU，无任何序列
化路径 —— chain completeness 无法离线回归、replay 无法对历史 turn 做
A/B。本模块把已结束 turn 的链序列化为**会话级 JSONL**（一行一个
turn，``chain.as_dict()`` 有界载荷），与 provenance manifest 的
durable 记录互补（那覆盖 workflow run，本模块覆盖 chat turn）。

有界纪律：
- 每会话文件滚动保留最近 ``MAX_RECORDS_PER_SESSION`` 条（append 前裁剪）；
- 单条载荷 = as_dict()（阶段桶 ≤8 条、payload ≤512 字符/键消毒）；
- GIS_TRACE_PERSIST=0 一键关停（默认开）；
- 任何失败静默 False —— 记录面绝不阻断业务路径。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_RECORDS_PER_SESSION = 64
_WRITE_LOCK = threading.Lock()


def _enabled() -> bool:
    return os.getenv("GIS_TRACE_PERSIST", "1") not in ("0", "false", "False")


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


def persist_chain(chain_dict: Dict[str, Any], session_id: str = "") -> bool:
    """append 一条已序列化的链（``chain.as_dict()``）到会话 JSONL。

    session_id 缺省时从 chain_dict 取。滚动保留最近 N 条（读-裁-重写 +
    append，进程锁内原子性足够 —— 记录面允许极端并发下的行交错损失，
    绝不抛出）。
    """
    if not _enabled() or not isinstance(chain_dict, dict):
        return False
    sid = str(session_id or chain_dict.get("session_id") or "")
    path = _chains_path(sid)
    if path is None:
        return False
    try:
        line = json.dumps(chain_dict, ensure_ascii=False, sort_keys=False,
                          default=str)
        with _WRITE_LOCK:
            lines: List[str] = []
            if path.exists():
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    lines = []
            lines = [ln for ln in lines if ln.strip()]
            if len(lines) >= MAX_RECORDS_PER_SESSION:
                lines = lines[-(MAX_RECORDS_PER_SESSION - 1):]
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except Exception:  # noqa: BLE001 — 记录面绝不阻断业务
        logger.debug("[TraceStore] persist_chain failed session=%s", sid,
                     exc_info=True)
        return False


def persist_turn_chain(turn_id: str, session_id: str = "") -> bool:
    """按 turn_id 取进程内链并持久化（turn 收尾调用；链缺席 = False）。"""
    if not _enabled() or not turn_id:
        return False
    try:
        from app.lib.runtime.gis_trace import get_gis_trace_registry

        chain = get_gis_trace_registry().get(turn_id)
        if chain is None:
            return False
        payload = chain.as_dict()
        if session_id:
            payload["session_id"] = session_id
        return persist_chain(payload, session_id=session_id)
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


__all__ = ["persist_chain", "persist_turn_chain", "read_chains",
           "MAX_RECORDS_PER_SESSION"]
