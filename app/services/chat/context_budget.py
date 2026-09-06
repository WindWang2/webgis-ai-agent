"""角色感知上下文预算管理器（ADR-0101 Wave 5, §22）。

现状（audit）：历史有 6000 token 软预算（history_compression），system 块
（环境感知/项目块/裁决/工具 schema）**无界**叠加 —— 真实 prompt 规模只有
事后估算，没有任何组件回答「这个 prompt 是否会撞模型上下文窗」。

本模块提供确定性的预算**规划与度量**（不截断、不改 prompt —— 截断权仍在
各既有组件；预算器的职责是给出可解释的分配与违规留痕）：

- ``ContextBudgetPlan``：按类别分配 token 预算（优先级确定性）；
- ``measure_components(...)``：对实际组装产物分类度量 → ``BudgetReport``；
- 超预算 → reason codes（trace/debug bundle 用），**绝不静默**；
- tool schema 类别有硬上限比例（§22「工具 schema 永不吃满上下文」）。

token 估算复用 history_compression 的 CJK-aware ``_estimate_tokens``
（单一估算语义；CJK 1 字 ≈ 1.5 token，ASCII 4 字符 ≈ 1 token）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Sequence

from app.services.chat.context.history_compression import _estimate_tokens

logger = logging.getLogger(__name__)


class Category(IntEnum):
    """预算类别；值 = 裁剪优先级（小者最后被裁）。 IntEnum 保证确定性排序。"""

    RESERVED_OUTPUT = 0      # 保留输出（不可侵占）
    SYSTEM_INSTRUCTIONS = 1  # 系统/agent 指令
    USER_PROMPT = 2          # 用户当轮输入（不裁）
    TOOL_SCHEMAS = 3         # 工具 schema（硬上限比例）
    SESSION_PLAN = 4
    HISTORY = 5
    HARNESS_EVIDENCE = 6     # GIS Harness 证据/裁决
    MAP_STATE = 7
    LAYER_INVENTORY = 8
    PROJECT_CONTEXT = 9
    DATA_REFS = 10
    TRACE_SUMMARIES = 11     # 执行轨迹摘要（最先裁）


#: tool schema 硬上限（占可用上下文比例）—— §22 红线
TOOL_SCHEMA_MAX_FRACTION = 0.35
#: 输出保留 + 安全边际（占 context window 比例）
_OUTPUT_RESERVE_FRACTION = 0.25   # max_output + 生成余量 + 估算误差
#: 超预算告警阈值（估算 ≥ 90% 可用 → 警告；≥ 100% → 违规）
_WARN_FRACTION = 0.90


@dataclass(frozen=True)
class BudgetPlan:
    """一次预算规划（全部为 token 数）。"""

    context_window: int
    reserved_output: int
    usable: int
    category_budgets: Dict[str, int]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "context_window": self.context_window,
            "reserved_output": self.reserved_output,
            "usable": self.usable,
            "category_budgets": dict(self.category_budgets),
        }


@dataclass
class BudgetItem:
    """一个可度量的上下文组件。"""

    category: Category
    name: str
    text: str = ""
    est_tokens: int = 0
    hard_limit_tokens: Optional[int] = None   # 组件自身上限（如 tool schema 预算）

    def __post_init__(self):
        if not self.est_tokens and self.text:
            self.est_tokens = _estimate_tokens(self.text)


@dataclass
class BudgetReport:
    """度量结果（trace/debug bundle 用；reason codes 全量留痕）。"""

    total_est_tokens: int
    usable: int
    over_budget: bool
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    by_category: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "total_est_tokens": self.total_est_tokens,
            "usable": self.usable,
            "over_budget": self.over_budget,
            "violations": list(self.violations),
            "warnings": list(self.warnings),
            "by_category": dict(self.by_category),
        }


def plan_budget(
    context_window: int,
    max_output_tokens: int,
    *,
    output_reserve_fraction: float = _OUTPUT_RESERVE_FRACTION,
    tool_schema_fraction: float = TOOL_SCHEMA_MAX_FRACTION,
) -> BudgetPlan:
    """确定性预算规划。

    分配规则：
    1. 保留输出 = max(max_output_tokens, context_window ×
       output_reserve_fraction)（两者取大 —— 估算误差 + 生成余量），但不超过
       窗口的一半（max_output 大于保守窗口时不至于把可用空间清零）；
    2. 可用 = context_window - 保留输出；
    3. tool schema 类预算 = min(可用 × tool_schema_fraction, 12k)；
    4. 其余类别共享剩余（规划层不细分到每类 —— 度量层按实际用量判定）。
    """
    if context_window <= 0:
        # 未知 context window：保守按 8k 窗口规划（宁可误报不漏报）
        context_window = 8192
    reserved = max(int(max_output_tokens or 0), int(context_window * output_reserve_fraction))
    reserved = min(reserved, context_window // 2)
    usable = max(0, context_window - reserved)
    tool_budget = int(min(usable * tool_schema_fraction, 12288))
    return BudgetPlan(
        context_window=context_window,
        reserved_output=reserved,
        usable=usable,
        category_budgets={Category.TOOL_SCHEMAS.name: tool_budget},
    )


def measure_components(
    items: Sequence[BudgetItem],
    plan: BudgetPlan,
) -> BudgetReport:
    """对实际组件做确定性度量（§23：同输入必同输出）。"""
    by_category: Dict[str, int] = {}
    violations: List[str] = []
    warnings: List[str] = []
    total = 0
    for item in items:
        est = int(item.est_tokens)
        total += est
        by_category[item.category.name] = by_category.get(item.category.name, 0) + est
        limit = item.hard_limit_tokens
        if limit is None and item.category is Category.TOOL_SCHEMAS:
            limit = plan.category_budgets.get(Category.TOOL_SCHEMAS.name)
        if limit is not None and est > limit:
            violations.append(
                f"over_limit:{item.name}:{est}>{limit}"
            )
    over = total > plan.usable
    if over:
        violations.append(f"over_budget:total:{total}>{plan.usable}")
    elif total >= plan.usable * _WARN_FRACTION:
        warnings.append(f"near_budget:total:{total}/{plan.usable}")
    return BudgetReport(
        total_est_tokens=total,
        usable=plan.usable,
        over_budget=over,
        violations=violations,
        warnings=warnings,
        by_category=by_category,
    )


# ---------------------------------------------------------------------------
# 组装产物 → 预算组件的适配（context_assembler 集成用）
# ---------------------------------------------------------------------------

def measure_assembled_context(
    messages: Sequence[Dict[str, Any]],
    tools_payload: str = "",
    *,
    context_window: Optional[int],
    max_output_tokens: int,
    history_budget_tokens: int = 6000,
) -> BudgetReport:
    """对 assembler 产物做分类度量。

    消息级分类：system → SYSTEM_INSTRUCTIONS；最后一条 user → USER_PROMPT；
    其余 → HISTORY。工具 schema 计 TOOL_SCHEMAS（预算 = plan 的 schema
    上限）。history 预算超限记 violation（与既有 6000 软预算对齐的可观测
    强制化，不改变截断行为）。
    """
    plan = plan_budget(
        context_window=context_window or 0,
        max_output_tokens=max_output_tokens,
    )
    items: List[BudgetItem] = []
    history_est_tokens = 0
    for idx, m in enumerate(messages):
        role = m.get("role")
        content = m.get("content")
        text = content if isinstance(content, str) else ""
        if role == "system":
            items.append(BudgetItem(
                category=Category.SYSTEM_INSTRUCTIONS, name=f"system[{idx}]", text=text,
            ))
        elif role == "user" and idx == len(messages) - 1:
            items.append(BudgetItem(
                category=Category.USER_PROMPT, name=f"user_final[{idx}]", text=text,
            ))
        else:
            # PERF（review R1）：逐消息累加估算（_estimate_tokens 带 memo，命中
            # 既有字符串对象）—— 不再 join 成新字符串重扫一遍。
            history_est_tokens += _estimate_tokens(text)
    items.append(BudgetItem(
        category=Category.HISTORY, name="history",
        est_tokens=history_est_tokens,
        hard_limit_tokens=history_budget_tokens,
    ))
    if tools_payload:
        items.append(BudgetItem(
            category=Category.TOOL_SCHEMAS, name="tool_schemas", text=tools_payload,
        ))
    return measure_components(items, plan)
