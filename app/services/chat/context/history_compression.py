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


# ── audit4 #980: 轮内（intra-turn）tool 结果软预算 ──────────────────────────
# truncate_history_by_budget 以「用户轮」为原子、min_turns 强制保留最近轮，
# 但一个用户回合内最多 CHAT_MAX_ROUNDS=60 轮 LLM 循环的 tool_calls+tool 结果
# 全部属于"当前轮"——预算对轮内增长完全无效，每轮全量重发导致 token 二次方
# 级膨胀。折叠策略：当前回合内只保留最近 keep_recent 条 tool 结果原文，更早
# 的替换为单行占位（消息不删、tool_call/tool 配对保持，provider 不会拒绝）。
_TURN_TOOL_KEEP_RECENT = 8   # 保留原文的最新 tool 结果条数
_TURN_TOOL_FOLD_MIN = 12     # 回合内 tool 消息少于该数不折叠（小回合无 churn）
_SYNTHETIC_TOOL_USER_PREFIX = "[工具执行结果]"
_FOLDED_TOOL_PLACEHOLDER = (
    "[已折叠] {name} 已执行，结果已省略以控制上下文长度。"
    "如需引用其产物请使用已返回的 ref/别名；如需重跑请微调参数"
    "（完全同参会被去重拦截）。"
)


def fold_intra_turn_tool_results(
    messages: list[dict],
    keep_recent: int = _TURN_TOOL_KEEP_RECENT,
) -> list[dict]:
    """折叠**当前回合**内较早的 tool 结果（仅影响发给 LLM 的视图，不改动库）。

    - 当前回合 = 最后一条真实 user 消息（排除 XML 路径合成的
      ``[工具执行结果]`` 载体）之后的全部消息。
    - 回合内 tool 消息 ≤ fold_min 时不动作，返回原列表。
    - 被折叠消息的 content 替换为单行占位（配对不变），其余字段原样保留。
    """
    if not messages:
        return messages
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        text = content if isinstance(content, str) else ""
        if text.startswith(_SYNTHETIC_TOOL_USER_PREFIX):
            continue
        last_user_idx = i
        break
    if last_user_idx < 0:
        return messages
    tail = messages[last_user_idx + 1:]
    tool_positions = [j for j, m in enumerate(tail) if m.get("role") == "tool"]
    if len(tool_positions) <= max(keep_recent, _TURN_TOOL_FOLD_MIN):
        return messages

    # tool_call_id → 工具名（占位文案里指名道姓，帮助模型定位）
    call_names: dict[str, str] = {}
    for m in tail:
        for tc in m.get("tool_calls") or []:
            try:
                call_names[tc["id"]] = tc["function"]["name"]
            except (KeyError, TypeError):
                continue

    fold_set = set(tool_positions[:-keep_recent]) if keep_recent > 0 else set(tool_positions)
    if not fold_set:
        return messages
    new_tail: list[dict] = []
    for j, m in enumerate(tail):
        if j in fold_set:
            name = call_names.get(m.get("tool_call_id") or "", "工具")
            folded = dict(m)
            folded["content"] = _FOLDED_TOOL_PLACEHOLDER.format(name=name)
            new_tail.append(folded)
        else:
            new_tail.append(m)
    return messages[: last_user_idx + 1] + new_tail
