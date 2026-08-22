"""工具决策的结构化可观测性日志（JSONL）。

每次工具调用落一条记录，用真实数据回答「选错工具时，是检索问题
（对的工具没被推给 LLM）还是区分度问题（工具都在但选了相邻错工具）」。

写盘失败只记 warning，绝不影响对话主流程。
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_LOG_PATH = Path("logs/tool_decisions.jsonl")


@dataclasses.dataclass
class ToolDecisionRecord:
    session_id: str
    round: int
    user_message: str
    active_domains: list[str]
    from_plan: bool
    subset_size: int
    total_tools: int
    tool_chosen: str
    tool_args: dict[str, Any]
    result_quality: str            # "ok" | "empty" | "error"
    plan_step_matched: Optional[int]  # 命中的计划步骤号；无计划时为 None
    # design-v3 §6 observability（additive）：
    plan_id: Optional[str] = None
    plan_revision: Optional[int] = None
    step_id: Optional[str] = None        # 命中的 canonical 步骤 id（如 s1）
    failure_class: Optional[str] = None  # 仅 status=="error" 时有值
    recovery_action: Optional[str] = None

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # 审计 S44：user_message 截断到 200 字符（已有），tool_args 也截断。
        # tool_args 常含完整 GeoJSON（可达 MB 级）+ 可能含路径/坐标 PII，
        # 全量写日志会撑爆磁盘 + 泄漏敏感数据。整体 args JSON 截到 2000 字符。
        d["user_message"] = (self.user_message or "")[:200]
        d["tool_args"] = _redact_and_truncate_args(d.get("tool_args"))
        return d


def _redact_and_truncate_args(args: Any, max_len: int = 2000) -> Any:
    """审计 S44：tool_args 截断 + 敏感 key 脱敏。

    - 整体 JSON 字符串超 max_len 的，截断并加标记
    - 含 'api_key'/'token'/'password'/'secret' 的 key 值替换为 '<redacted>'
    """
    if not isinstance(args, dict):
        return args
    REDACT_KEYS = {"api_key", "token", "password", "secret", "apikey", "Authorization"}
    redacted = {}
    for k, v in args.items():
        if isinstance(k, str) and k.lower() in REDACT_KEYS:
            redacted[k] = "<redacted>"
        else:
            redacted[k] = v
    try:
        s = json.dumps(redacted, ensure_ascii=False, default=str)
        if len(s) > max_len:
            return s[:max_len] + "...[truncated]"
        return redacted
    except (TypeError, ValueError):
        return str(redacted)[:max_len]


def _append_line(line: str) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# audit #822: 决策日志的同步写盘发生在事件循环上（chat_stream 工具波内、
# 持会话锁）——慢盘/网络盘会冻结整个循环。后台模式把 append 挪到单工作
# 线程（保序），有界 in-flight（超出丢弃并告警）：可观测性绝不反压主流程。
_BG_MAX_INFLIGHT = 256
_bg_executor: Optional["concurrent.futures.ThreadPoolExecutor"] = None
_bg_inflight = 0
_bg_lock = threading.Lock()


def _get_bg_executor() -> "concurrent.futures.ThreadPoolExecutor":
    global _bg_executor
    if _bg_executor is None:
        _bg_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="decision-log"
        )
    return _bg_executor


def _enqueue_background(line: str) -> None:
    global _bg_inflight
    with _bg_lock:
        if _bg_inflight >= _BG_MAX_INFLIGHT:
            logger.warning("[decision_log] background queue full — dropping record")
            return
        _bg_inflight += 1

    def _write() -> None:
        global _bg_inflight
        try:
            _append_line(line)
        finally:
            with _bg_lock:
                _bg_inflight -= 1

    try:
        _get_bg_executor().submit(_write)
    except Exception as e:  # noqa: BLE001 — 提交失败按丢弃处理
        with _bg_lock:
            _bg_inflight -= 1
        logger.warning(f"[decision_log] background submit failed: {e}")


def log_tool_decision(record: ToolDecisionRecord, *, background: bool = False) -> None:
    """追加一条决策记录。任何 IO 失败都被吞掉，只记 warning。

    ``background=True`` 在单工作线程异步写盘（audit #822：工具波内的同步
    写盘会阻塞事件循环）；``False``（默认/测试路径）保持同步语义。
    """
    try:
        line = json.dumps(record.to_dict(), ensure_ascii=False)
        if background:
            _enqueue_background(line)
        else:
            _append_line(line)
    except Exception as e:  # noqa: BLE001 — 可观测性绝不能拖垮主流程
        logger.warning(f"[decision_log] 写入失败: {e}")
