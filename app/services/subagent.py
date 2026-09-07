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

并行委派（ADR-0104 决策 7，Wave 6）：
    batch = await dispatcher.run_parallel([SubagentTaskSpec(task=...), ...])
- 有界并行：asyncio.Semaphore 上限 SUBAGENT_PARALLEL_CONCURRENCY=2；
- 父预算 roll-up：传入 parent_budget → 子代理的每次工具调用同时计入父预算；
- 失败隔离：gather(return_exceptions=True)，单个子代理失败不取消兄弟；
- 诚实 settle：全成=completed / 部分失败=partial / 全败=failed。
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING, Union

from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from app.services.chat_engine import ChatEngine
    from app.services.subagent_roles import SubagentBudget, SubagentRole

logger = logging.getLogger(__name__)


# ADR-0104 决策 7：并行委派的有界上限（kill switch：GIS_SUBAGENT_PARALLEL=0
# 时 run_parallel 诚实拒绝，不 spawn 任何子代理；省略 role/并行的调用路径
# 行为与本模块历史版本逐字节一致）。
SUBAGENT_PARALLEL_CONCURRENCY = 2
SUBAGENT_MAX_PARALLEL_CHILDREN = 6
_SUBAGENT_PARALLEL_KILL_SWITCH_ENV = "GIS_SUBAGENT_PARALLEL"
_KILL_SWITCH_OFF_VALUES = {"0", "false", "no", "off"}


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


@dataclass(frozen=True)
class SubagentTaskSpec:
    """并行委派的单个子任务声明（ADR-0104 决策 7）。

    role 为注册表角色名（get_subagent_role 校验，未知角色 fail-closed）；
    预算/域约束沿用 role∩caller 交集语义（角色只收紧不放宽）。
    """

    task: str
    domains: Optional[Tuple[str, ...]] = None
    extra_tools: Optional[Tuple[str, ...]] = None
    max_rounds: int = 10
    role: Optional[str] = None


@dataclass
class SubagentBatchResult:
    """并行委派的诚实 settle 结果。

    status: completed（全成）/ partial（部分失败）/ failed（全败或被禁用）。
    results 顺序与传入 specs 一一对应（asyncio.gather 保序）。
    """

    status: str
    results: List[SubagentResult] = field(default_factory=list)
    budget_usage: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "success": self.success,
            "results": [r.to_dict() for r in self.results],
            "budget_usage": dict(self.budget_usage),
            "error": self.error,
        }


# ADR-0103（§九）：递归子代理深度计数（纵深防御；主防线是 spawn_subagent
# 不进子代理工具面）。深度 ≥2 的嵌套 spawn 诚实失败。
_subagent_depth: "contextvars.ContextVar[int]" = contextvars.ContextVar("subagent_depth", default=0)


@contextlib.contextmanager
def _raise_subagent_depth():
    """子代理执行期深度 +1（contextvar —— 只影响本子代理内发起的嵌套 spawn）。"""
    token = _subagent_depth.set(_subagent_depth.get(0) + 1)
    try:
        yield
    finally:
        _subagent_depth.reset(token)


# ─────────────────────── 工具子集筛选器 ──────────────────────


def select_tools_for_subagent(
    registry: ToolRegistry,
    *,
    domains: Optional[list[str]] = None,
    extra_tools: Optional[list[str]] = None,
    exclude_tier3: bool = True,
    allow_mutation: Optional[bool] = None,
) -> list[dict]:
    """根据 domains + 显式工具白名单挑选子代理可见的 schema 子集。

    - 始终包含 tier=1（基础工具，子任务也需要 buffer/list_layers 等）。
    - tier=2 仅当其 domains 与传入 domains 集合有交集时纳入。
    - tier=3 默认排除（破坏性工具不允许子代理自动调用，除非 exclude_tier3=False）。
    - extra_tools 按名字白名单纳入 — 用于强制带上某个具体工具（SEC-F2：
      tier-3 工具仍受 exclude_tier3 约束，父 turn 无可委托的确认）。
    - 子代理永远看不到 spawn_subagent / propose_plan，防止递归与计划嵌套。
    - ADR-0101 Wave 7 + review R1 MAJOR（fail-closed）：allow_mutation=False
      采用**allow-list** —— 只保留显式声明为只读类（pure /
      deterministic_compute / cacheable_read）的工具。UNCLASSIFIED（存量全库
      默认）意味着「未知」，未知不可入只读面（此前黑名单式剔除对未分类工具
      是 no-op —— 全库 200+ 工具无一声明，角色权限面形同虚设）。代价：描述符
      富化完成前，no-mutation 角色的可见工具为空 —— 这是诚实的失败方向
      （富化是角色带工具的前置条件，见 docs/agent-runtime/tool-descriptor.md）。
    """
    from app.tools.descriptor import SideEffectClass

    domain_set = set(domains or [])
    extra_set = set(extra_tools or [])
    _BLACKLIST_ALWAYS = {"spawn_subagent", "propose_plan", "execute_plan", "get_plan_status"}
    _READONLY_CLASSES = {
        SideEffectClass.PURE,
        SideEffectClass.DETERMINISTIC_COMPUTE,
        SideEffectClass.CACHEABLE_READ,
    }
    selected: set[str] = set()

    for name, meta in registry.all_metadata().items():
        if name in _BLACKLIST_ALWAYS:
            continue
        tier = int(meta.get("tier", 1))
        if name in extra_set:
            # SEC-F2: an explicit extra_tools request must not override the
            # tier-3 exclusion — the parent turn holds no confirmed tier-3
            # grant it could delegate, and the subagent LLM must not even see
            # the schema.
            if tier >= 3 and exclude_tier3:
                continue
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

    if allow_mutation is False:
        selected = {
            name for name in selected
            if registry.descriptor(name).side_effect in _READONLY_CLASSES
        }

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
        role: Optional[Union[str, "SubagentRole"]] = None,
        budget_overlay: Optional["SubagentBudget"] = None,
    ) -> SubagentResult:
        _depth = _subagent_depth.get(0)
        if _depth >= 2:
            return SubagentResult(
                success=False,
                summary="",
                error="subagent recursion depth limit exceeded (max=2); nested spawn is not allowed",
            )
        # ADR-0101 Wave 7：角色档（显式策略，无未约束子代理）。角色提供的
        # 域/预算与调用方显式参数取**交集/更严者** —— 角色收紧，调用方不能
        # 经参数越权放宽。
        from app.services.subagent_roles import (
            BudgetExceeded,
            SubagentBudget,
            SubagentRole as _SubagentRole,
            get_subagent_role,
            wrap_dispatch_with_budget,
        )

        role_obj: Optional[_SubagentRole]
        if isinstance(role, _SubagentRole):
            role_obj = role
        elif isinstance(role, str):
            role_obj = get_subagent_role(role)
        else:
            role_obj = None
        if role_obj is not None:
            if role_obj.allowed_domains:
                domain_set = set(domains or [])
                domains = sorted(
                    domain_set & set(role_obj.allowed_domains)
                    if domain_set else set(role_obj.allowed_domains)
                )
            max_rounds = min(max_rounds, role_obj.max_rounds)
        allow_mutation = role_obj.allow_mutation if role_obj is not None else None

        tool_subset = select_tools_for_subagent(
            self.registry,
            domains=domains,
            extra_tools=extra_tools,
            exclude_tier3=True,
            allow_mutation=allow_mutation,
        )
        if role_obj is not None and role_obj.max_tool_calls == 0:
            tool_subset = []
        tool_names = [s["function"]["name"] for s in tool_subset]
        logger.info(
            "[Subagent] parent=%s role=%s task=%r tools=%d (%s)",
            self.parent_session_id,
            role_obj.name if role_obj else "adhoc",
            task[:80], len(tool_subset),
            ", ".join(tool_names[:8]) + ("..." if len(tool_names) > 8 else ""),
        )

        # 记下父 session 启动前已有的 refs，结束后用差集得到子任务新增的 refs
        try:
            refs_before = set((await session_data_manager.list_refs(self.parent_session_id)).keys())
        except Exception:
            refs_before = set()

        sub_engine = self._build_sub_engine(tool_subset, max_rounds)
        # ADR-0103：子代理按角色档案路由模型（subagent_worker /
        # subagent_reviewer / structured_extraction → model_runtime.roles）；
        # adhoc 子代理回落 execution 主模型。构造后注入而非改构造签名 ——
        # 测试桩（lambda 双参）保持兼容。
        sub_engine.model_role = (role_obj.model_role if role_obj is not None else "execution")

        # §32 层级预算：turn → agent → subagent → tools。工具调用计数经
        # dispatch 实例包装实现（引擎零改动）；墙钟在下方 asyncio.wait 的
        # timeout 参数上执行（超时 → budget_exceeded:wall_time 诚实失败）。
        # 防御：测试桩引擎可能没有 dispatch_service（如 cancellation 单测的
        # _SubEngine）—— 此时跳过包装（预算只覆盖真实引擎路径）。
        # review R1 minor：adhoc（无角色）子代理不再静默吃固定 40 次硬预算 ——
        # 按轮次推导（每轮多工具波余量），行为对齐既有「只有 max_rounds 约束」
        # 的基线，同时保留失控保护。
        budget = SubagentBudget(
            max_tool_calls=role_obj.max_tool_calls if role_obj else max(40, max_rounds * 6),
            max_heavy_tool_calls=role_obj.max_heavy_tool_calls if role_obj else max(8, max_rounds * 2),
            max_wall_time_s=role_obj.max_wall_time_s if role_obj else 300.0,
        )
        _dispatch_service = getattr(sub_engine, "dispatch_service", None)
        if _dispatch_service is not None and hasattr(_dispatch_service, "dispatch"):
            _orig_dispatch = _dispatch_service.dispatch

            async def _budgeted_dispatch(tc, session_id, executed_tools=None):
                # V4 §32 roll-up：声明 parent_budget（run_parallel 传入）时，
                # 子代理每次工具调用先查父预算再查自身预算 —— 任一超限即
                # BudgetExceeded（子代理诚实失败，兄弟不受影响）。
                checked = wrap_dispatch_with_budget(_orig_dispatch, budget, self.registry)
                if budget_overlay is not None:
                    checked = wrap_dispatch_with_budget(checked, budget_overlay, self.registry)
                return await checked(tc, session_id, executed_tools)

            _dispatch_service.dispatch = _budgeted_dispatch  # type: ignore[method-assign]

        # ADR-0100：子代理取消接线。此前取消只能以「任务已取消」文案形式
        # 从工具层渗回来 —— 引擎循环本身不观察令牌，父 turn 取消后子代理
        # 会把剩余轮次全部跑完。现在：子令牌链接父令牌（父取消 → 子取消），
        # chat() 与 token.wait() 竞速；取消时诚实返回 success=False + 明确
        # cancelled 语义（不再靠字符串比较猜）。
        from app.lib.cancellation import (
            CancellationToken,
            OperationCancelled,
            current_token,
            use_token,
        )

        parent_token = current_token()
        sub_token = CancellationToken(job_id=getattr(parent_token, "job_id", None))
        if parent_token is not None:
            parent_token.link(sub_token)

        wrapped_task_text = f"{self.SUB_SYSTEM_PROMPT}\n\n# 子任务\n{task}"
        if role_obj is not None:
            # ADR-0104：expected_outputs 结构化输出契约注入任务头（声明式；
            # 旧角色该字段为空 → 任务文本与历史版本逐字节一致）。
            _expected_line = (
                f"[期望输出] {'；'.join(role_obj.expected_outputs)}\n"
                if role_obj.expected_outputs
                else ""
            )
            wrapped_task_text = (
                f"[角色] {role_obj.title}\n"
                f"[纪律] 工具调用上限 {budget.max_tool_calls} 次（重工具 ≤ "
                f"{budget.max_heavy_tool_calls}）、墙钟 ≤ {budget.max_wall_time_s:.0f}s、"
                f"{'禁止修改任何会话/地图状态' if not role_obj.allow_mutation else '允许读写会话数据'}。\n"
                f"{_expected_line}\n"
                + wrapped_task_text
            )
        try:
            with use_token(sub_token), _raise_subagent_depth():
                chat_task = asyncio.create_task(
                    sub_engine.chat(
                        message=wrapped_task_text,
                        session_id=self.parent_session_id,
                    )
                )
                cancel_task = asyncio.create_task(sub_token.wait())
                try:
                    # review R1 MAJOR：墙钟预算真实执行 —— asyncio.wait 带
                    # 剩余预算超时（此前只有注释宣称 asyncio.timeout，纯 LLM
                    # 子代理完全不受墙钟约束，timeout 处理分支是死代码）。
                    # V4 roll-up：声明父预算时子代理墙钟 = min(自身, 父剩余)。
                    _wall_budget_s = budget.max_wall_time_s
                    if budget_overlay is not None:
                        _wall_budget_s = min(
                            _wall_budget_s, budget_overlay.remaining_wall_time_s()
                        )
                    done, pending = await asyncio.wait(
                        {chat_task, cancel_task},
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=max(0.1, _wall_budget_s),
                    )
                except BaseException:
                    # review M-C1：派发器自身被硬取消（客户端断开/turn 拆除）
                    # 时 CancelledError 先于下方 pending 清理传播 —— 在此
                    # 兜底回收两个任务，子代理绝不泄漏续跑。
                    for t in (chat_task, cancel_task):
                        t.cancel()
                    raise
                if not done:
                    # 墙钟预算耗尽（wait 超时返回空集）
                    chat_task.cancel()
                    cancel_task.cancel()
                    with contextlib.suppress(BaseException):
                        await chat_task
                    budget_used = budget.usage()
                    logger.warning(
                        "[Subagent] parent=%s wall-time budget exceeded: %s",
                        self.parent_session_id, budget_used,
                    )
                    return SubagentResult(
                        success=False,
                        summary=f"子代理超过墙钟预算（{budget.max_wall_time_s:.0f}s）被终止",
                        refs=[],
                        error="budget_exceeded:wall_time",
                    )
            for t in pending:
                t.cancel()
            # review m2：被取消的 chat_task 若已带异常完成，必须取回异常
            # （避免 GC 期 "exception never retrieved"）。
            with contextlib.suppress(BaseException):
                await chat_task
            if cancel_task in done and not sub_token.cancelled:
                # cancel_task 因外层异常先完成（罕见）—— 走 chat 结果路径
                cancel_task.cancel()
            if sub_token.cancelled and chat_task not in done:
                reason = sub_token.reason or "parent turn cancelled"
                logger.info(
                    "[Subagent] parent=%s cancelled mid-run: %s",
                    self.parent_session_id, reason,
                )
                return SubagentResult(
                    success=False,
                    summary=f"子代理已取消: {reason}",
                    refs=[],
                    error="cancelled",
                )
            if sub_token.cancelled and chat_task in done:
                # chat_task 可能已因工具层取消而异常/失败完成 —— 取其结果按取消语义
                reason = sub_token.reason or "parent turn cancelled"
                return SubagentResult(
                    success=False,
                    summary=f"子代理已取消: {reason}",
                    refs=[],
                    error="cancelled",
                )
            result = chat_task.result()
        except OperationCancelled:
            return SubagentResult(
                success=False,
                summary="子代理已取消",
                refs=[],
                error="cancelled",
            )
        except asyncio.TimeoutError:
            budget_used = budget.usage()
            logger.warning(
                "[Subagent] parent=%s wall-time budget exceeded: %s",
                self.parent_session_id, budget_used,
            )
            return SubagentResult(
                success=False,
                summary=f"子代理超过墙钟预算（{budget.max_wall_time_s:.0f}s）被终止",
                refs=[],
                error="budget_exceeded:wall_time",
            )
        except BudgetExceeded as e:
            # review R2 BLOCKER 修复：BudgetExceeded 现为 BaseException 派生
            # （保证穿透引擎/管道的 except Exception 兜底）—— 本处理器必须
            # 显式捕获，否则预算信号会沿 spawn_subagent → 父管道 → 父波执行
            # 一路上抛，炸掉整个父 turn。此处是预算信号的终点：诚实失败。
            logger.warning(
                "[Subagent] parent=%s tool budget exceeded: %s",
                self.parent_session_id, e,
            )
            return SubagentResult(
                success=False,
                summary=f"子代理超过工具预算被终止: {e}",
                refs=[],
                error="budget_exceeded:tools",
            )
        except Exception as e:
            # #685: 非流式诚实 settle 后 chat() 会抛异常（empty / max_rounds / no_progress）
            # 这里统一判失败，不再假成功；summary 保留失败原因以便父循环决策。
            # refs=None（无法可靠计算 after-set）；父循环按无新增 refs 处理。
            logger.exception("[Subagent] sub-engine failed")
            return SubagentResult(
                success=False,
                error=str(e),
                summary=f"子代理执行失败: {e}",
                refs=[],
            )

        # #685: 消费 chat() 的真实 outcome（success 仅当 content 非空且 task 未失败）
        # 失败轮（empty / max_rounds / no_progress）即使 result 含 content 也判 success=False，
        # 避免 subagent 空 summary 汇报成功（ticket #685）
        summary = (result.get("content") or "").strip()
        reasoning = (result.get("reasoning") or "").strip()

        # 诚实成功判定：content 非空才算 success，否则为 failed（与非流式 CORRECTNESS-4 对齐）
        # 注意：若 chat() 在 empty/max_rounds/no_progress 路径已抛异常，上方 except 已返回 False
        # 取消语义现在由令牌竞速显式判定（error="cancelled"），不再靠文案字符串猜测。
        _honest_success = bool(summary) and not sub_token.cancelled

        # 子任务结束后新增的 refs
        try:
            refs_after = set((await session_data_manager.list_refs(self.parent_session_id)).keys())
        except Exception:
            refs_after = set()
        new_refs = sorted(refs_after - refs_before)

        if not _honest_success:
            return SubagentResult(
                success=False,
                summary=summary or "子代理未返回有效内容（empty completion / no_progress / max_rounds）",
                reasoning=reasoning,
                refs=new_refs,
                error="subagent empty or failed turn",
            )

        return SubagentResult(
            success=True,
            summary=summary,
            reasoning=reasoning,
            refs=new_refs,
        )

    # ─── 并行委派（ADR-0104 决策 7）──────────────────────────

    async def run_parallel(
        self,
        specs: Sequence[SubagentTaskSpec],
        *,
        parent_budget: Optional["SubagentBudget"] = None,
        concurrency: int = SUBAGENT_PARALLEL_CONCURRENCY,
    ) -> SubagentBatchResult:
        """有界并行委派：信号量上限 2、父预算 roll-up、失败隔离、诚实 settle。

        - kill switch：``GIS_SUBAGENT_PARALLEL=0`` → 诚实拒绝（不 spawn 任何
          子代理）；省略本方法的旧调用路径不受影响。
        - fail-closed 预检：空列表 / 超过 SUBAGENT_MAX_PARALLEL_CHILDREN /
          任何 spec 的 role 非法 → 直接 ValueError，不启动任何子代理。
        - 失败隔离：gather(return_exceptions=True)，单个子代理失败/异常不
          取消兄弟；每个子代理给诚实的 SubagentResult。
        - 取消传播（协作式）：每个子代理启动前（拿到信号量槽位后）检查
          当前取消令牌与既有任务注册表（cancellation.registry.is_cancelled），
          已取消则剩余子代理不再启动、逐个返回 cancelled 诚实结果。
        - 诚实 settle：completed / partial（任一子代理失败）/ failed。
        """
        if os.getenv(_SUBAGENT_PARALLEL_KILL_SWITCH_ENV, "1").lower() in _KILL_SWITCH_OFF_VALUES:
            return SubagentBatchResult(
                status="failed",
                results=[],
                error="subagent_parallel_disabled (GIS_SUBAGENT_PARALLEL=0)",
            )
        if not specs:
            raise ValueError("run_parallel 需要至少一个 SubagentTaskSpec")
        if len(specs) > SUBAGENT_MAX_PARALLEL_CHILDREN:
            raise ValueError(
                f"并行子代理数量 {len(specs)} 超过上限 {SUBAGENT_MAX_PARALLEL_CHILDREN}"
            )
        for i, spec in enumerate(specs):
            if not spec.task or not spec.task.strip():
                raise ValueError(f"specs[{i}].task 不能为空")
            if spec.role is not None:
                # fail-closed：任何一个未知角色都不允许启动任何子代理
                from app.services.subagent_roles import get_subagent_role
                get_subagent_role(spec.role)

        from app.lib.cancellation import (
            OperationCancelled,
            current_token as _current_cancel_token,
            registry as _cancel_registry,
        )
        from app.services.subagent_roles import BudgetExceeded

        sem = asyncio.Semaphore(max(1, min(concurrency, SUBAGENT_PARALLEL_CONCURRENCY)))

        def _cancelled_reason() -> Optional[str]:
            tok = _current_cancel_token()
            if tok is None:
                return None
            if tok.cancelled:
                return tok.reason or "parent turn cancelled"
            job_id = getattr(tok, "job_id", None)
            if job_id is not None and _cancel_registry.is_cancelled(job_id):
                return "task cancelled (registry)"
            return None

        async def _one(spec: SubagentTaskSpec) -> SubagentResult:
            reason = _cancelled_reason()
            if reason:
                return SubagentResult(
                    success=False, summary=f"子代理已取消: {reason}", error="cancelled",
                )
            async with sem:
                # 子代理之间的协作式取消检查点：拿到槽位后再查一次
                reason = _cancelled_reason()
                if reason:
                    return SubagentResult(
                        success=False,
                        summary=f"子代理已取消: {reason}",
                        error="cancelled",
                    )
                try:
                    return await self.run(
                        task=spec.task,
                        domains=list(spec.domains) if spec.domains else None,
                        extra_tools=list(spec.extra_tools) if spec.extra_tools else None,
                        max_rounds=spec.max_rounds,
                        role=spec.role,
                        budget_overlay=parent_budget,
                    )
                except BudgetExceeded as e:
                    # 防御：run 内已把预算信号 settle 成诚实结果；这里兜住
                    # 父预算 roll-up 信号的任何漏网路径。
                    return SubagentResult(
                        success=False,
                        summary=f"子代理超过预算被终止: {e}",
                        error="budget_exceeded:tools",
                    )
                except OperationCancelled:
                    return SubagentResult(
                        success=False, summary="子代理已取消", error="cancelled",
                    )
                except BaseException as e:  # noqa: BLE001 — 单子代理失败绝不炸兄弟
                    if isinstance(e, asyncio.CancelledError):
                        raise
                    return SubagentResult(
                        success=False,
                        summary=f"子代理执行失败: {e}",
                        error=type(e).__name__,
                    )

        raw = await asyncio.gather(
            *(_one(s) for s in specs), return_exceptions=True,
        )
        results: List[SubagentResult] = []
        for item in raw:
            if isinstance(item, BaseException):
                # 最终兜底：任何漏网异常 → 该子代理诚实失败（兄弟已不受影响）
                results.append(SubagentResult(
                    success=False,
                    summary=f"子代理执行失败: {item}",
                    error=type(item).__name__,
                ))
            else:
                results.append(item)
        ok_count = sum(1 for r in results if r.success)
        if ok_count == len(results):
            status = "completed"
        elif ok_count == 0:
            status = "failed"
        else:
            status = "partial"
        return SubagentBatchResult(
            status=status,
            results=results,
            budget_usage=parent_budget.usage() if parent_budget is not None else {},
        )

    # ─── helpers ────────────────────────────────────────────

    def _build_sub_engine(self, tool_subset: list[dict], max_rounds: int) -> "ChatEngine":
        """造一个轻量 ChatEngine：用同一份 registry，但通过 catalog stub 把
        工具白名单固定为 tool_subset（绕过域关键词匹配）。

        review R2 MAJOR-8：catalog stub 只限制模型**看到**的工具 —— 被注入
        的子代理 LLM 仍可直接点名隐藏工具并经共享 registry 执行。这里包一层
        **dispatch 成员校验代理**：白名单之外的名字在 dispatch 边界拒绝
        （honest error，不执行），把「只可见」升级为「只可执行」。"""

        from app.services.chat_engine import ChatEngine

        allowed_names = {s["function"]["name"] for s in tool_subset}

        class _AllowlistedRegistry:
            """dispatch 边界成员校验（narrowing-only：一切委托真 registry）。"""

            def __init__(self, inner, allowed):
                self._inner = inner
                self._allowed = allowed

            def dispatch(self, tool_name, *args, **kwargs):
                if tool_name not in self._allowed:
                    return {
                        "success": False,
                        "error": (
                            f"工具 {tool_name} 不在本子代理的授权工具面内 "
                            "(subagent tool allowlist)"
                        ),
                        "code": "TOOL_NOT_ALLOWLISTED",
                    }
                return self._inner.dispatch(tool_name, *args, **kwargs)

            def get_schemas(self, *args, **kwargs):
                return self._inner.get_schemas_subset(self._allowed)

            def __getattr__(self, item):
                return getattr(self._inner, item)

        class _FrozenCatalog:
            """只返回 tool_subset 的 catalog stub，禁用粘性 / 关键词匹配。

            P2-7（Pi#1/#2）：引擎在 _maybe_plan / _log_tool_decision 里会调
            ``reset_sticky`` / ``active_domains`` —— stub 必须提供 no-op 实现，
            否则子代理轮次直接 AttributeError 崩溃。
            """

            def __init__(self, schemas: list[dict]):
                self._schemas = schemas

            def select_schemas(
                self,
                _user_message: str,
                session_id: Optional[str] = None,
                declared_domains: Optional[set] = None,
                turn_id: Optional[str] = None,
            ):
                # declared_domains / turn_id 是 ToolCatalog 现行接口（_select_tools
                # 始终传这两个 kwarg，#684 加入 turn_id 按用户轮衰减）；stub 固定
                # 返回白名单，忽略即可。
                return self._schemas

            def reset_session(self, session_id: str) -> None:
                return

            def reset_sticky(self, session_id: str) -> None:
                return

            def active_domains(self, session_id: Optional[str]) -> set:
                return set()

        engine = ChatEngine(
            _AllowlistedRegistry(self.registry, allowed_names),
            tool_catalog=_FrozenCatalog(tool_subset),
            is_subagent_engine=True,
        )
        engine.max_rounds = max_rounds
        return engine
