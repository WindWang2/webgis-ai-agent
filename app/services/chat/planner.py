"""规划阶段：启发式门控 + 结构化规划 LLM 调用 + Plan 存储 + 步骤打勾。

设计原则：规划是增强，永不是硬依赖。任何环节失败都降级回无计划循环。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# 追问词：短消息命中其一则视为承接上一轮的追问
_FOLLOWUP_PATTERN = re.compile(
    r"(换|再|又|放大|缩小|颜色|配色|隐藏|显示|去掉|删掉|清除|加粗|样式|"
    r"大一点|小一点|这个|那个|上面|刚才)"
)
_SHORT_THRESHOLD = 20  # 字符数


def should_plan(message: str, messages: list[dict], has_active_plan: bool) -> bool:
    """启发式门控：判断本轮是否需要跑规划阶段。

    跳过规划（返回 False）的条件：消息短 且 命中追问词 且 已有活跃计划。
    其余情况返回 True。无活跃计划时即使是追问也规划（没有上文可承接）。
    """
    text = (message or "").strip()
    is_short = len(text) <= _SHORT_THRESHOLD
    is_followup = bool(_FOLLOWUP_PATTERN.search(text))
    if is_short and is_followup and has_active_plan:
        return False
    return True
