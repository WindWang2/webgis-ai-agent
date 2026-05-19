"""Subagent — 委派子任务给一个隔离的 ChatEngine 微会话。

设计目的（对应 Claude Code 的 Agent 工具模式）：
- 长任务（"批量处理 100 个区县的 LISA"、"为 50 个 POI 找最近设施再着色"）
  在主会话执行会把上百步 trace 全部留在主上下文，token 消耗和 LLM 准确率
  双双崩塌。
- Subagent 把子任务塞进一个**独立的 ChatEngine 实例**：独立的消息历史
  LRU（不污染父对话）、独立的工具子集（tier=1 + 显式指定的 tier=2 域）、
  独立的轮次预算；执行完只把**自然语言摘要 + 关键 ref**回写给主代理。
- session_id 复用父会话 → session_data_manager 中的 GeoJSON refs 与
  map_state 父子原生互通，子任务输出立刻可被父 chain 引用，无需投影。

调用契约：
    result = await SubagentDispatcher(registry, parent_session_id).run(
        task="为海淀区每个医院找最近的地铁站",
        domains=["network", "chinese"],
        extra_tools=["nearest_facility"],
        max_rounds=10,
    )
返回 SubagentResult 含 success / summary / refs / rounds_used / tools_called。

未来扩展（不在 MVP 内）：
- 并行 spawn 多个 subagent（每域一个）
- 子代理也可调 propose_plan 二次嵌套
- 子代理用更小/更快的 LLM（成本优化）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from app.services.chat_engine import ChatEngine

logger = logging.getLogger(__name__)


# ─────────────────────────── 结果数据类 ───────────────────────────


@dataclass
class SubagentResult:
    success: bool
    summary: str = ""
    refs: list[str] = field(default_factory=list)
    reasoning: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "summary": self.summary,
            "refs": self.refs,
            "reasoning": self.reasoning,
            "error": self.error,
        }


# ─────────────────────── 工具子集筛选器 ──────────────────────


def select_tools_for_subagent(
    registry: ToolRegistry,
    *,
    domains: Optional[list[str]] = None,
    extra_tools: Optional[list[str]] = None,
    exclude_tier3: bool = True,
) -> list[dict]:
    """根据 domains + 显式工具白名单挑选子代理可见的 schema 子集。

    - 始终包含 tier=1（基础工具，子任务也需要 buffer/list_layers 等）。
    - tier=2 仅当其 domains 与传入 domains 集合有交集时纳入。
    - tier=3 默认排除（破坏性工具不允许子代理自动调用，除非 exclude_tier3=False）。
    - extra_tools 强制按名字白名单纳入，无视 tier — 用于强制带上某个具体工具。
    - 子代理永远看不到 spawn_subagent / propose_plan，防止递归与计划嵌套。
    """
    domain_set = set(domains or [])
    extra_set = set(extra_tools or [])
    _BLACKLIST_ALWAYS = {"spawn_subagent", "propose_plan", "execute_plan", "get_plan_status"}
    selected: set[str] = set()

    for name, meta in registry.all_metadata().items():
        if name in _BLACKLIST_ALWAYS:
            continue
        tier = int(meta.get("tier", 1))
        if name in extra_set:
            selected.add(name)
            continue
        if tier == 1:
            selected.add(name)
        elif tier == 2:
            tool_domains = set(meta.get("domains") or [])
            if tool_domains & domain_set:
                selected.add(name)
        elif tier == 3 and not exclude_tier3:
            tool_domains = set(meta.get("domains") or [])
            if tool_domains & domain_set:
                selected.add(name)

    return registry.get_schemas_subset(selected)


# ─────────────────────────── 派遣器 ──────────────────────────


class SubagentDispatcher:
    """对接到主 ChatEngine 之外、按需启动短生命周期子代理的派遣器。

    与父：共享 registry、共享 session_data_manager、共享 session_id。
    隔离：独立 ChatEngine 实例（独立 _sessions LRU，自己的工具 catalog stub）。
    """

    SUB_SYSTEM_PROMPT = (
        "你是一个**子代理**（subagent），被主 Agent 委派执行一个具体子任务。\n"
        "纪律：\n"
        "1. 只用允许给你的工具子集；遇到能力之外的需求直接在结论里说明而不是绕路；\n"
        "2. 不要做超出子任务边界的分析；\n"
        "3. 最终回复请用纯文本，1-3 段，描述：(a) 你做了什么；(b) 关键 ref_id；"
        "(c) 给主代理的建议下一步。"
    )

    def __init__(self, registry: ToolRegistry, parent_session_id: str):
        if not parent_session_id:
            raise ValueError("SubagentDispatcher 需要有效的 parent_session_id")
        self.registry = registry
        self.parent_session_id = parent_session_id

    async def run(
        self,
        *,
        task: str,
        domains: Optional[list[str]] = None,
        extra_tools: Optional[list[str]] = None,
        max_rounds: int = 10,
    ) -> SubagentResult:
        tool_subset = select_tools_for_subagent(
            self.registry,
            domains=domains,
            extra_tools=extra_tools,
            exclude_tier3=True,
        )
        tool_names = [s["function"]["name"] for s in tool_subset]
        logger.info(
            "[Subagent] parent=%s task=%r tools=%d (%s)",
            self.parent_session_id, task[:80], len(tool_subset),
            ", ".join(tool_names[:8]) + ("..." if len(tool_names) > 8 else ""),
        )

        # 记下父 session 启动前已有的 refs，结束后用差集得到子任务新增的 refs
        try:
            refs_before = set(session_data_manager.list_refs(self.parent_session_id).keys())
        except Exception:
            refs_before = set()

        sub_engine = self._build_sub_engine(tool_subset, max_rounds)

        try:
            # 给子代理一条复合 prompt：系统纪律 + 具体任务
            wrapped_task = f"{self.SUB_SYSTEM_PROMPT}\n\n# 子任务\n{task}"
            result = await sub_engine.chat(
                message=wrapped_task,
                session_id=self.parent_session_id,
            )
        except Exception as e:
            logger.exception("[Subagent] sub-engine failed")
            return SubagentResult(
                success=False,
                error=str(e),
                summary=f"子代理执行失败: {e}",
            )

        summary = (result.get("content") or "").strip()
        reasoning = (result.get("reasoning") or "").strip()

        # 子任务结束后新增的 refs
        try:
            refs_after = set(session_data_manager.list_refs(self.parent_session_id).keys())
        except Exception:
            refs_after = set()
        new_refs = sorted(refs_after - refs_before)

        return SubagentResult(
            success=True,
            summary=summary,
            reasoning=reasoning,
            refs=new_refs,
        )

    # ─── helpers ────────────────────────────────────────────

    def _build_sub_engine(self, tool_subset: list[dict], max_rounds: int) -> "ChatEngine":
        """造一个轻量 ChatEngine：用同一份 registry，但通过 catalog stub 把
        工具白名单固定为 tool_subset（绕过域关键词匹配）。"""
        from app.services.chat_engine import ChatEngine

        class _FrozenCatalog:
            """只返回 tool_subset 的 catalog stub，禁用粘性 / 关键词匹配。"""

            def __init__(self, schemas: list[dict]):
                self._schemas = schemas

            def select_schemas(self, _user_message: str, session_id: Optional[str] = None):
                return self._schemas

            def reset_session(self, session_id: str) -> None:
                return

        engine = ChatEngine(self.registry, tool_catalog=_FrozenCatalog(tool_subset))
        engine.max_rounds = max_rounds
        return engine
