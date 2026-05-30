"""Session overview and duration calculation for chat context."""
from __future__ import annotations

import datetime
from app.services.session_data import session_data_manager

def _format_duration(started_at_iso: str | None) -> str | None:
    """ISO 时间字符串 -> '23 分钟' / '2 小时 5 分钟' / '3 天'。"""
    if not started_at_iso:
        return None
    try:
        started = datetime.datetime.fromisoformat(started_at_iso)
    except (ValueError, TypeError):
        return None
    if started.tzinfo is None:
        now = datetime.datetime.now()
    else:
        now = datetime.datetime.now(started.tzinfo)
    delta = now - started
    total_min = int(delta.total_seconds() // 60)
    if total_min < 1:
        return "<1 分钟"
    if total_min < 60:
        return f"{total_min} 分钟"
    hours = total_min // 60
    mins = total_min % 60
    if hours < 24:
        return f"{hours} 小时 {mins} 分钟" if mins else f"{hours} 小时"
    days = hours // 24
    return f"{days} 天"


async def build_session_overview(
    session_id: str,
    messages: list[dict] | None = None,
    started_at: str | None = None,
    event_log: list[dict] | None = None,
    inventory: dict | None = None,
    _fetched: bool = False,
) -> str | None:
    """组装一行『会话概览』：持续时长 / 对话轮数 / 工具调用 / 失败 / 数据引用 / 导出。

    LLM 拿这一行可以快速判断"用户处于探索初期还是已深入分析"——回答风格、
    建议详略都会跟着调整（早期多解释，深入后简洁直给）。
    """
    if not _fetched:
        started_at = await session_data_manager.get_started_at(session_id) if hasattr(session_data_manager, "get_started_at") else None
        event_log = await session_data_manager.get_event_log(session_id) or []
        inventory = await session_data_manager.list_refs(session_id) or {}

    duration = _format_duration(started_at)

    tool_calls = [e for e in (event_log or []) if e.get("event") == "tool_executed"]
    errors = [e for e in tool_calls if (e.get("data") or {}).get("is_error")]
    exports = [e for e in tool_calls if (e.get("data") or {}).get("command") == "export_map"]

    refs_count = len(inventory or {})

    turn_count = 0
    if messages:
        # 数 user 角色出现次数 = 用户提问轮数
        turn_count = sum(1 for m in messages if m.get("role") == "user")

    parts: list[str] = []
    if duration:
        parts.append(f"持续 {duration}")
    if turn_count:
        parts.append(f"{turn_count} 轮提问")
    if tool_calls:
        tool_seg = f"调用 {len(tool_calls)} 次工具"
        if errors:
            tool_seg += f" (其中 {len(errors)} 次失败)"
        parts.append(tool_seg)
    if refs_count:
        parts.append(f"{refs_count} 个数据引用")
    if exports:
        parts.append(f"{len(exports)} 张已导出地图")
    if not parts:
        return None
    return " / ".join(parts)
