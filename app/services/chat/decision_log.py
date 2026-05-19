"""工具决策的结构化可观测性日志（JSONL）。

每次工具调用落一条记录，用真实数据回答「选错工具时，是检索问题
（对的工具没被推给 LLM）还是区分度问题（工具都在但选了相邻错工具）」。

写盘失败只记 warning，绝不影响对话主流程。
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import logging
from pathlib import Path
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

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
        d["user_message"] = (self.user_message or "")[:200]
        return d


def _append_line(line: str) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_tool_decision(record: ToolDecisionRecord) -> None:
    """追加一条决策记录。任何 IO 失败都被吞掉，只记 warning。"""
    try:
        _append_line(json.dumps(record.to_dict(), ensure_ascii=False))
    except Exception as e:  # noqa: BLE001 — 可观测性绝不能拖垮主流程
        logger.warning(f"[decision_log] 写入失败: {e}")
