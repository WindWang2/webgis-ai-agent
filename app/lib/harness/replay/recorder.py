"""生产录制器（B2，ADR-0183 决策二）：S13 settle 缝的 env-gated 打包。

- **总闸**：``HARNESS_REPLAY_RECORD``（默认关 —— 零生产开销语义：关闸时
  只做一次 env 读）；
- **输出**：``HARNESS_REPLAY_DIR``（缺省 ``<DATA_DIR>/replay-recordings``）
  下 ``<session_id>/<turn_id>.json``，tmp+os.replace 原子写；
- **有界**：单 session 保留最近 ``MAX_TRACES_PER_SESSION`` 条（64），
  溢出按 mtime 淘汰最旧；
- **绝不阻断主链路**：任何异常自吞（唯一入口 :func:`maybe_record_turn`
  合约为 never-raises），失败仅 debug 日志；
- **只读收集**：chain（GisTraceRegistry）+ TurnEvidence（TURN_EVIDENCE）
  + settle 透传的 finalization 载荷 —— 不触碰 dispatch / chat 上下文。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from app.lib.harness.replay.schema import REPLAY_TRACE_SCHEMA_VERSION, build_trace

logger = logging.getLogger(__name__)

MAX_TRACES_PER_SESSION = 64

_ENV_RECORD = "HARNESS_REPLAY_RECORD"
_ENV_DIR = "HARNESS_REPLAY_DIR"


def _enabled() -> bool:
    return os.getenv(_ENV_RECORD, "0") in ("1", "true", "True")


def _record_root() -> Optional[Path]:
    override = os.getenv(_ENV_DIR)
    if override:
        return Path(override)
    try:
        from app.core.config import settings

        return Path(settings.DATA_DIR) / "replay-recordings"
    except Exception:  # noqa: BLE001 — 目录不可得 → 不录制
        return None


def _session_dir_ok(session_id: str) -> bool:
    """会话 id 字符集纪律（与 trace_store._session_dir 同源约束）。"""
    return bool(session_id) and all(
        c.isalnum() or c in "-_" for c in session_id
    )


def _prune_session_dir(session_dir: Path) -> None:
    """保留最近 MAX_TRACES_PER_SESSION 条（按 mtime，淘汰最旧）。"""
    try:
        traces = sorted(
            (p for p in session_dir.glob("*.json") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        overflow = len(traces) - MAX_TRACES_PER_SESSION
        for path in traces[:max(0, overflow)]:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def write_trace(trace_dict: Dict[str, Any], *, session_id: str,
                turn_id: str, root: Path) -> Optional[Path]:
    """原子落盘（tmp + os.replace）；返回写入路径，失败 None。"""
    if not _session_dir_ok(session_id) or not turn_id:
        return None
    try:
        session_dir = root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        target = session_dir / f"{turn_id}.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(trace_dict, ensure_ascii=False, sort_keys=False, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, target)
        _prune_session_dir(session_dir)
        return target
    except OSError:
        logger.debug("[ReplayRecorder] write failed session=%s", session_id,
                     exc_info=True)
        return None


def collect_turn(
    *,
    session_id: str,
    turn_id: str,
    final_text: str = "",
    map_product: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """只读收集当轮事实 → trace dict（chain/evidence 缺席时诚实降级）。"""
    chain_dict: Dict[str, Any] = {}
    turn_summary: Optional[Dict[str, Any]] = None
    try:
        from app.lib.runtime.evidence import TURN_EVIDENCE
        from app.lib.runtime.gis_trace import get_gis_trace_registry

        chain = get_gis_trace_registry().get(turn_id)
        if chain is not None:
            payload = chain.as_dict()
            if session_id:
                payload["session_id"] = session_id
            chain_dict = payload
        evidence = TURN_EVIDENCE.get(turn_id)
        if evidence is not None:
            turn_summary = evidence.to_summary()
    except Exception:  # noqa: BLE001 — 证据面缺席按降级录，不抛
        logger.debug("[ReplayRecorder] collect degraded turn=%s", turn_id,
                     exc_info=True)

    degraded = not chain_dict or turn_summary is None
    trace = build_trace(
        session_id=session_id,
        turn_id=turn_id,
        chain_dict=chain_dict,
        turn_summary=turn_summary or {},
        map_product=map_product if isinstance(map_product, dict) else None,
        final_text=final_text,
        recording={
            "source": "settle",
            "schema": REPLAY_TRACE_SCHEMA_VERSION,
            "degraded": degraded,
        },
    )
    return trace.to_dict()


def maybe_record_turn(
    *,
    session_id: str,
    turn_id: str,
    final_text: str = "",
    map_product: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """settle 缝唯一入口：关闸 no-op；开闸打包落盘。**合约 never-raises。**"""
    if not _enabled():
        return None
    try:
        root = _record_root()
        if root is None:
            return None
        trace_dict = collect_turn(
            session_id=session_id,
            turn_id=turn_id,
            final_text=final_text,
            map_product=map_product,
        )
        if trace_dict is None:
            return None
        path = write_trace(
            trace_dict, session_id=session_id, turn_id=turn_id, root=root,
        )
        return str(path) if path is not None else None
    except Exception:  # noqa: BLE001 — 记录面绝不阻断 settle
        logger.debug("[ReplayRecorder] record failed turn=%s", turn_id,
                     exc_info=True)
        return None
