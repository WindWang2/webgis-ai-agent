"""Chat Context Assembler — Deep module for prompt context composition.

Encapsulates map state ambient summaries, history token budget management,
XML security fencing, and execution plan blocks behind a unified assembly seam.

The project-context block (active_project_workspace) is rendered through
``ProjectContextCache`` so that:

- a chat turn that touches the same project across many LLM rounds
  reads the project fingerprint exactly once per round (1 query) and
  serves the rendered block from the LRU on every other round
  (0 additional queries);
- a mutation on the project / dataset / workflow bumps at least one
  component of the fingerprint, so the next round misses the cache
  and rebuilds the block (5 queries) — no stale data can leak;
- a no-project path is unchanged (zero extra queries).

See ``app/services/chat/project_context_cache.py`` for the cache
contract and ``.planning/2026-08-13-context-assembly-perf/findings.md``
for the design rationale.
"""
from dataclasses import dataclass
import asyncio
import logging
from typing import Callable, List, Optional

from app.services.session_data import session_data_manager
from app.services.session_data_protocol import SessionStoreProtocol

logger = logging.getLogger(__name__)


# Module-level SessionLocal override for tests. The default delegates
# to ``app.core.database.SessionLocal`` (the production sync engine);
# tests inject a sessionmaker bound to an in-memory SQLite engine so
# the cache + slim-summary path can be exercised without a real
# Postgres.
_session_local_factory: Optional[Callable] = None


def _get_session_local() -> Callable:
    """Return the sync session factory, honoring the test override."""
    if _session_local_factory is not None:
        return _session_local_factory
    from app.core.database import SessionLocal
    return SessionLocal


def set_session_local_factory(factory: Optional[Callable]) -> None:
    """Override the sync session factory (tests). Pass ``None`` to reset."""
    global _session_local_factory
    _session_local_factory = factory


def _build_project_context_block(
    project_id: str,
    user_id: Optional[str] = None,
    org_id: Optional[int] = None,
) -> Optional[str]:
    """Sync DB read of the active project workspace (runs OFF the event loop).

    Reads the project fingerprint (1 query) and, on a miss, the full
    ``ProjectContextSummary`` (5 queries) — a strict reduction from
    the previous 10 queries per round.

    The fingerprint + summary pair is fed to ``ProjectContextCache``:

    - On a hit, the cached rendered text is returned and *no* further
      SQL is issued.
    - On a miss, the freshly rendered text is stored in the cache
      under ``(project_id, fingerprint.cache_key())``.

    A ``None`` return means the project is missing or the caller is
    not authorised; the cache deliberately does not store this
    outcome so a re-creation or auth grant is picked up immediately.
    """
    from app.services.project_service import ProjectService
    from app.services.chat.project_context_cache import project_context_cache

    SessionLocal = _get_session_local()
    with SessionLocal() as db:
        # 1. Read just the fingerprint (3 cheap aggregate queries: auth
        # + dataset aggregate + workflow aggregate). The auth check is
        # folded into step 1 — a missing/unauthorised project returns
        # None here, which the cache treats as a deliberate miss.
        fingerprint = ProjectService.get_project_fingerprint(
            db, project_id, user_id=user_id, org_id=org_id,
        )
        if fingerprint is None:
            return None

        # 2. Cache lookup under (project_id, fingerprint.cache_key()).
        cached = project_context_cache.lookup(project_id, fingerprint.cache_key())
        if cached is not None:
            return cached

        # 3. Cache miss: pay the full summary read (5 queries). The
        # summary is then stored under the same key, so the next round
        # pays only the 1-query fingerprint cost.
        summary = ProjectService.get_project_context_summary(
            db, project_id, user_id=user_id, org_id=org_id,
        )
        if summary is None:
            # The fingerprint path saw the project but the summary
            # path did not — race with a delete. Treat as a miss;
            # do not cache.
            return None
        project_context_cache.store(project_id, summary)
        return summary.render()


def _build_project_memory_block(project_id: str) -> str:
    """Sync DB read of the project's cartographic memory (runs OFF the event loop).

    ADR-0069：注入的是**先验而非证据**——只影响下一张图的起点（共享分类
    方案/偏好/recipe 成效），永不参与 verdict 计算。只读 ``active`` 事实
    （``stale``/``conflicted`` 一律不注入：注入一个自己都不确定的先验比
    不注入更坏）。账本不可用时返回空串，行为退化为无记忆。

    spec P3：待告知的环境变化（分布漂移致先验过期）前置于记忆块——先说
    "世界变了"，再给剩下仍然可信的先验。事件读取即消费，不会每个 turn 重复。
    """
    from app.services.cartography.distribution_drift import render_env_change_block
    from app.services.cartography.project_memory import (
        get_active_facts,
        get_pending_env_changes,
        render_memory_block,
    )

    SessionLocal = _get_session_local()
    try:
        with SessionLocal() as db:
            events = get_pending_env_changes(db, project_id)
            if events:
                db.commit()
            facts = get_active_facts(db, project_id)
            return render_env_change_block(events) + render_memory_block(facts)
    except Exception as ex:  # noqa: BLE001 — 记忆是增值上下文，失败不阻断对话
        logger.warning(
            "Cartographic project memory unavailable for %s: %s", project_id, ex
        )
        return ""


@dataclass(frozen=True)
class ContextAssemblyResult:
    """Structured result of prompt context assembly with observability metrics."""

    messages: List[dict]
    estimated_tokens: int
    history_turns_included: int
    layer_count: int
    # ADR-0101 Wave 5：模型感知预算度量（可观测；不改变组装/截断行为）。
    # None = 调用方未提供 context_window（无法规划）；dict = BudgetReport.as_dict()。
    budget_report: Optional[dict] = None

    def to_messages(self) -> List[dict]:
        """Return the raw OpenAI-compatible message dict list."""
        return self.messages


class ChatContextAssembler:
    """Deep context assembly engine for chat engine prompt composition."""

    def __init__(self, store: Optional[SessionStoreProtocol] = None) -> None:
        self.store = store or session_data_manager

    @staticmethod
    async def _build_cartography_verdict_block(
        session_id: str, map_state: dict,
    ) -> str:
        """#788: bounded ``[CARTOGRAPHY_VERDICT]`` block for the legacy path.

        Read-only composition mirroring the Pi route's
        ``_build_cartography_turn_context``: the stored ``_cartographic_review``
        is rendered behind ``should_inject_verdict`` (fingerprint must match the
        CURRENT MapSpec generation) — a pass renders only the #657 micro-token.
        Any failure degrades to no injection (the verdict is additive context;
        it must never break turn composition). Called only when a stored review
        exists, so the mapspec fingerprint read costs nothing otherwise.
        """
        review = map_state.get("_cartographic_review") if isinstance(map_state, dict) else None
        if not isinstance(review, dict):
            return ""
        try:
            from app.lib.cartography.quality_loop import cartographic_fingerprint
            from app.lib.cartography.verdict_summary import (
                render_verdict_for_llm,
                should_inject_verdict,
            )
            from app.services.mapspec.store import mapspec_store_instance

            mapspec = await mapspec_store_instance.get_mapspec(session_id)
            current_fingerprint = (
                cartographic_fingerprint(mapspec) if isinstance(mapspec, dict) else None
            )
            if not should_inject_verdict(review, current_fingerprint):
                return ""
            return render_verdict_for_llm(review)
        except Exception as ex:  # noqa: BLE001 — 注入是增值上下文，失败不阻断对话
            logger.warning(
                "Cartography verdict block unavailable for %s: %s", session_id, ex
            )
            return ""

    async def assemble(
        self,
        session_id: str,
        messages: List[dict],
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
        tools_payload_chars: int = 0,
        tools_payload: Optional[str] = None,
        include_plan_block: bool = True,
    ) -> ContextAssemblyResult:
        """
        Assemble the complete LLM request message list from session state,
        ambient environment summary, execution plan, and history token budget.

        ``project_id`` is an optional override: when set, it is used in
        preference to ``metadata.get("project_id")``. The chat engine
        forwards the active project's id when it has one (the session
        metadata store does not yet persist ``project_id``, so this
        override is the only way the assembler can currently learn
        about the active project — see also the
        ``get_session_metadata`` path which is still a no-op for
        ``project_id``).

        ``tools_payload``（P3 #3，优先）：调用方把已选出的工具 schema 序列化
        JSON（``json.dumps(..., ensure_ascii=False)``）传入，按与历史压缩相同的
        ``_estimate_tokens`` 权重软计入 estimated_tokens（CJK 1 char ≈ 1.5
        tokens、ASCII 4 char ≈ 1 token——不再用 ASCII-heavy 近似）。只影响估算，
        不改变任何截断行为。

        ``tools_payload_chars``（兼容旧调用——测试/benchmark 传字符数）：仅在
        ``tools_payload`` 未提供时使用 chars/4 近似，语义不变。

        ``include_plan_block``（#436）：False 时跳过活跃计划块的注入。子代理
        引擎复用父 session_id，若不关闭会把**父会话**的活跃计划（含"未完成
        步骤"警告、子代理未必可见的工具族提示）注入子代理上下文 —— 计划推进
        属于主代理（输出侧 #407 已隔离），输入侧同样不得继承。主引擎保持
        默认 True，行为不变。
        """
        if not messages:
            return ContextAssemblyResult(
                messages=[], estimated_tokens=0, history_turns_included=0, layer_count=0
            )

        from app.services.chat.context_builder import (
            build_last_analysis_context,
            build_map_state_summary,
            truncate_history_by_budget,
            fold_intra_turn_tool_results,
            _build_truncation_notice,
        )
        from app.services.chat.context.session_overview import build_session_overview
        from app.services.chat.context.history_compression import _estimate_tokens

        # Extract session metadata & map state
        store = self.store
        # Resolved below from the metadata store when available; the
        # no-metadata branch keeps the caller's explicit override only, so
        # the memory block below never reads an unbound name.
        effective_project_id = project_id
        if hasattr(store, "get_session_metadata"):
            metadata = await store.get_session_metadata(session_id)
            map_state = metadata.get("map_state") or {}
            list_refs = metadata.get("list_refs") or {}
            event_log = metadata.get("event_log") or []
            started_at = metadata.get("started_at")
            # RUN-04 / PERF-08: get_session_metadata already fetched map_state +
            # list_refs + event_log in one pipeline. Pass them into
            # build_map_state_summary with _fetched=True so it reuses them
            # instead of issuing redundant Redis/L1 reads every chat round.
            env_summary = await build_map_state_summary(
                session_id,
                state=map_state,
                inventory=list_refs,
                event_log=event_log,
                _fetched=True,
            )

            # Prefer the explicit override; fall back to the session
            # metadata. Both are independently optional — a session
            # without an active project is the common case today and
            # must remain zero-query.
            effective_project_id = project_id or metadata.get("project_id")
            if effective_project_id:
                # C-F4: offload the sync Postgres reads to a worker
                # thread so they no longer block the event loop every
                # LLM round. The body now goes through
                # ``ProjectContextCache``: a hit costs 1 fingerprint
                # query, a miss costs 5; multi-round same-project
                # turns pay 1 per round after the first.
                try:
                    project_block = await asyncio.to_thread(
                        _build_project_context_block,
                        effective_project_id,
                        user_id,
                        org_id,
                    )
                    if project_block:
                        env_summary += project_block
                except Exception as ex:
                    logger.warning(f"Failed to assemble project context block: {ex}")

            overview = await build_session_overview(
                session_id,
                messages,
                started_at=started_at,
                event_log=event_log,
                inventory=list_refs,
                _fetched=True,
            )
        else:
            env_summary = await build_map_state_summary(session_id)
            overview = await build_session_overview(session_id, messages)
            map_state = {}

        if overview:
            env_summary += f"\n- 会话概览: {overview}"

        sys_msg = dict(messages[0])
        sys_msg["content"] = sys_msg.get("content", "") + "\n\n" + env_summary

        head = [sys_msg]

        from app.services.chat.context_policy import policy_enabled
        _policy_on = policy_enabled()
        # ADR-0104 #6：head 块登记表（name/category/base_chars），供策略执行器
        # 把 advisor 的 CONDENSE 建议落到具体块上。与 head 一一对应。
        # base_chars = messages[0] 原始 system 提示的长度 + 分隔符 —— [环境感知]
        # 尾部（MAP_STATE）可在 advisor 判超分区时被有界压缩；核心指令不可触碰。
        head_meta: List[dict] = [{
            "name": "map_state_env",
            "category": "MAP_STATE",
            "base_chars": max(0, len(sys_msg["content"]) - len(env_summary) - 2),
        }]

        from app.services.chat.planner import get_plan

        plan = get_plan(session_id)
        if plan is not None and include_plan_block:
            # design-v3 §4：计划块单一渲染来源（plan_orchestrator.render_plan_block）。
            from app.services.chat.plan_orchestrator import render_plan_block
            head.append({"role": "system", "content": render_plan_block(plan)})
            head_meta.append({"name": "session_plan", "category": "SESSION_PLAN"})

        # #788 (F-A-8): the [CARTOGRAPHY_VERDICT] turn-start injection used to
        # be Pi-only (chat route builds it in the _use_pi_bridge() branches),
        # so a legacy turn followed a failed/repairable cartography generation
        # with zero knowledge of it. Inject the same bounded verdict block
        # here — the seam where the legacy path composes its request messages.
        # Gated by include_plan_block like the plan block: the verdict is the
        # main agent's session-level corrective context, not a subagent's
        # (#436 isolation rationale applies identically).
        if include_plan_block:
            verdict_block = await self._build_cartography_verdict_block(
                session_id, map_state
            )
            if verdict_block:
                head.append({"role": "system", "content": verdict_block})
                head_meta.append(
                    {"name": "cartography_verdict", "category": "CARTOGRAPHY_METADATA"}
                )

            # ADR-0069 (spec P2): the project's cartographic memory block sits
            # beside the verdict — the verdict is THIS session's corrective
            # evidence, the memory is the project's confirmed priors (shared
            # classification / preferences / recipe outcomes). Same gating as
            # the verdict: main agent only, additive, never blocking. Only
            # emitted when a project context exists (no project → zero query).
            if effective_project_id:
                try:
                    memory_block = await asyncio.to_thread(
                        _build_project_memory_block, effective_project_id
                    )
                    if memory_block:
                        head.append({"role": "system", "content": memory_block})
                        head_meta.append(
                            {"name": "project_memory", "category": "DATA_PROFILE"}
                        )
                except Exception as ex:  # noqa: BLE001
                    logger.warning(f"Failed to assemble project memory block: {ex}")

        last_ctx = build_last_analysis_context(messages)
        if last_ctx:
            head.append({"role": "system", "content": last_ctx})
            head_meta.append({"name": "last_analysis", "category": "TRACE_SUMMARIES"})

        # audit4 #980: 先折叠当前回合内较早的 tool 结果（仅影响发送视图，
        # 不落库），再按跨轮预算截断 —— min_turns 豁免对轮内增长无效，60 轮
        # 工具循环的回合此前每轮全量重发。
        #
        # ADR-0104 #6：策略开启时历史管线升级为 KEEP pin 感知（安全事实永不
        # 被折叠/丢弃），被丢弃轮次附指纹化确定性摘要（SUMMARIZE）；
        # GIS_CONTEXT_POLICY=0 时走原路径，逐字节不变。
        _dropped_messages: List[dict] = []
        _history_ops_summary: Optional[dict] = None
        if _policy_on:
            # review Round-1 MAJOR：策略路径与 legacy 同级的 never-break 纪律
            # —— 任何策略内部异常都回退 legacy 折叠/截断，绝不失败整个装配。
            try:
                from app.services.chat.context_policy import run_history_ops
                _hist = run_history_ops(messages[1:])
                # run_history_ops 已完成 pin 感知的折叠 + DROP_OLDEST + SUMMARIZE。
                history = _hist["kept"]
                dropped = _hist["dropped_turns"]
                _dropped_messages = _hist["dropped_messages"]
                _history_ops_summary = {
                    "pinned_messages": _hist["pinned_count"],
                    "folded_results": _hist["folded_count"],
                    "dropped_turns": _hist["dropped_turns"],
                    "summarized": bool(_hist["dropped_turns"] > 0),
                    "ops": _hist["ops"],
                }
            except Exception:  # noqa: BLE001 — 策略失败回退 legacy 管线
                logger.warning(
                    "[ContextPolicy] run_history_ops failed — legacy fallback",
                    exc_info=True,
                )
                foldable = fold_intra_turn_tool_results(messages[1:])
                history, dropped = truncate_history_by_budget(foldable)
        else:
            foldable = fold_intra_turn_tool_results(messages[1:])
            history, dropped = truncate_history_by_budget(foldable)
        if dropped > 0:
            if _policy_on:
                try:
                    from app.services.chat.context_policy import (
                        build_truncation_notice_with_summary,
                    )
                    notice_content = build_truncation_notice_with_summary(
                        dropped, _dropped_messages
                    )
                except Exception:  # noqa: BLE001 — 回退旧 notice
                    notice_content = _build_truncation_notice(dropped)
            else:
                notice_content = _build_truncation_notice(dropped)
            head.append({"role": "system", "content": notice_content})
            logger.info(f"[HISTORY-TRUNC] session={session_id} dropped {dropped} turns")
        head.extend(history)
        history_start = len(head) - len(history)
        if _policy_on:
            # RELOAD_REF tombstone：组装产物引用已逐出 ref 时追加有界诚实提示
            # （可从持久副本重载 vs 已失效），而非沉默。不占 head_meta（披露
            # 块不可压缩）。
            try:
                from app.services.chat.context_policy import build_evicted_refs_tombstone
                tombstone = await build_evicted_refs_tombstone(session_id, head, store)
                if tombstone:
                    head.append({"role": "system", "content": tombstone})
            except Exception:  # noqa: BLE001 — tombstone 是增值披露
                logger.debug(
                    "[ContextPolicy] evicted-refs tombstone failed", exc_info=True
                )

        layers = map_state.get("layers", {}) if isinstance(map_state, dict) else {}
        layer_count = len(layers) if isinstance(layers, dict) else 0

        # Calculate estimated total tokens across assembled messages
        total_tokens = sum(
            _estimate_tokens(str(m.get("content", ""))) for m in head
        )
        if tools_payload:
            # P3 #3：CJK-aware —— 与历史压缩同一权重（CJK 1 char ≈ 1.5 tokens、
            # ASCII 4 char ≈ 1 token）。软计入，只影响估算与可观测性，不改变
            # 任何截断行为。工具 schema 里中文描述（描述/参数说明）越多，估算
            # 越接近真实 token 数。
            total_tokens += _estimate_tokens(tools_payload)
        elif tools_payload_chars and tools_payload_chars > 0:
            # 兼容旧调用（测试/benchmark 只传字符数）：ASCII-heavy 近似。
            total_tokens += int(tools_payload_chars / 4) + 1

        # ADR-0101 Wave 5：模型感知预算度量（加观测不加行为 —— 截断权仍在
        # 既有组件；超预算以 violations 留痕，供 trace/debug bundle 消费）。
        # ADR-0104 #6：策略开启时 advisor 的建议在此被真实执行（本模块只是
        # 编排；建议源仍是 GisBudgetAdvisor，执行器是 context_policy）。
        budget_report: Optional[dict] = None
        try:
            from app.core.config import settings as _settings
            from app.services.chat.context_budget import (
                measure_assembled_context,
            )

            _window = getattr(_settings, "LLM_CONTEXT_WINDOW", None)
            _max_output = _settings.LLM_MAX_TOKENS

            # ADR-0104 #6：先执行建议（CONDENSE/OFFLOAD_REF 落刀），再对执行后
            # 的最终产物做权威度量 —— budget_report 如实反映模型实际看到的形态。
            _policy_report: Optional[dict] = (
                {"enabled": True, "history_ops": _history_ops_summary}
                if _history_ops_summary is not None else None
            )
            if _policy_on:
                try:
                    from app.services.chat.context_budget import (
                        GisBudgetAdvisor,
                        plan_budget as _plan_budget,
                    )
                    from app.services.chat.context_policy import (
                        build_policy_items,
                        execute_advice,
                    )
                    from app.services.chat.context.history_compression import (
                        _estimate_tokens as _est,
                    )

                    _plan = _plan_budget(
                        context_window=_window or 0, max_output_tokens=_max_output,
                    )
                    _tools_tokens = _est(tools_payload) if tools_payload else 0
                    _items = build_policy_items(head, head_meta, tools_payload or "")
                    _advice = GisBudgetAdvisor(_plan).advise(_items)
                    _exec_report = await execute_advice(
                        head, head_meta,
                        advice_actions=_advice.actions,
                        window_known=_plan.window_known,
                        usable_tokens=_plan.usable,
                        session_id=session_id,
                        store=store,
                        tools_tokens=_tools_tokens,
                        history_start=history_start,
                    )
                    if _policy_report is None:
                        _policy_report = {"enabled": True}
                    _policy_report.update(_exec_report)
                    _policy_report.setdefault("history_ops", _history_ops_summary)
                except Exception as ex:  # noqa: BLE001 — 策略执行绝不阻断组装
                    logger.warning(f"[CONTEXT-POLICY] execution failed: {ex}")

            _report = measure_assembled_context(
                head,
                tools_payload=tools_payload or "",
                context_window=_window,
                max_output_tokens=_max_output,
            )
            budget_report = _report.as_dict()
            if _policy_report is not None:
                budget_report["context_policy"] = _policy_report
            # ADR-0103 V2：GIS-aware 处置建议（执行后的余量 —— 已执行的动作见
            # budget_report["context_policy"]；策略关闭时保持 V2 原语义）。
            try:
                from app.services.chat.context_budget import (
                    BudgetItem,
                    Category,
                    GisBudgetAdvisor,
                    plan_budget as _plan_budget,
                )

                _plan = _plan_budget(
                    context_window=_window or 0,
                    max_output_tokens=_settings.LLM_MAX_TOKENS,
                )
                if _policy_on:
                    from app.services.chat.context_policy import build_policy_items
                    _items = build_policy_items(head, head_meta, tools_payload or "")
                else:
                    _items = [
                        BudgetItem(
                            category=Category.HISTORY, name="history",
                            est_tokens=_report.by_category.get("HISTORY", 0),
                            hard_limit_tokens=6000,
                        ),
                        BudgetItem(
                            category=Category.TOOL_SCHEMAS, name="tool_schemas",
                            est_tokens=_report.by_category.get("TOOL_SCHEMAS", 0),
                        ),
                        BudgetItem(
                            category=Category.SYSTEM_INSTRUCTIONS, name="system",
                            est_tokens=_report.by_category.get("SYSTEM_INSTRUCTIONS", 0),
                        ),
                        BudgetItem(
                            category=Category.USER_PROMPT, name="user_final",
                            est_tokens=_report.by_category.get("USER_PROMPT", 0),
                        ),
                    ]
                _advice = GisBudgetAdvisor(_plan).advise(_items)
                budget_report["gis_advice"] = _advice.as_dict()
            except Exception:  # noqa: BLE001 — 建议绝不阻断组装
                pass
            if _report.over_budget:
                logger.warning(
                    "[CONTEXT-BUDGET] session=%s over budget: %s",
                    session_id, _report.violations,
                )
        except Exception:  # noqa: BLE001 — 预算度量绝不阻断组装
            budget_report = None

        return ContextAssemblyResult(
            messages=head,
            estimated_tokens=total_tokens,
            history_turns_included=len(history),
            layer_count=layer_count,
            budget_report=budget_report,
        )


__all__ = [
    "ChatContextAssembler",
    "ContextAssemblyResult",
    "set_session_local_factory",
]
