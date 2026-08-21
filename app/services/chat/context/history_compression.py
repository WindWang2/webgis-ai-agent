"""History compression and token budgeting for chat context."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# 单次请求里给"历史对话"留的 token 预算（粗估）。
HISTORY_TOKEN_BUDGET = 6000
HISTORY_MIN_TURNS = 2  # 至少保留最近 N 轮 user/assistant，绝不为节省 token 砍掉刚刚的对话


_TOKEN_ESTIMATE_MEMO: dict[int, int] = {}
_TOKEN_MEMO_MAX = 8192


def _estimate_tokens(content: object) -> int:
    """超粗 token 估算：CJK 1 char ≈ 1.5 tokens，ASCII 4 char ≈ 1 token。

    精度只要不长期偏离 30% 就行——这里宁可高估也别低估，防止侥幸压线还是爆 context。
    #729: per-char CJK 循环是纯 Python O(N)（781 KiB 历史 47 ms/scan，且每轮
    LLM 至少扫两遍）——换成 encode 差分法数 CJK 字符（同输入 ~57×），并对
    不可变历史消息按 (类型, id) memo，第二轮直接命中。语义与旧实现一致
    （CJK 计数 × 1.5 + 非 CJK / 4 + 1）。
    """
    if content is None:
        return 0
    if isinstance(content, (list, dict)):
        content = json.dumps(content, ensure_ascii=False)
    if not isinstance(content, str):
        content = str(content)
    if not content:
        return 0
    memo_key = hash(content)
    hit = _TOKEN_ESTIMATE_MEMO.get(memo_key)
    if hit is not None:
        return hit
    total = len(content)
    ascii_len = len(content.encode("ascii", "ignore"))
    cjk_approx = total - ascii_len  # 非 ASCII ≈ CJK（旧口径的扩展近似）
    other = ascii_len
    est = int(cjk_approx * 1.5 + other / 4) + 1
    if len(_TOKEN_ESTIMATE_MEMO) < _TOKEN_MEMO_MAX:
        _TOKEN_ESTIMATE_MEMO[memo_key] = est
    return est


def _message_tokens(msg: dict) -> int:
    """估算单条消息总开销（content + tool_calls + tool_call_id 元数据）。"""
    total = _estimate_tokens(msg.get("content"))
    tool_calls = msg.get("tool_calls")
    if tool_calls:
        total += _estimate_tokens(tool_calls)
    return total + 4


def _group_into_turns(messages: list[dict]) -> list[list[dict]]:
    """把消息序列按 user 开头切成"轮次"。

    一轮 = 一个 user 消息 + 后面紧跟的所有 assistant/tool 消息，直到下一个 user。
    """
    turns: list[list[dict]] = []
    current: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role == "user" and current:
            turns.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        turns.append(current)
    return turns


def _build_truncation_notice(dropped_turns: int) -> str:
    return (
        f"[历史折叠] 已省略最早 {dropped_turns} 轮对话以控制上下文长度。"
        f"完整历史仍保存在数据库中（如需引用旧 analysis 结果，可通过 ref:xxx 直接调用）。"
    )


def truncate_history_by_budget(
    history: list[dict],
    budget: int = HISTORY_TOKEN_BUDGET,
    min_turns: int = HISTORY_MIN_TURNS,
) -> tuple[list[dict], int]:
    """按 token 预算截断历史，返回 (保留下来的消息序列, 被丢弃的轮次数)。

    规则：
    - 把消息切成"轮次"（user 开头的连续段）
    - 从最新轮反向纳入，累计 token 不超预算
    - 永远至少保留最近 min_turns 轮，即使总和已超预算
    """
    if not history:
        return history, 0

    turns = _group_into_turns(history)
    if len(turns) <= min_turns:
        return history, 0

    kept_rev: list[list[dict]] = []
    used = 0
    for turn in reversed(turns):
        turn_cost = sum(_message_tokens(m) for m in turn)
        if len(kept_rev) < min_turns:
            kept_rev.append(turn)
            used += turn_cost
            continue
        if used + turn_cost > budget:
            break
        kept_rev.append(turn)
        used += turn_cost

    kept = list(reversed(kept_rev))
    dropped = len(turns) - len(kept)
    if dropped <= 0:
        return history, 0
    flat = [m for turn in kept for m in turn]
    return flat, dropped
