"""生产录制器（B2，ADR-0256 决策二）：S13 settle 缝的 env-gated 打包。

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

from app.contracts.gis_trace import TraceChainRecord
from app.lib.harness.replay.recording_contracts import EnvironmentFingerprint

from app.lib.harness.replay.schema import REPLAY_TRACE_SCHEMA_VERSION, build_trace

logger = logging.getLogger(__name__)

MAX_TRACES_PER_SESSION = 64
MAX_RECORDING_SESSIONS = 256
MAX_RECORDING_BYTES = 128 * 1024 * 1024
RECORDING_TTL_S = 7 * 86400

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


def _prune_recordings(root: Path) -> None:
    """Global age, count and byte quotas complement each session's ring."""
    import time
    import shutil
    sessions = []
    total = 0
    for directory in root.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        files = [p for p in directory.glob("*.json") if p.is_file()]
        for path in files:
            if time.time() - path.stat().st_mtime > RECORDING_TTL_S:
                path.unlink(missing_ok=True)
        files = [p for p in files if p.exists()]
        size = sum(p.stat().st_size for p in files)
        newest = max((p.stat().st_mtime for p in files), default=0)
        total += size
        sessions.append((newest, directory, size))
    sessions.sort()
    while sessions and (len(sessions) > MAX_RECORDING_SESSIONS or total > MAX_RECORDING_BYTES or sessions[0][0] == 0):
        _, directory, size = sessions.pop(0)
        shutil.rmtree(directory)
        total -= size


def write_trace(trace_dict: Dict[str, Any], *, session_id: str,
                turn_id: str, root: Path) -> Optional[Path]:
    """原子落盘（tmp + os.replace）；返回写入路径，失败 None。"""
    if not _session_dir_ok(session_id) or not _session_dir_ok(turn_id):
        # turn_id 与 session_id 同字符集纪律（防路径分隔符注入）。
        return None
    try:
        session_dir = root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        target = session_dir / f"{turn_id}.json"
        tmp = target.with_suffix(".json.tmp")
        from app.lib.harness.replay.sanitize import sanitize_value
        trace_dict = dict(trace_dict)
        for field in ("user_input", "final_text"):
            trace_dict[field] = sanitize_value(trace_dict.get(field, ""))
        tmp.write_text(
            json.dumps(trace_dict, ensure_ascii=False, sort_keys=False, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, target)
        _prune_session_dir(session_dir)
        _prune_recordings(root)
        return target if target.exists() else None
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
    chain_dict: TraceChainRecord = {}
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
    # ADR-0212：录制时 capability registry 指纹（重放期 drift 归因面）。
    # ADR-0262 D2：环境指纹 v2 —— 封闭白名单（行为开关/policy 版本/
    # source 指纹/manifest/预算摘要），缺席段诚实降级，绝不阻断录制。
    env_payload: EnvironmentFingerprint = {}
    try:
        from app.lib.harness.replay.drift import collect_env_fingerprint

        env_payload = collect_env_fingerprint()
    except Exception:  # noqa: BLE001 — 指纹缺席按无录制，不阻断
        env_payload = {}
    try:
        from app.lib.harness.replay.drift import capability_registry_digest

        if not env_payload.get("registry_digest"):
            digest = capability_registry_digest()
            if digest:
                env_payload["registry_digest"] = digest
    except Exception as _exc:  # noqa: BLE001 — 指纹缺席按无录制，不阻断
        logging.getLogger(__name__).debug("recorder: optional operation unavailable (%s)", type(_exc).__name__)
    trace = build_trace(
        session_id=session_id,
        turn_id=turn_id,
        chain_dict=chain_dict,
        turn_summary=turn_summary or {},
        map_product=map_product if isinstance(map_product, dict) else None,
        final_text=final_text,
        env=env_payload or None,
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
