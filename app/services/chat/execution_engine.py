"""ChatExecutionEngine - 核心 Agent 对话与流式响应执行引擎。

深入封装会话状态 LRU 缓存、按 session_id 并发锁、LLM Token 流式推送、
工具循环重试、上下文组装及 SSE 事件格式化。
"""
import asyncio
import json
import logging
import re
import time
import uuid
from typing import AsyncGenerator, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.tool_catalog import ToolCatalog

from app.core.config import settings
from app.tools.registry import ToolRegistry
from app.services.task_tracker import TaskStatus, TaskTracker
from app.services.session_data import session_data_manager
from app.services.ws_service import broadcast_ws_event
from app.tools._utils import async_db_session
from app.services.history_service_async import AsyncHistoryService
from app.utils.sse import sse_event

from app.services.chat.llm_client import (
    LLMConfig,
    LRUCache,
    call_llm,
    call_llm_stream,
    parse_minimax_xml_tool_calls as _parse_minimax_xml_tool_calls,
)
from app.services.chat.prompt import (
    SYSTEM_PROMPT,
    construct_self_healing_message as _construct_self_healing_message,
)
from app.services.chat.context_builder import (
    build_map_state_summary as _build_map_state_summary,
    format_layer_lines as _format_layer_lines,
    build_last_analysis_context as _build_last_analysis_context,
)
from app.services.tool_dispatch_service import (
    ToolDispatchResult,
    ToolDispatchService,
    is_suspicious_result as _is_suspicious_result_fn,
)
from app.services.chat.tool_pipeline import ToolExecutionPipeline, ToolExecutionResult
from app.lib.runtime import context as rt_ctx
from app.lib.runtime.evidence import (
    Outcome,
    TurnEvidence,
    TURN_EVIDENCE,
    bind_turn_evidence,
    current_turn_evidence,
    emit_turn_summary,
)

logger = logging.getLogger(__name__)


def _settle_cancel() -> None:
    """Mark the live turn's outcome CANCELLED on a cooperative cancel.

    The legacy round loops return a normal "任务已取消" dict on cancel (not an
    exception), so without this the outer turn would settle SUCCEEDED — the
    exact "error counted as success" failure this PR prevents. No-op when no
    turn evidence is bound (e.g. subagent / plan-only paths).
    """
    _ev = current_turn_evidence()
    if _ev is not None:
        _ev.settle(Outcome.CANCELLED)

# 计划自身 ref 的判定（P2-6 / recovery P2）：plan-mode 计划以 ref:plan-* 存，
# canonical 计划经别名 plan-current / plan-id:* 寻址。它们只代表"计划存在"，
# 不代表会话有可复用的数据 —— session_has_refs 必须把它们排除，否则
# "仅存了计划"的会话会被 ref_reuse 分类误判。
_PLAN_REF_PREFIX = "ref:plan-"
_PLAN_ALIAS_PREFIX = "plan-id:"
_PLAN_CURRENT_ALIAS = "plan-current"


def _has_non_plan_refs(refs: dict) -> bool:
    """list_refs() 结果里是否存在非计划数据引用（ref:plan-* / plan 别名除外）。"""
    for rid, alias in (refs or {}).items():
        rid_s = str(rid)
        alias_s = str(alias or "")
        if rid_s.startswith(_PLAN_REF_PREFIX):
            continue
        if alias_s == _PLAN_CURRENT_ALIAS or alias_s.startswith(_PLAN_ALIAS_PREFIX):
            continue
        return True
    return False


#: F9/F28: 非流式工具波被抢占式取消时，未完成任务的结果槽用哨兵占位 ——
#: 结果对齐循环据此跳过（不写成工具失败），孤儿 tool_call 由
#: _repair_orphaned_tool_calls 以「已取消」补齐。
_CANCELLED_TOOL = object()


class SessionClearingError(RuntimeError):
    """Raised when a new turn is started for a session that is mid-``clear_session``.

    P1: clear_session deletes the conversation rows and cancels the in-flight
    turn; a turn that starts in that window would race the delete — either
    writing into a deleted conversation (resurrecting rows) or being cancelled
    mid-start. Callers should surface this as a clean refusal, not retry into
    the race.
    """


#: F15: fire-and-forget 背景任务的进程内注册表 —— main.py lifespan shutdown
#: 通过 drain_background_tasks() 等待它们收尾，避免任务跨事件循环泄漏。
_background_tasks: set = set()


def _track_background_task(task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def drain_background_tasks(timeout: float = 5.0) -> None:
    """等待 fire-and-forget 背景任务收尾（lifespan shutdown 调用）。

    P1 round-2（原实现两个缺陷）：
      (a) 超时只是记日志返回 —— 慢 _generate_title 会在 lifespan 关闭共享
          client / dispose 引擎之后继续写死资源。现在超时后 cancel 剩余任务
          并短暂 gather（best-effort，try/except TimeoutError）。
      (b) 模块级注册表跨事件循环粘连 —— 绑定已关闭/其它 loop 的任务永不完成，
          之后每次 drain 都把它算进去吃满整个 timeout。现在只收集绑定当前
          running loop 的任务，陈旧条目（get_loop() 不是当前 loop）直接从
          注册表丢弃并记日志。
    cancellation-safe：等待期间调用方自己被取消时 CancelledError 正常传播。
    """
    loop = asyncio.get_running_loop()
    stale: list = []
    current: list = []
    for t in list(_background_tasks):
        if t.done():
            continue
        try:
            bound_loop = t.get_loop()
        except RuntimeError:
            bound_loop = None
        if bound_loop is not loop:
            stale.append(t)
        else:
            current.append(t)
    if stale:
        for t in stale:
            _background_tasks.discard(t)
        logger.warning(
            "drain_background_tasks: dropped %d stale background task(s) bound "
            "to a closed/different event loop; they can never complete here",
            len(stale),
        )
    if not current:
        return
    _done, pending = await asyncio.wait(current, timeout=timeout)
    if pending:
        logger.warning(
            "drain_background_tasks: %d background tasks still pending after "
            "%.1fs — cancelling them (best-effort)",
            len(pending), timeout,
        )
        for t in pending:
            t.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True), timeout=timeout
            )
        except asyncio.TimeoutError:
            pass


def _lock_in_use(lock: asyncio.Lock) -> bool:
    """锁被持有或有等待者时为 True —— 这样的锁不可逐出/丢弃（F1）。

    只看 ``locked()`` 有一个 check-then-pop 竞态：release() 唤醒等待者用的是
    call_soon，等待者还没重新拿到锁的窗口里 locked() 已经是 False。
    """
    if lock.locked():
        return True
    waiters = getattr(lock, "_waiters", None)
    return bool(waiters)


class ChatExecutionEngine:
    """Agent 对话与流式响应执行引擎"""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        tool_catalog: Optional["ToolCatalog"] = None,
        *,
        is_subagent_engine: bool = False,
    ):
        self.registry = tool_registry
        self.catalog = tool_catalog
        # P2-7（Pi#1/#2）：子代理引擎轮次绝不推进父会话的计划。SubagentDispatcher
        # 构造子引擎时传 is_subagent_engine=True；_maybe_plan / mark_step_done /
        # _flush_plan 据此跳过一切计划推进，避免子代理的工具调用把父会话的
        # 活跃计划打勾/改写。
        self.is_subagent_engine = is_subagent_engine
        self.base_url = settings.LLM_BASE_URL.rstrip("/")
        self.model = settings.LLM_MODEL
        self.api_key = settings.LLM_API_KEY
        self.use_prompt_caching = settings.LLM_PROMPT_CACHING_ENABLED
        self.max_rounds = 60
        self.tracker = TaskTracker()

        from app.services.chat.context_assembler import ChatContextAssembler
        self.context_assembler = ChatContextAssembler()

        # RUN-01 (deep-audit round 2): ONE long-lived ToolDispatchService shared
        # by every tool call in a turn. The previous code built a fresh service
        # (and thus a fresh asyncio.Lock) per _dispatch_tool call, so the
        # duplicate-tool dedup check-and-add was NOT atomic across the parallel
        # wave — two identical tool calls could both pass the `in` check and
        # both run. A single shared instance makes the lock effective.
        def _broadcast(sid: str, event_type: str, data: dict) -> None:
            self._fire_and_forget(broadcast_ws_event, sid, event_type, data)

        self.dispatch_service = ToolDispatchService(
            registry=self.registry, fire_broadcast=_broadcast
        )
        self.tool_pipeline = ToolExecutionPipeline(
            self.registry,
            self.tracker,
            # Late-bind the dispatch entry point: the pipeline task outlives
            # __init__, and freezing self._dispatch_tool as a bound method here
            # would silently bypass later overrides (test seams, subclass
            # overrides) — the pipeline would dispatch through the stale
            # original method. _pipeline_dispatch resolves _dispatch_tool at
            # call time; the shared ToolDispatchService (RUN-01) is preserved
            # because _dispatch_tool always routes to self.dispatch_service.
            dispatch_fn=self._pipeline_dispatch,
            dispatch_service=self.dispatch_service,
        )

        import os as _os
        _SESSION_CACHE_SIZE = int(_os.getenv("SESSION_CACHE_SIZE", "200"))
        self._sessions: LRUCache = LRUCache(capacity=_SESSION_CACHE_SIZE)
        # C-F12: the LRU above bounds the session COUNT; this bounds how many
        # messages one resident session may hold. Every append is persisted via
        # _save_msg_async, so the DB is the source of truth — the cache only
        # needs the recent tail (see _trim_session_tail).
        self._session_message_cap = max(1, int(_os.getenv("SESSION_MESSAGE_CAP", "200")))
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._MAX_LOCKS = 200
        self._session_owner_tokens: LRUCache = LRUCache(capacity=_SESSION_CACHE_SIZE)

        # P1: per-session 'clearing' marker + active-turn task registry.
        # clear_session sets the marker BEFORE deleting rows so the in-flight
        # turn's cleanup (repair + saves) cannot resurrect history; new turns
        # for a marked session are refused cleanly. The task registry lets
        # clear_session quiesce the cancelled turn (bounded) before clearing
        # the marker.
        self._clearing_sessions: set[str] = set()
        self._active_turn_tasks: dict[str, asyncio.Task] = {}
        self._clear_quiesce_timeout = float(_os.getenv("CLEAR_QUIESCE_TIMEOUT", "5.0"))
        # P1: bounded cancel-and-await budget for the cancel/finally cleanup
        # paths — a straggler tool that cannot be interrupted (worker thread
        # parked in long GIS compute) must not hold the session lock for the
        # worker's full duration; the bounded wait returns promptly and the
        # straggler's result is discarded.
        self._cancel_wait_timeout = float(_os.getenv("CANCEL_WAIT_TIMEOUT", "5.0"))

    def _select_tools(self, session_id: Optional[str], messages: list[dict]) -> Optional[list[dict]]:
        """选出本轮要推给 LLM 的工具 schema 列表。"""
        if self.catalog is not None:
            user_text = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    content = m.get("content")
                    if isinstance(content, str):
                        user_text = content
                    elif isinstance(content, list):
                        user_text = " ".join(
                            seg.get("text", "") for seg in content if isinstance(seg, dict)
                        )
                    break
            from app.services.chat import planner
            plan = planner.get_plan(session_id) if session_id else None
            declared = set(plan.domains) if plan and plan.domains else None
            schemas = self.catalog.select_schemas(
                user_text, session_id=session_id, declared_domains=declared,
            )
            return schemas or None
        all_schemas = self.registry.get_schemas()
        return all_schemas or None

    def _build_system_prompt(self) -> str:
        """动态构建带 Skill 列表的 System Prompt"""
        from app.tools.skills import list_md_skills
        skills = list_md_skills()
        if skills:
            lines = [f"- **{s['name']}**: {s['description']}" for s in skills]
            skill_text = "\n".join(lines)
        else:
            skill_text = "（暂无预置技能）"
        return SYSTEM_PROMPT.format(skill_list=skill_text)

    def update_config(self, base_url: str = None, model: str = None, api_key: str = None, use_prompt_caching: bool = None):
        """动态更新 LLM 配置"""
        if base_url:
            self.base_url = base_url.rstrip("/")
        if model:
            self.model = model
        if api_key:
            self.api_key = api_key
        if use_prompt_caching is not None:
            self.use_prompt_caching = use_prompt_caching
        logger.info(f"ChatExecutionEngine config updated: model={self.model}, base_url={self.base_url}")

    def get_config(self) -> dict:
        """获取当前配置"""
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key": "***" + self.api_key[-4:] if self.api_key else "",
            "use_prompt_caching": self.use_prompt_caching
        }

    def _fire_and_forget(self, func, *args, **kwargs):
        """异步执行背景任务"""
        import inspect
        if inspect.iscoroutinefunction(func):
            task = asyncio.create_task(func(*args, **kwargs))
            _track_background_task(task)  # F15
            task.add_done_callback(lambda t: (
                logger.error(f"Background async task failed: {t.exception()}")
                if not t.cancelled() and t.exception() else None
            ))
        else:
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(None, func, *args)
            future.add_done_callback(lambda f: (
                logger.error(f"Background sync task failed: {f.exception()}")
                if f.exception() else None
            ))
            _track_background_task(asyncio.wrap_future(future))  # F15

    def _db_msg_to_llm(self, msg) -> dict:
        """将 DB 消息转换成 LLM 格式 dict"""
        d = {"role": msg.role, "content": msg.content or ""}
        if msg.reasoning_content:
            d["reasoning_content"] = msg.reasoning_content
        if msg.tool_calls:
            try:
                d["tool_calls"] = msg.tool_calls if isinstance(msg.tool_calls, list) else json.loads(msg.tool_calls)
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Failed to parse tool_calls for message {msg.id}")
        if msg.tool_call_id:
            d["tool_call_id"] = msg.tool_call_id
        return d

    async def _load_session_from_db(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[list[dict]]:
        """从 DB 加载历史对话。失败返回 None（F23：调用方不得缓存失败 stub）。"""
        try:
            history_svc = AsyncHistoryService()
            ctx = await history_svc.load_context(
                session_id=session_id,
                user_id=user_id,
                system_prompt=self._build_system_prompt(),
            )
            if ctx.owner_token and session_id not in self._session_owner_tokens:
                self._session_owner_tokens[session_id] = ctx.owner_token
            return ctx.llm_messages
        except Exception as e:
            logger.warning(f"History: failed to load conversation {session_id}: {e}")
            return None

    def get_session_owner_token(self, session_id: str) -> Optional[str]:
        """取出并消费 session 的 owner_token（SEC-08）"""
        return self._session_owner_tokens.pop(session_id, None)

    async def _get_map_state_summary(self, session_id: str) -> str:
        return await _build_map_state_summary(session_id)

    @staticmethod
    def _format_layer_lines(inventory: dict, active_layers: list[dict]) -> list[str]:
        return _format_layer_lines(inventory, active_layers)

    async def _compose_request_messages(
        self,
        session_id: str,
        messages: list[dict],
        project_id: Optional[str] = None,
        tools: Optional[list] = None,
    ) -> list[dict]:
        tools_payload = None
        if tools:
            try:
                # P3 #3：把序列化后的工具 schema JSON 交给 assembler 做 CJK-aware
                # token 估算（_estimate_tokens，与历史压缩同权重）——只传一次字符串，
                # 不重复序列化。
                tools_payload = json.dumps(tools, ensure_ascii=False)
            except (TypeError, ValueError):
                tools_payload = None
        res = await self.context_assembler.assemble(
            session_id,
            messages,
            project_id=project_id,
            tools_payload=tools_payload,
        )
        return res.to_messages()

    def _build_last_analysis_context(self, messages: list[dict]) -> str:
        return _build_last_analysis_context(messages)

    def _evict_idle_locks(self) -> None:
        """逐出空闲 session 锁。被持有或有等待者的锁绝不可逐出（F1）。"""
        if len(self._session_locks) > self._MAX_LOCKS:
            evict_count = self._MAX_LOCKS // 4
            for sid in list(self._session_locks.keys())[:evict_count]:
                lock_to_evict = self._session_locks[sid]
                if not _lock_in_use(lock_to_evict):
                    self._session_locks.pop(sid, None)

    async def _get_or_create_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> list[dict]:
        if session_id in self._sessions:
            return self._sessions[session_id]

        self._evict_idle_locks()
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            if session_id in self._sessions:
                return self._sessions[session_id]
            loaded = await self._load_session_from_db(session_id, user_id=user_id)
            if loaded is None:
                # F23: DB 抖动时本轮降级为仅 system prompt 的临时历史，
                # 但不写进缓存 —— 否则 DB 恢复后该会话仍永远顶着空历史。
                return [{"role": "system", "content": self._build_system_prompt()}]
            self._sessions[session_id] = loaded
            return loaded

    def _apply_skill(self, messages: list[dict], skill_name: Optional[str]) -> None:
        """注入或刷新 Skill 指令"""
        if not skill_name:
            return
        from app.tools.skills import get_md_skill
        skill = get_md_skill(skill_name)
        if not skill:
            return
        marker = f"[Skill指令: {skill_name}]"
        messages[:] = [
            m for m in messages
            if not (m.get("role") == "system" and isinstance(m.get("content"), str) and m["content"].startswith(marker))
        ]
        messages.append({"role": "system", "content": f"{marker}\n\n{skill['body']}"})

    def _trim_session_tail(self, messages: list[dict]) -> None:
        """C-F12: bound the in-memory per-session message tail.

        ``_sessions`` caches the conversation as a mutable list that grows by
        2-N messages every turn and was never trimmed — a long-lived streaming
        session accumulates hundreds of turns in memory (tens of MB) while the
        LLM only ever reads the recent tail (``truncate_history_by_budget``,
        ~6000-token budget). Every append is also persisted via
        ``_save_msg_async``, so the DB is the source of truth; the cache only
        needs the recent tail.

        Keeps ``messages[0]`` (the system prompt — the context assembler reads
        it unconditionally) plus the newest turns that fit within
        ``_session_message_cap``. Turns are grouped the same way
        ``truncate_history_by_budget`` groups them (a turn = one user message
        plus its following assistant/tool messages), so a user/assistant/tool
        chain is never split — the LLM API rejects an orphaned assistant
        ``tool_calls`` or a ``tool`` message without its ``tool_call``. An
        oversized newest turn is kept whole (the bound degrades to one turn).
        """
        cap = self._session_message_cap
        if len(messages) <= cap:
            return
        tail = messages[1:]
        turns: list[list[dict]] = []
        current: list[dict] = []
        for msg in tail:
            if msg.get("role") == "user" and current:
                turns.append(current)
                current = [msg]
            else:
                current.append(msg)
        if current:
            turns.append(current)
        slots = cap - 1
        kept: list[dict] = []
        for turn in reversed(turns):
            if kept and len(kept) + len(turn) > slots:
                break
            kept = turn + kept
        messages[:] = [messages[0], *kept]

    async def _repair_orphaned_tool_calls(self, session_id: str, messages: list[dict]) -> None:
        """F9: 补齐孤儿 assistant tool_calls 的取消占位 tool 消息。

        cancel / disconnect 会打断「assistant(tool_calls) → N 条 tool 响应」的
        配对（见 _trim_session_tail 的 turn 分组说明）：LLM API 拒绝带孤儿
        tool_calls 的历史，下一轮调用直接报错，会话永久损坏。每条缺失的
        tool_call_id 补一条「已取消」tool 消息 —— 内存与 DB 同步补齐。
        幂等：已配对的 tool_call 不会重复补。
        """
        answered = {
            m.get("tool_call_id") for m in messages if m.get("role") == "tool"
        }
        orphan_ids: list[str] = []
        for m in messages:
            if m.get("role") != "assistant":
                continue
            for tc in m.get("tool_calls") or []:
                tc_id = tc.get("id") if isinstance(tc, dict) else None
                if tc_id and tc_id not in answered and tc_id not in orphan_ids:
                    orphan_ids.append(tc_id)
        for tc_id in orphan_ids:
            payload = "工具执行已被用户取消"
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": payload,
            })
            await self._save_msg_async(session_id, "tool", "", None, payload, tc_id)

    async def _persist_tool_messages(
        self,
        session_id: str,
        pending_tools: list[dict],
        completion_results: dict[str, dict],
        standard_calls: list,
        messages: list[dict],
        tool_result_msgs: list[str],
    ) -> None:
        """把已完成工具的结果按原始 tc 顺序对齐 LLM 上下文 + 落库。

        Phase 3（正常出口）与抢占式取消路径共用：取消路径在 Phase 3 之前
        return，已完成的工具（F9: 已完成 ≠ 已取消）必须在 abort 前同样落库，
        否则它们的 tool_call_id 会被 _repair_orphaned_tool_calls 误标成
        「工具执行已被用户取消」—— 而工具可能已创建图层、已 spawn durable job，
        首达终态使真实成功不可恢复。
        """
        for p in pending_tools:
            res = completion_results.get(p["step"].id)
            if res is None:
                continue  # 极端取消场景：该工具未完成，跳过
            tc = res["tc"]
            msg_result_str = res["msg_result_str"]
            tool_name = res["tool_name"]
            if standard_calls:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": msg_result_str,
                })
                db_save_content = msg_result_str[:100000] if len(msg_result_str) > 100000 else msg_result_str
                await self._save_msg_async(session_id, "tool", "", None, db_save_content, tc["id"])
            else:
                tool_result_msgs.append(f"{tool_name}: {msg_result_str}")

    async def _save_msg_async(self, session_id: str, role: str, content: str, tool_calls=None, tool_result=None, tool_call_id=None, reasoning_content=None):
        """异步保存消息到数据库"""
        if session_id in self._clearing_sessions:
            # P1: clear_session 刚删了该会话的 conversation 行 —— 现在写入会
            # 复活历史（SQLite: FK off → 孤儿 Message 行；Postgres: FK →
            # IntegrityError 被下方吞掉）。clear 期间抑制 DB 写；在途 turn 的
            # 内存 repair（_repair_orphaned_tool_calls 的 append）不受影响。
            logger.debug(
                "save_message suppressed for %s: session is being cleared", session_id
            )
            return
        try:
            if tool_result is not None and isinstance(tool_result, str) and len(tool_result) > 100000:
                tool_result = tool_result[:100000] + "...[truncated]"
            async with async_db_session() as db:
                await AsyncHistoryService(db).save_message(session_id, role, content, tool_calls, tool_result, tool_call_id, reasoning_content)
        except Exception as e:
            logger.error(f"Failed to save message asynchronously: {e}")

    async def _generate_title(self, session_id: str, first_user_message: str):
        """异步生成对话标题"""
        title = None
        try:
            # Route through the pooled LLM client (call_llm) instead of opening a
            # one-off httpx.AsyncClient, so title generation reuses the same
            # keep-alive connection pool as every other LLM call to this provider.
            cfg = LLMConfig(
                base_url=self.base_url,
                model=self.model,
                api_key=self.api_key,
                max_tokens=64,
            )
            messages = [
                {"role": "system", "content": "根据用户的首条消息，生成一个简短的对话主题标题。要求：1) 不超过12个字 2) 突出空间分析的核心对象（地名、分析类型等）3) 不要使用引号、书名号或多余的标点。只输出标题文本，不要任何额外内容。"},
                {"role": "user", "content": first_user_message[:500]},
            ]
            resp = await call_llm(cfg, messages)
            choice = resp["choices"][0]
            msg = choice["message"]
            title = msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content")
            if title:
                title = title.strip()

            if title:
                title = title.strip('"\'""''《》')
                if len(title) > 50:
                    title = first_user_message[:20].rstrip() + "..."
            if not title:
                title = first_user_message[:20].rstrip() + "..."
            async with async_db_session() as db:
                await AsyncHistoryService(db).update_title(session_id, title)
        except Exception as e:
            logger.warning(f"History: title generation failed for {session_id}: {e}")
            if title is None:
                title = (first_user_message or "")[:20].rstrip() + "..."
                try:
                    async with async_db_session() as db:
                        await AsyncHistoryService(db).update_title(session_id, title)
                except Exception as e2:
                    logger.warning(f"History: title fallback failed for {session_id}: {e2}")

    def _llm_config(self) -> LLMConfig:
        return LLMConfig(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            use_prompt_caching=self.use_prompt_caching,
        )

    def _planner_llm_config(self) -> LLMConfig:
        cfg = self._llm_config()
        if settings.LLM_PLANNER_MODEL:
            return LLMConfig(
                base_url=cfg.base_url,
                model=settings.LLM_PLANNER_MODEL,
                api_key=cfg.api_key,
                use_prompt_caching=cfg.use_prompt_caching,
            )
        return cfg

    async def _maybe_plan(self, session_id: str, message: str, messages: list[dict]):
        from app.services.chat.plan_orchestrator import plan_orchestrator
        from app.services.planning.followup import classify_followup
        from app.services.planning import FollowUpKind
        from app.services.tool_catalog import DOMAIN_KEYWORDS
        # P2-7（Pi#1/#2）：子代理引擎的轮次不参与父会话的规划（独立微会话，
        # 计划推进只属于主代理）。getattr 兜底：测试用 __new__ 构造的引擎没有
        # 该属性时按 False（主代理）处理。
        if getattr(self, "is_subagent_engine", False):
            return None
        env = await self._get_map_state_summary(session_id)
        try:
            # R5/R10：进程重启续接——把 store 里的 canonical 计划恢复到本进程 LRU。
            await plan_orchestrator.restore_plan(session_id)
        except Exception as e:
            logger.warning(f"[chat_execution_engine] plan restore 失败: {e}")
        try:
            plan = plan_orchestrator.get_plan(session_id)
            has_plan = plan is not None
            active_domains = list(plan.domains) if plan else []
            try:
                refs = await session_data_manager.list_refs(session_id)
            except Exception:  # noqa: BLE001
                refs = {}
            # P2-6（recovery P2 / Pi#3）：session_has_refs 必须排除计划自身的
            # ref（ref:plan-* 与 plan-current / plan-id:* 别名）——否则"仅存了
            # 一个计划、没有任何数据"的会话会被 ref_reuse 误判。
            has_data_refs = _has_non_plan_refs(refs)
            # design-v3 §followup：确定性分类喂给 should_plan。
            kind = classify_followup(
                message,
                has_active_plan=has_plan,
                active_domains=active_domains,
                session_has_refs=has_data_refs,
                domain_keywords=DOMAIN_KEYWORDS,
            )
            if kind == FollowUpKind.new_goal and self.catalog is not None:
                # design-v3 §5：明确换目标 → 旧领域 sticky 停止污染本轮工具选择。
                self.catalog.reset_sticky(session_id)
            return await plan_orchestrator.orchestrate_plan(
                self._planner_llm_config(), session_id, message, messages, env,
                followup_kind=kind,
            )
        except Exception as e:
            logger.warning(f"[chat_execution_engine] 规划阶段异常，降级无计划: {e}")
            return None

    def _log_tool_decision(
        self,
        session_id: str,
        round_index: int,
        message: str,
        tool_name: str,
        tool_args: dict,
        outcome: ToolDispatchResult,
        subset_size: int,
        step_n: int | None = None,
        failure_class: Optional[str] = None,
        recovery_action: Optional[str] = None,
    ) -> None:
        from app.services.chat import planner
        from app.services.chat.decision_log import ToolDecisionRecord, log_tool_decision
        from app.services.planning.store import plan_store

        if outcome.status == "error":
            quality = "error"
        elif _is_suspicious_result_fn(outcome.raw_result):
            quality = "empty"
        else:
            quality = "ok"
        plan = planner.get_plan(session_id)
        active = self.catalog.active_domains(session_id) if self.catalog else set()
        # Observability (design-v3 §6)：有活跃计划时附带 plan_id / plan_revision /
        # step_id（只做 plan_store 进程缓存查，绝不读 session store）。
        plan_id: Optional[str] = None
        plan_revision: Optional[int] = None
        step_id: Optional[str] = None
        if plan is not None:
            canon = plan_store.peek(session_id)
            if canon is not None:
                plan_id = canon.plan_id
                plan_revision = canon.revision
                if step_n is not None:
                    for cs in canon.steps:
                        if cs.n == step_n:
                            step_id = cs.id
                            break
        try:
            log_tool_decision(ToolDecisionRecord(
                session_id=session_id,
                round=round_index,
                user_message=message,
                active_domains=sorted(active),
                from_plan=plan is not None,
                subset_size=subset_size,
                total_tools=len(self.registry.get_schemas()),
                tool_chosen=tool_name,
                tool_args=tool_args if isinstance(tool_args, dict) else {},
                result_quality=quality,
                plan_step_matched=step_n,
                plan_id=plan_id,
                plan_revision=plan_revision,
                step_id=step_id,
                failure_class=failure_class,
                recovery_action=recovery_action,
            ))
        except Exception as e:
            logger.warning(f"[chat_execution_engine] 决策日志记录失败: {e}")

    async def _call_llm(self, messages: list[dict], tools: Optional[list] = None) -> dict:
        return await call_llm(self._llm_config(), messages, tools)

    def _call_llm_stream(self, messages: list[dict], tools: Optional[list] = None):
        return call_llm_stream(self._llm_config(), messages, tools)

    async def _persist_map_state(self, session_id: str, map_state: dict) -> None:
        """F4: persist the turn-start map_state snapshot.

        The viewport key has two writers (this snapshot + the client's
        throttled POST). The client stamps the snapshot with its monotonic
        ``viewport_seq``; we pass it through so an older in-flight POST that
        lands after this write is rejected as stale instead of clobbering the
        newer snapshot. Other keys are unsequenced (always apply).
        """
        viewport_seq = map_state.get("viewport_seq")
        for k, v in map_state.items():
            if k == "viewport_seq":
                continue
            await session_data_manager.set_map_state(
                session_id, k, v, seq=viewport_seq if k == "viewport" else None
            )

    async def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
        map_state: Optional[dict] = None,
        skill_name: Optional[str] = None,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> dict:
        """非流式对话"""
        if not session_id:
            session_id = str(uuid.uuid4())

        # P1: 快速路径拒绝 clearing 中的 session 的新 turn（避免
        # _get_or_create_session 的 DB 副作用）；权威检查在持锁后再次执行。
        self._reject_if_clearing(session_id)

        if map_state:
            await self._persist_map_state(session_id, map_state)
            from app.services.viewport_naming import schedule_populate_from_map_state
            schedule_populate_from_map_state(map_state)

        # RUN-03 (deep-audit round 2): serialize the whole turn per session.
        # The previous non-streaming chat() ran its tool loop with NO session
        # lock, so two concurrent requests on the same session_id interleaved
        # messages.append / executed_tools writes and duplicated tool
        # execution. chat_stream only locked around map_state setup. Both paths
        # now hold the session lock for the duration of the turn.
        #
        # NOTE: _get_or_create_session must run BEFORE acquiring the turn lock —
        # it acquires the SAME per-session lock internally for the DB-load path
        # (asyncio.Lock is not reentrant; acquiring it twice deadlocks).
        messages = await self._get_or_create_session(session_id, user_id=user_id)
        lock = self._get_session_lock(session_id)
        async with lock:
            self._reject_if_clearing(session_id)
            _task = asyncio.current_task()
            if _task is not None:
                self._active_turn_tasks[session_id] = _task
            # Runtime observability: bind turn identity + evidence for the whole
            # legacy turn (retires "run_id == session_id"). tool_metrics / job
            # rows inherit turn_id/run_id via W4/W5; the round loop records
            # context/LLM timing into the evidence accumulator.
            turn_id = rt_ctx.new_turn_id()
            run_id = rt_ctx.new_run_id()
            _pctx = rt_ctx.current_runtime_context()
            rt_ev = TurnEvidence(
                request_id=_pctx.request_id if _pctx else None,
                session_id=session_id, turn_id=turn_id, run_id=run_id,
            )
            with rt_ctx.bind_runtime_context(turn_id=turn_id, run_id=run_id), bind_turn_evidence(rt_ev):
                TURN_EVIDENCE.register(rt_ev)
                try:
                    result = await self._chat_locked(
                        message, session_id, messages, skill_name, user_id, project_id,
                    )
                    rt_ev.settle(Outcome.SUCCEEDED)
                    return result
                except asyncio.CancelledError:
                    rt_ev.settle(Outcome.CANCELLED)
                    raise
                except Exception as exc:  # noqa: BLE001
                    rt_ev.settle(Outcome.FAILED, failure_class=type(exc).__name__, detail=str(exc)[:200])
                    raise
                finally:
                    # design-v3：把本进程 canonical 计划（含打勾进度）持久化到 store。
                    await self._flush_plan(session_id)
                    # P1: the turn's cleanup drained — deregister the turn task so
                    # clear_session's quiesce doesn't wait on a finished turn.
                    self._active_turn_tasks.pop(session_id, None)
                    # C-F12: bound the in-memory tail once the turn's appends are
                    # complete (every exit path — return, exception, cancel).
                    self._trim_session_tail(messages)
                    rt_ev.mark_ended()
                    emit_turn_summary(rt_ev)
                    TURN_EVIDENCE.remove(turn_id)

    async def _flush_plan(self, session_id: str) -> None:
        """把活跃 canonical 计划持久化（advance_step 的 done 标志写回 store）。

        既作回合末 flush（chat / chat_stream 的 finally），也作 P3 #4 的
        mid-turn tick flush（mark_step_done 打勾后立即调用）——二者幂等（同一
        载荷原地 overwrite）。best-effort：任何失败只记 warning，绝不破坏回合。
        """
        # P2-7：子代理引擎不 flush 父会话的计划（独立微会话，计划属于主代理）。
        if getattr(self, "is_subagent_engine", False):
            return
        try:
            from app.services.chat import planner as _planner
            if _planner.get_plan(session_id) is None:
                return
            from app.services.chat.plan_orchestrator import plan_orchestrator
            await plan_orchestrator.flush(session_id)
        except Exception as e:
            logger.warning(f"[chat_execution_engine] plan flush 失败: {e}")

    async def _chat_locked(
        self,
        message: str,
        session_id: str,
        messages: list[dict],
        skill_name: Optional[str] = None,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> dict:
        """Non-streaming chat turn body, executed under the session lock."""

        self._apply_skill(messages, skill_name)
        messages.append({"role": "user", "content": message})
        await self._save_msg_async(session_id, "user", message)

        await self._maybe_plan(session_id, message, messages)
        executed_tools: set[tuple[str, str]] = set()

        task = self.tracker.create(session_id, message)
        owner_token = self.get_session_owner_token(session_id)

        try:
            for _ in range(self.max_rounds):
                if self.tracker.is_cancelled(task.id):
                    _settle_cancel()  # cooperative cancel ≠ success
                    return {"session_id": session_id, "content": "任务已取消", **({"owner_token": owner_token} if owner_token else {})}

                # tools 先选（含 tools payload 软计入估算），再组装上下文
                tools = self._select_tools(session_id, messages)
                _t_ctx = time.perf_counter()
                messages_with_context = await self._compose_request_messages(
                    session_id, messages, tools=tools,
                )
                _ev = current_turn_evidence()
                if _ev is not None:
                    _ev.add_context_ms((time.perf_counter() - _t_ctx) * 1000.0)

                _t_llm = time.perf_counter()
                response = await self._call_llm(messages_with_context, tools)
                if _ev is not None:
                    _ev.add_llm_round(total_ms=(time.perf_counter() - _t_llm) * 1000.0)
                choice = response.get("choices", [{}])[0]
                assistant_msg = choice.get("message", {})

                raw_content = assistant_msg.get("content") or ""
                reasoning = assistant_msg.get("reasoning") or assistant_msg.get("reasoning_content") or ""

                standard_calls = assistant_msg.get("tool_calls") or []
                xml_calls: list[dict] = []
                if not standard_calls:
                    if "minimax:tool_call" in raw_content:
                        xml_calls = _parse_minimax_xml_tool_calls(raw_content)

                tc_list = standard_calls or xml_calls

                if tc_list:
                    content_text = raw_content
                    if xml_calls:
                        content_text = re.sub(r'\s*minimax:tool_call[\s\S]*', '', content_text).strip()

                    entry: dict = {"role": "assistant", "content": content_text}
                    if reasoning:
                        entry["reasoning_content"] = reasoning
                    if standard_calls:
                        entry["tool_calls"] = standard_calls
                    messages.append(entry)
                    await self._save_msg_async(session_id, "assistant", content_text, tc_list, reasoning_content=reasoning)

                    tool_result_msgs: list[str] = []
                    # F28: 与流式路径（chat_stream 并行 wave）同款抢占式取消 ——
                    # cancel token 点燃立即 cancel 在飞工具，而不是等它自然跑完。
                    # 循环每轮重挂 cancel_watch：任一工具完成或取消点燃都会从
                    # asyncio.wait 返回，取消在整波完成前始终被检查（旧实现单次
                    # FIRST_COMPLETED wait 后直接 gather 整波 —— 多工具轮次里
                    # 取消要等所有工具自然跑完才生效，抢占是死的）。
                    tool_tasks = [
                        asyncio.create_task(
                            self.tool_pipeline.execute_tool_call(
                                tc, session_id, task.id, executed_tools,
                                owner_id=user_id, owner_token=owner_token,
                            )
                        )
                        for tc in tc_list
                    ]
                    cancel_watch: Optional[asyncio.Task] = None
                    if task.cancel_token is not None:
                        cancel_watch = asyncio.create_task(task.cancel_token.wait())
                    exec_results: list = [_CANCELLED_TOOL] * len(tool_tasks)
                    cancelled = False
                    try:
                        remaining: set = set(tool_tasks)
                        while remaining:
                            wait_set = set(remaining)
                            if cancel_watch is not None:
                                wait_set.add(cancel_watch)
                            done, _pending = await asyncio.wait(
                                wait_set, return_when=asyncio.FIRST_COMPLETED
                            )
                            if cancel_watch is not None and cancel_watch in done:
                                # F28: 抢占式取消 —— 同一批里已完成的工具先收走
                                # 结果（F9: 已完成 ≠ 已取消），未完成的立即 cancel。
                                cancelled = True
                                for t in done & remaining:
                                    idx = tool_tasks.index(t)
                                    try:
                                        exec_results[idx] = t.result()
                                    except Exception as e:  # noqa: BLE001
                                        exec_results[idx] = e
                                # P1: 有界等待 —— 顽固 straggler 不能拖住会话锁
                                await self._cancel_and_await(remaining)
                                remaining = set()
                                break
                            done_tools = done & remaining
                            remaining -= done_tools
                            for t in done_tools:
                                idx = tool_tasks.index(t)
                                try:
                                    exec_results[idx] = t.result()
                                except Exception as e:  # noqa: BLE001
                                    exec_results[idx] = e
                    finally:
                        # 外部取消（chat 协程被 cancel）时回收在飞工具与 watch。
                        # P1: 有界等待 —— 顽固 straggler 不能拖住会话锁。
                        await self._cancel_and_await(
                            [t for t in tool_tasks if not t.done()]
                        )
                        if cancel_watch is not None and not cancel_watch.done():
                            cancel_watch.cancel()
                            await self._cancel_and_await([cancel_watch])

                    for tc, exec_res in zip(tc_list, exec_results):
                        if exec_res is _CANCELLED_TOOL:
                            continue  # 被抢占/未完成 —— 由 repair 以「已取消」补齐
                        if isinstance(exec_res, Exception):
                            if cancelled:
                                continue  # 取消路径：已完成但失败的工具不写失败消息
                            logger.error("Tool execution parallel exception for %s: %s", tc, exec_res)
                            llm_payload = f"Tool execution failed: {exec_res}"
                        else:
                            llm_payload = exec_res.llm_payload
                            # R6-nonstreaming / R2：非流式路径同样只在“非可疑成功”
                            # 时推进计划（失败/空结果绝不打勾）。
                            if (
                                not getattr(self, "is_subagent_engine", False)  # P2-7：子代理不打勾父计划
                                and exec_res.outcome is not None
                                and exec_res.outcome.status == "ok"
                                and not _is_suspicious_result_fn(exec_res.outcome.raw_result)
                            ):
                                from app.services.chat import planner as _planner
                                step_n_matched = _planner.mark_step_done(
                                    session_id, exec_res.tool_name, self.registry
                                )
                                if step_n_matched is not None:
                                    # P3 #4：打勾后立即 best-effort 落盘（回合末
                                    # flush 仍保留，幂等）。崩溃/断连不丢 tick。
                                    await self._flush_plan(session_id)

                        if standard_calls:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": llm_payload,
                            })
                            await self._save_msg_async(session_id, "tool", "", None, llm_payload, tc.get("id", ""))
                        else:
                            tool_name = tc.get("function", {}).get("name", "tool") if isinstance(exec_res, Exception) else exec_res.tool_name
                            tool_result_msgs.append(f"{tool_name}: {llm_payload}")


                    if xml_calls and tool_result_msgs:
                        messages.append({
                            "role": "user",
                            "content": "[工具执行结果]\n" + "\n".join(tool_result_msgs),
                        })

                    if cancelled:
                        # F9: 已完成工具的消息已在上方落库；这里只补真正孤儿的
                        # tool_call（幂等），随后以取消收尾本轮。
                        await self._repair_orphaned_tool_calls(session_id, messages)
                        _settle_cancel()  # cooperative cancel ≠ success
                        return {"session_id": session_id, "content": "任务已取消", **({"owner_token": owner_token} if owner_token else {})}
                    continue
                else:
                    content = raw_content
                    entry = {"role": "assistant", "content": content}
                    if reasoning:
                        entry["reasoning_content"] = reasoning
                    messages.append(entry)
                    await self._save_msg_async(session_id, "assistant", content, reasoning_content=reasoning)
                    self.tracker.complete_task(task.id)
                    return {"session_id": session_id, "content": content, "reasoning": reasoning, **({"owner_token": owner_token} if owner_token else {})}

            self.tracker.complete_task(task.id)
            return {"content": "达到最大工具调用轮数", "session_id": session_id, **({"owner_token": owner_token} if owner_token else {})}
        except (asyncio.CancelledError, GeneratorExit):
            # F8: 取消必须呈现为 cancelled 终态，而不是 failed。
            self.tracker.cancel(task.id)
            # F9: 补齐孤儿 tool_call，避免会话历史永久损坏。
            # P1: shield + 有界 wait_for —— 二次取消不能截断 repair 的中途写；
            # GC 驱动的 GeneratorExit 也不会因 except 内的 suspend 报
            # 'async generator ignored GeneratorExit'。aclose() 路径行为不变。
            try:
                await asyncio.wait_for(
                    asyncio.shield(
                        self._repair_orphaned_tool_calls(session_id, messages)
                    ),
                    timeout=self._cancel_wait_timeout,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            raise
        except Exception:
            self.tracker.fail_task(task.id, "non-streaming chat exception")
            raise

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        self._evict_idle_locks()
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    def _reject_if_clearing(self, session_id: str) -> None:
        """Raise SessionClearingError if the session is mid-clear_session.

        P1: blocks NEW turn start for a clearing session — return a clean
        error, not a race into the delete.
        """
        if session_id in self._clearing_sessions:
            raise SessionClearingError(
                f"session {session_id} is being cleared; start a new turn after it completes"
            )

    async def _cancel_and_await(self, tasks, timeout: Optional[float] = None) -> None:
        """Cancel a batch of tool tasks and await them briefly (bounded).

        P1: a straggler tool that cannot be interrupted (worker thread parked
        in long GIS compute) would make ``await asyncio.gather(*tasks,
        return_exceptions=True)`` hold the session lock for the worker's full
        duration on cancel/disconnect. Cancel everything first, then await up
        to ``timeout`` seconds so the turn's cancel/finally path returns
        promptly and the session lock is released — stragglers finish whenever
        their workers return and their results are discarded. Task-level
        CancelledError is collected by asyncio.wait; a re-cancel of THIS task
        propagates as before.
        """
        if not tasks:
            return
        if timeout is None:
            timeout = self._cancel_wait_timeout
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.wait(tasks, timeout=timeout)

    async def chat_stream(
        self,
        message: str,
        session_id: Optional[str] = None,
        map_state: Optional[dict] = None,
        skill_name: Optional[str] = None,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """流式对话，yield SSE 格式事件含任务跟踪"""
        if not session_id:
            session_id = str(uuid.uuid4())

        # P1: 快速路径拒绝 clearing 中的 session 的新 turn（避免
        # _get_or_create_session 的 DB 副作用）；权威检查在持锁后再次执行。
        self._reject_if_clearing(session_id)

        # RUN-03: the session lock now covers the ENTIRE turn (not just
        # map_state setup), serializing concurrent requests on the same
        # session_id — messages.append / executed_tools writes were previously
        # racing between two turns. Holding an asyncio.Lock across yield is
        # safe: the lock is released via async-with __aexit__ when the
        # generator is closed (aclose) or when the turn ends.
        #
        # NOTE: _get_or_create_session must run BEFORE acquiring the turn lock —
        # it acquires the SAME per-session lock internally for the DB-load path
        # (asyncio.Lock is not reentrant; acquiring it twice deadlocks).
        messages = await self._get_or_create_session(session_id, user_id=user_id)
        lock = self._get_session_lock(session_id)
        async with lock:
            self._reject_if_clearing(session_id)
            _task = asyncio.current_task()
            if _task is not None:
                self._active_turn_tasks[session_id] = _task
            if map_state:
                await self._persist_map_state(session_id, map_state)
                from app.services.viewport_naming import schedule_populate_from_map_state
                schedule_populate_from_map_state(map_state)

            self._apply_skill(messages, skill_name)
            messages.append({"role": "user", "content": message})
            await self._save_msg_async(session_id, "user", message)

            task = self.tracker.create(session_id, message)
            # Runtime observability: bind turn identity + evidence for the legacy
            # stream turn (manual CM enter/exit — the generator body is too large
            # to re-indent; reset happens in the outer finally, which is correct
            # for ContextVar token reset under any exit path).
            turn_id = rt_ctx.new_turn_id()
            run_id = rt_ctx.new_run_id()
            _pctx = rt_ctx.current_runtime_context()
            rt_ev = TurnEvidence(
                request_id=_pctx.request_id if _pctx else None,
                session_id=session_id, turn_id=turn_id, run_id=run_id,
            )
            _rt_cm = rt_ctx.bind_runtime_context(turn_id=turn_id, run_id=run_id)
            _tev_cm = bind_turn_evidence(rt_ev)
            _rt_cm.__enter__()
            _tev_cm.__enter__()
            try:
                # register inside the try so a raise here still reaches the
                # finally that exits the CMs (no ContextVar leak window).
                TURN_EVIDENCE.register(rt_ev)
                owner_token = self.get_session_owner_token(session_id)
                task_start_data = {"task_id": task.id, "session_id": session_id}
                if owner_token:
                    task_start_data["owner_token"] = owner_token
                yield sse_event("task_start", task_start_data)

                plan = await self._maybe_plan(session_id, message, messages)
                plan_existed_this_turn = plan is not None
                if not plan_existed_this_turn:
                    from app.services.chat import planner as _planner_ctx
                    plan_existed_this_turn = _planner_ctx.get_plan(session_id) is not None
                try:
                    if plan is not None:
                        yield sse_event("plan_ready", {
                            "session_id": session_id,
                            "task_id": task.id,
                            "intent": plan.intent,
                            "domains": plan.domains,
                            "steps": [
                                # P3-2/P2-6：done 取投影的真实打勾状态（恢复出的
                                # 计划带已完成步骤），不再硬编码 False。
                                {"n": s.n, "goal": s.goal,
                                 "tool_family": s.tool_family if s.tool_family is not None else "core",
                                 "done": s.done}
                                for s in plan.steps
                            ],
                        })
                except Exception as e:
                    logger.warning(f"[chat_execution_engine] plan_ready 发送失败: {e}")

                def _maybe_plan_finalized_event():
                    # design-v3：只有本轮确实存在/产生过非终态计划才发 plan_finalized，
                    # 避免无计划轮次或已恢复出终态计划时发出 spurious finalize。
                    if not plan_existed_this_turn:
                        return None
                    try:
                        from app.services.chat import planner as _planner
                        plan_obj = _planner.get_plan(session_id)
                        if plan_obj is None:
                            return None
                        skipped = [s.n for s in plan_obj.steps if not s.done]
                        return sse_event("plan_finalized", {
                            "session_id": session_id,
                            "task_id": task.id,
                            "skipped": skipped,
                        })
                    except Exception as e:
                        logger.warning(f"[chat_execution_engine] plan_finalized 构造失败: {e}")
                        return None

                executed_tools = set()

                for round_index in range(self.max_rounds):
                    # tools 先选（含 tools payload 软计入估算），再组装上下文
                    tools = self._select_tools(session_id, messages)
                    _t_ctx = time.perf_counter()
                    messages_with_context = await self._compose_request_messages(
                        session_id, messages, project_id=project_id, tools=tools,
                    )
                    _ev = current_turn_evidence()
                    if _ev is not None:
                        _ev.add_context_ms((time.perf_counter() - _t_ctx) * 1000.0)

                    if self.tracker.is_cancelled(task.id):
                        pf = _maybe_plan_finalized_event()
                        if pf:
                            yield pf
                        _settle_cancel()  # cooperative cancel ≠ success
                        yield sse_event("task_cancelled", {"task_id": task.id})
                        return

                    streamed_content_parts: list[str] = []
                    assistant_msg: dict = {}
                    # Phase 8: token 事件批处理。LLM 流式输出每个 token 产生一条 SSE
                    # （= 一次 HTTP write）；SSEBatcher 按 32 条 / 80ms 窗口合并为更少
                    # 更大的 write，降低网络与前端解析开销。只合并 token 热路径——
                    # 结构事件（step_start/step_result/...）保持逐条 yield，前端事件
                    # 语义不变。done 到来时 flush 尾部，保证流式内容完整到达。
                    from app.utils.sse import SSEBatcher
                    token_batcher = SSEBatcher(max_events=32, max_delay_s=0.08)
                    _t_llm = time.perf_counter()
                    _ttft_ms: Optional[float] = None
                    async for event_type, event_data in self._call_llm_stream(messages_with_context, tools):
                        if event_type == "token":
                            if _ttft_ms is None:
                                _ttft_ms = (time.perf_counter() - _t_llm) * 1000.0
                                if _ev is not None:
                                    _ev.mark_first_event()
                            streamed_content_parts.append(event_data["content"])
                            token_batcher.push(sse_event("token", {
                                "content": event_data["content"],
                                "is_reasoning": event_data.get("is_reasoning", False),
                                "session_id": session_id,
                            }))
                            async for chunk in token_batcher.drain():
                                yield chunk
                        elif event_type == "done":
                            # 收尾：冲刷尾部 token，保证流式内容完整到达前端
                            for chunk in token_batcher.flush():
                                yield chunk
                            assistant_msg = event_data["message"]

                    if _ev is not None:
                        _ev.add_llm_round(
                            total_ms=(time.perf_counter() - _t_llm) * 1000.0,
                            ttft_ms=_ttft_ms,
                        )

                    standard_calls = assistant_msg.get("tool_calls") or []
                    xml_calls: list[dict] = []
            
                    raw_content = assistant_msg.get("content") or ""
                    reasoning = assistant_msg.get("reasoning") or assistant_msg.get("reasoning_content") or ""

                    if not standard_calls:
                        if "minimax:tool_call" in raw_content:
                            xml_calls = _parse_minimax_xml_tool_calls(raw_content)

                    tc_list = standard_calls or xml_calls

                    if tc_list:
                        content_text = raw_content
                        if xml_calls:
                            content_text = re.sub(r'\s*minimax:tool_call[\s\S]*', '', content_text).strip()
                
                        if content_text:
                            yield sse_event("content", {"content": "\n", "session_id": session_id})

                        entry: dict = {"role": "assistant", "content": content_text}
                        if reasoning:
                            entry["reasoning_content"] = reasoning
                        if standard_calls:
                            entry["tool_calls"] = standard_calls
                        messages.append(entry)
                        await self._save_msg_async(session_id, "assistant", content_text, tc_list, reasoning_content=reasoning)

                        tool_result_msgs: list[str] = []

                        # ── 并行工具分发 (Phase 8) ─────────────────────────────────────
                        # 原实现按 tc 顺序逐条 await execute_tool_call：LLM 一次返回 N 个
                        # 独立工具调用时串行执行（总耗时 = N 个工具耗时之和）。现在：
                        #   Phase 1: 顺序发出每个工具的 step_start/tool_call（保持前端
                        #            认知顺序），同时为每个工具创建 asyncio.Task 并发执行；
                        #   Phase 2: asyncio.wait 按完成顺序消费结果，完成即流式推送
                        #            step_result/step_error/plan_step_done/tool_result；
                        #   Phase 3: 全部完成后按原始 tc 顺序对齐 LLM 上下文 messages
                        #            + 落库（LLM 按 tool_call_id 对齐，顺序必须与请求一致）。
                        # 取消语义：任一完成事件后检查 is_cancelled（不再启动新任务并
                        # cancel 未完成任务）；生成器被外部取消时 finally 清理全部任务。
                        # ───────────────────────────────────────────────────────────────
                        pending_tools: list[dict] = []  # 保持原始 tc 顺序
                        for tc in tc_list:
                            tool_name = tc["function"]["name"]
                            tool_args_raw = tc["function"]["arguments"]

                            try:
                                tool_args_dict = json.loads(tool_args_raw) if isinstance(tool_args_raw, str) else tool_args_raw
                            except (json.JSONDecodeError, TypeError) as e:
                                _arg_preview = tool_args_raw[:200] if isinstance(tool_args_raw, (str, bytes)) else tool_args_raw
                                logger.warning(f"工具参数解析失败 tool={tool_name} raw={repr(_arg_preview)}: {e}")
                                tool_args_dict = {}

                            step = self.tracker.start_step(task.id, tool_name, tool_args_dict)
                            yield sse_event("step_start", {
                                "task_id": task.id,
                                "step_id": step.id,
                                "step_index": len(task.steps),
                                "tool": tool_name,
                                "session_id": session_id,
                            })
                            yield sse_event("tool_call", {"name": tool_name, "arguments": tool_args_raw})

                            pipeline_task = asyncio.create_task(
                                # RUN-02: chat_stream already opened the step (start_step
                                # above) and owns its terminal transition via
                                # complete_step/fail_step — pass it in so the pipeline
                                # does NOT open a second track_step (which previously
                                # doubled task.steps and desynced step_id in SSE).
                                self.tool_pipeline.execute_tool_call(
                                    tc, session_id, task.id, executed_tools,
                                    pre_created_step=step,
                                    owner_id=user_id,
                                    owner_token=owner_token,
                                )
                            )
                            pending_tools.append({
                                "tc": tc,
                                "step": step,
                                "tool_name": tool_name,
                                "tool_args_dict": tool_args_dict,
                                "task": pipeline_task,
                            })

                        all_tasks = {p["task"] for p in pending_tools}
                        task_to_pending = {p["task"]: p for p in pending_tools}
                        completion_results: dict[str, dict] = {}  # step.id -> {tc, msg_result_str}

                        def _aborted_step_events() -> list[str]:
                            """F7/F8: 取消/断连路径上把未完成的 step 记 cancelled
                            （绝不留 running），并生成对应的 step_cancelled SSE 事件。"""
                            evts: list[str] = []
                            for p in pending_tools:
                                if p["step"].id in completion_results:
                                    continue
                                try:
                                    self.tracker.cancel_step(task.id, p["step"].id)
                                except Exception:
                                    continue
                                evts.append(sse_event("step_cancelled", {
                                    "task_id": task.id,
                                    "step_id": p["step"].id,
                                    "tool": p["tool_name"],
                                    "session_id": session_id,
                                }))
                            return evts

                        # ADR-0052 抢占式取消：以前只在「某个工具跑完之后」才检查
                        # is_cancelled，所以用户点 Cancel 要等当前工具自然结束
                        # （长 GIS 计算 30–60s）。现在把 token.wait() 一起放进
                        # asyncio.wait —— 取消到达即刻 cancel 全部在飞任务，
                        # CPU/worker 立即释放，而不是只把 UI 状态改成已取消。
                        cancel_watch: Optional[asyncio.Task] = None
                        if task.cancel_token is not None:
                            cancel_watch = asyncio.create_task(task.cancel_token.wait())

                        try:
                            remaining: set[asyncio.Task] = set(all_tasks)
                            while remaining:
                                wait_set = set(remaining)
                                if cancel_watch is not None:
                                    wait_set.add(cancel_watch)
                                done, _pending = await asyncio.wait(
                                    wait_set, timeout=5.0, return_when=asyncio.FIRST_COMPLETED
                                )

                                done_tools = done & remaining
                                remaining -= done_tools
                                cancel_fired = cancel_watch is not None and cancel_watch in done
                                if not done_tools and not cancel_fired:
                                    yield sse_event("keep_alive", {"message": "ping"})
                                    logger.debug("SSE Heartbeat sent for parallel tool wave")
                                    continue
                                for t in done_tools:
                                    p = task_to_pending[t]
                                    step = p["step"]
                                    tool_name = p["tool_name"]
                                    tool_args_dict = p["tool_args_dict"]

                                    try:
                                        exec_res = t.result()
                                    except asyncio.CancelledError:
                                        # F7/F8: 被抢占取消的 step 记 cancelled，不留 running
                                        try:
                                            self.tracker.cancel_step(task.id, step.id)
                                        except Exception:
                                            pass
                                        yield sse_event("step_cancelled", {
                                            "task_id": task.id,
                                            "step_id": step.id,
                                            "tool": tool_name,
                                            "session_id": session_id,
                                        })
                                        continue
                                    except Exception as e:  # noqa: BLE001 防御（execute_tool_call 内部已兜底）
                                        logger.error(f"[chat_execution_engine] tool task raised for {tool_name}: {e}")
                                        self.tracker.fail_step(task.id, step.id, str(e))
                                        fc, ra = self._classify_failure(
                                            outcome=None, exception=e
                                        )
                                        step_error_payload = {
                                            "task_id": task.id,
                                            "step_id": step.id,
                                            "tool": tool_name,
                                            "error": str(e),
                                        }
                                        if fc:
                                            step_error_payload["failure_class"] = fc
                                            step_error_payload["recovery_action"] = ra
                                        yield sse_event("step_error", step_error_payload)
                                        continue

                                    outcome = exec_res.outcome

                                    # R2 (design-v3 §2)：只有“非可疑成功”才推进计划步骤；
                                    # 失败 / 重复 / 校验错误 / 空结果绝不打勾。
                                    from app.services.chat import planner as _planner
                                    step_n_matched = None
                                    failure_class: Optional[str] = None
                                    recovery_action: Optional[str] = None
                                    if (
                                        outcome.status == "ok"
                                        and not _is_suspicious_result_fn(outcome.raw_result)
                                        and not getattr(self, "is_subagent_engine", False)  # P2-7
                                    ):
                                        step_n_matched = _planner.mark_step_done(
                                            session_id, tool_name, self.registry
                                        )
                                        if step_n_matched is not None:
                                            # P3 #4：打勾后立即 best-effort 落盘
                                            # （回合末 flush 仍保留，幂等）。
                                            await self._flush_plan(session_id)
                                    elif outcome.status == "error":
                                        failure_class, recovery_action = self._classify_failure(outcome)
                                    self._log_tool_decision(
                                        session_id, round_index, message, tool_name,
                                        tool_args_dict, outcome, len(tools or []),
                                        step_n=step_n_matched,
                                        failure_class=failure_class,
                                        recovery_action=recovery_action,
                                    )
                                    try:
                                        if step_n_matched is not None:
                                            yield sse_event("plan_step_done", {
                                                "session_id": session_id,
                                                "task_id": task.id,
                                                "step_n": step_n_matched,
                                            })
                                    except Exception as e:
                                        logger.warning(f"[chat_execution_engine] plan_step_done 发送失败: {e}")

                                    msg_result_str = outcome.llm_payload

                                    if getattr(exec_res, "cancelled", False):
                                        # F8: 取消不是工具故障 —— step 记 cancelled
                                        # 并发 step_cancelled，区别于 step_error。
                                        try:
                                            self.tracker.cancel_step(task.id, step.id)
                                        except Exception:
                                            pass
                                        yield sse_event("step_cancelled", {
                                            "task_id": task.id,
                                            "step_id": step.id,
                                            "tool": tool_name,
                                            "session_id": session_id,
                                        })
                                        yield sse_event("tool_result", {"name": tool_name, "result": msg_result_str, "session_id": session_id})
                                    elif outcome.status == "repeated":
                                        # Reviewer BLOCKING fix (RUN-02): chat_stream
                                        # owns the step lifecycle now (pre_created_step
                                        # skips the pipeline's track_step, whose
                                        # __aexit__ used to complete the step). The
                                        # "repeated" branch previously left the step
                                        # in "running" forever. Terminal transition
                                        # is required here too.
                                        self.tracker.complete_step(task.id, step.id, outcome.raw_result)
                                        yield sse_event("step_result", {
                                            "task_id": task.id,
                                            "step_id": step.id,
                                            "tool": tool_name,
                                            "result": outcome.slim_event,
                                            "session_id": session_id,
                                        })
                                    elif outcome.status == "error":
                                        self.tracker.fail_step(task.id, step.id, outcome.error_msg or "")
                                        step_error_payload = {
                                            "task_id": task.id,
                                            "step_id": step.id,
                                            "tool": tool_name,
                                            "error": outcome.error_msg,
                                        }
                                        if failure_class:
                                            step_error_payload["failure_class"] = failure_class
                                            step_error_payload["recovery_action"] = recovery_action
                                        yield sse_event("step_error", step_error_payload)
                                        yield sse_event("tool_result", {"name": tool_name, "result": msg_result_str, "session_id": session_id})
                                    else:
                                        self.tracker.complete_step(task.id, step.id, outcome.raw_result)
                                        step_payload = {
                                            "task_id": task.id,
                                            "step_id": step.id,
                                            "tool": tool_name,
                                            "result": outcome.slim_event,
                                            "geojson_ref": outcome.geojson_ref,
                                            "session_id": session_id,
                                        }
                                        # V3 Performance: descriptor was computed by dispatch() and
                                        # is carried on outcome.ref_descriptor — zero extra async calls.
                                        if outcome.ref_descriptor:
                                            step_payload["ref_descriptor"] = outcome.ref_descriptor
                                        # ADR-0052: 本步骤派生的后台 durable job —— 前端
                                        # 据此把 GIS job 挂到该 tool step 下，而不是让
                                        # agent task 与 Celery task 变成两条毫无关联的
                                        # UI 条目。
                                        bg_jobs = getattr(exec_res, "background_job_ids", None)
                                        if bg_jobs:
                                            step_payload["background_job_ids"] = list(bg_jobs)
                                        yield sse_event("step_result", step_payload)
                                        yield sse_event("tool_result", {"name": tool_name, "result": outcome.slim_event, "session_id": session_id})

                                    completion_results[step.id] = {
                                        "tc": p["tc"],
                                        "msg_result_str": msg_result_str,
                                        "tool_name": tool_name,
                                    }

                                # 取消（cancel_watch 点燃或 tracker is_cancelled）：
                                # 先收割同一批里已完成的工具（F9: 已完成 ≠ 已取消），
                                # 再抢占式 cancel 真正未完成的在飞任务（F28）。
                                if cancel_fired or self.tracker.is_cancelled(task.id):
                                    # F9: 已完成工具的消息先正常落库 —— 否则 repair
                                    # 会把它们的 tool_call_id 写成「已取消」占位，而
                                    # 工具可能已创建图层 / 已 spawn durable job，首达
                                    # 终态使真实成功不可恢复。
                                    await self._persist_tool_messages(
                                        session_id, pending_tools, completion_results,
                                        standard_calls, messages, tool_result_msgs,
                                    )
                                    # P1: 有界等待 —— 顽固 straggler 不能拖住会话锁
                                    await self._cancel_and_await(remaining)
                                    remaining = set()
                                    # F7/F8: 只有真正未完成的 step 记 cancelled
                                    for evt in _aborted_step_events():
                                        yield evt
                                    # F9: 补齐孤儿 tool_call（已完成工具不在其列）
                                    await self._repair_orphaned_tool_calls(session_id, messages)
                                    pf = _maybe_plan_finalized_event()
                                    if pf:
                                        yield pf
                                    _settle_cancel()  # cooperative cancel ≠ success
                                    yield sse_event("task_cancelled", {"task_id": task.id})
                                    return
                        finally:
                            # 生成器被外部取消（GeneratorExit/CancelledError）时清理未完成任务。
                            # P1: 有界等待 —— 顽固 straggler 不能拖住会话锁。
                            await self._cancel_and_await(all_tasks)
                            # ADR-0052: cancel_watch 是纯等待任务，正常路径永不完成，
                            # 必须显式回收，否则每个工具 wave 泄漏一个 pending task。
                            if cancel_watch is not None and not cancel_watch.done():
                                cancel_watch.cancel()
                                await self._cancel_and_await([cancel_watch])

                        # Phase 3: 按原始 tc 顺序对齐 LLM 上下文 + 落库
                        await self._persist_tool_messages(
                            session_id, pending_tools, completion_results,
                            standard_calls, messages, tool_result_msgs,
                        )

                        if xml_calls and tool_result_msgs:
                            messages.append({
                                "role": "user",
                                "content": "[工具执行结果]\n" + "\n".join(tool_result_msgs),
                            })

                        # F9: 正常出口也兜底补齐孤儿 tool_call —— per-tool 处理里的
                        # except CancelledError: continue / 防御 except Exception:
                        # continue 分支不落库，会留下孤儿 assistant tool_calls；repair
                        # 幂等，已配对的 tool_call 不会重复补。
                        await self._repair_orphaned_tool_calls(session_id, messages)

                        continue
                    else:
                        content = raw_content
                
                        entry = {"role": "assistant", "content": content}
                        if reasoning:
                            entry["reasoning_content"] = reasoning
                        messages.append(entry)
                        await self._save_msg_async(session_id, "assistant", content, reasoning_content=reasoning)

                        yield sse_event("content", {"content": "", "session_id": session_id, "streaming_done": True})

                        pf = _maybe_plan_finalized_event()
                        if pf:
                            yield pf
                        self.tracker.complete_task(task.id)
                        rt_ev.settle(Outcome.SUCCEEDED)
                        yield sse_event("task_complete", {
                            "task_id": task.id,
                            "step_count": len(task.steps),
                            "summary": content[:100],
                        })
                        yield sse_event("done", {"session_id": session_id})
                        self._fire_and_forget(self._generate_title, session_id, message)
                        return

                self.tracker.fail_task(task.id, "达到最大工具调用轮数")
                rt_ev.settle(Outcome.FAILED, failure_class="max_rounds")
                pf = _maybe_plan_finalized_event()
                if pf:
                    yield pf
                yield sse_event("task_error", {"task_id": task.id, "error": "达到最大轮数"})
                yield sse_event("content", {"content": "达到最大工具调用轮数", "session_id": session_id})
                yield sse_event("done", {"session_id": session_id})
            except (asyncio.CancelledError, GeneratorExit):
                # B-P2-17: 客户端断连/生成器被关闭时终止 tracker 任务，
                # 避免任务永远停留在 running（直到 MAX_TOTAL_TASKS 逐出）。
                # F8: 断连/取消呈现为 cancelled 终态而不是 failed；
                # cancel() 顺带把仍 running 的 step 收尾（F7）。
                task_info = self.tracker.get(task.id)
                if task_info is not None and task_info.status == TaskStatus.running:
                    self.tracker.cancel(task.id)
                else:
                    self.tracker.terminalize_running_steps(task.id)
                # F9: 补齐孤儿 tool_call —— 否则下一轮 LLM 调用被拒，
                # 会话永久损坏。_save_msg_async 内部已吞异常。
                # P1: shield + 有界 wait_for —— 二次取消不能截断 repair 的
                # 中途写；GC 驱动的 GeneratorExit 也不会因 except 内的 suspend
                # 报 'async generator ignored GeneratorExit'。aclose() 行为不变。
                try:
                    await asyncio.wait_for(
                        asyncio.shield(
                            self._repair_orphaned_tool_calls(session_id, messages)
                        ),
                        timeout=self._cancel_wait_timeout,
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                rt_ev.settle(Outcome.CANCELLED)
                raise
            except Exception:
                # B-P2-17: 流中异常同样终止任务（与 _chat_locked 一致）。
                task_info = self.tracker.get(task.id)
                if task_info is not None and task_info.status == TaskStatus.running:
                    self.tracker.fail_task(task.id, "chat_stream exception")
                rt_ev.settle(Outcome.FAILED, failure_class="chat_stream_exception")
                raise
            finally:
                # design-v3：把本进程 canonical 计划（含打勾进度）持久化到 store。
                await self._flush_plan(session_id)
                # P1: the turn's cleanup drained — deregister the turn task so
                # clear_session's quiesce doesn't wait on a finished turn.
                self._active_turn_tasks.pop(session_id, None)
                # C-F12: bound the in-memory tail once the turn's appends
                # are complete (every exit path — done, cancelled, max rounds,
                # disconnect/exception via generator close).
                self._trim_session_tail(messages)
                # Runtime observability: exit the bound CMs (reset ContextVars),
                # emit the diagnostic summary, and unregister the evidence. If no
                # terminal point settled an outcome, it stays None (honest).
                rt_ev.mark_ended()
                emit_turn_summary(rt_ev)
                TURN_EVIDENCE.remove(turn_id)
                _tev_cm.__exit__(None, None, None)
                _rt_cm.__exit__(None, None, None)

    async def _dispatch_tool(
        self,
        tc: dict,
        session_id: str,
        executed_tools: set[tuple[str, str]],
    ) -> ToolDispatchResult:
        # RUN-01: reuse the engine's single ToolDispatchService so the dedup
        # lock is shared across the parallel tool wave.
        return await self.dispatch_service.dispatch(tc, session_id, executed_tools)

    async def _pipeline_dispatch(
        self,
        tc: dict,
        session_id: str,
        executed_tools: set[tuple[str, str]],
    ) -> ToolDispatchResult:
        """Pipeline dispatch entry point — resolves _dispatch_tool at call time.

        Passed to ToolExecutionPipeline as dispatch_fn so the pipeline task
        dispatches through the engine's CURRENT _dispatch_tool instead of the
        method frozen at __init__ time: overrides (test seams, subclass
        overrides) take effect at dispatch time, while the RUN-01 shared
        ToolDispatchService guarantee holds because _dispatch_tool always
        routes to self.dispatch_service.
        """
        return await self._dispatch_tool(tc, session_id, executed_tools)

    async def clear_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        owner_token: Optional[str] = None,
    ) -> bool:
        """删除会话"""
        # P1: 在删行之前把该 session 标记为 clearing —— 在途 turn 的清理
        # （repair + _save_msg_async）在标记下运行，DB 写被抑制，不可能复活
        # 已删除的 conversation；新的 turn 被干净地拒绝。标记在 finally 中、
        # 有界 quiesce 之后清除。
        self._clearing_sessions.add(session_id)
        try:
            deleted = False
            try:
                async with async_db_session() as db:
                    deleted = await AsyncHistoryService(db).delete_session(
                        session_id, user_id=user_id, owner_token=owner_token
                    )
            except Exception as e:
                logger.warning(f"History: failed to delete session {session_id}: {e}")
                return False
            if deleted:
                # F1: 先取消该 session 的在途 turn —— 否则持有会话锁的 turn 会在
                # 会话删除后继续往 messages/DB 追加（消息复活、重复工具执行）。
                for task_info in self.tracker.list_by_session(session_id):
                    if task_info.status == TaskStatus.running:
                        self.tracker.cancel(task_info.id)
                # P1: 有界 quiesce —— 在清标记之前等被取消 turn 的清理排空
                # （cancel 点燃 token → wave 抢占在飞工具 → repair → turn 结束）。
                # 不等待的话，turn 的 repair/save 可能与标记清除竞态、复活行。
                turn_task = self._active_turn_tasks.get(session_id)
                if (
                    turn_task is not None
                    and not turn_task.done()
                    and turn_task is not asyncio.current_task()
                ):
                    try:
                        await asyncio.wait(
                            {turn_task}, timeout=self._clear_quiesce_timeout
                        )
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
                self._sessions.pop(session_id, None)
                # F1: 锁被在途 turn 持有（或有等待者）时不能丢弃 —— 丢弃后下一个
                # 并发请求拿到的是新锁，会与旧锁并行，破坏按会话串行化。
                lock = self._session_locks.get(session_id)
                if lock is not None and not _lock_in_use(lock):
                    self._session_locks.pop(session_id, None)
                self._session_owner_tokens.pop(session_id, None)
                # F21: 每一项进程内清理都可能独立失败（Redis 抖动 / 可选模块缺失），
                # 隔离失败，保证其余清理总能执行；路由语义不变（仍按 DB 删除结果返回）。
                try:
                    await session_data_manager.clear_session(session_id)
                except Exception as e:
                    logger.warning(f"clear_session: session_data cleanup failed for {session_id}: {e}")
                try:
                    from app.services.chat import planner
                    planner.clear_plan(session_id)
                except Exception as e:
                    logger.warning(f"clear_session: plan cleanup failed for {session_id}: {e}")
                try:
                    from app.services.planning.store import plan_store
                    await plan_store.clear(session_id)
                except Exception as e:
                    logger.warning(f"clear_session: plan_store cleanup failed for {session_id}: {e}")
                try:
                    from app.services.chat.context.layer_schema import clear_layer_schema_cache
                    clear_layer_schema_cache(session_id)
                except ImportError:
                    pass
                except Exception as e:
                    logger.warning(f"clear_session: layer schema cleanup failed for {session_id}: {e}")
                if self.catalog is not None:
                    try:
                        self.catalog.reset_session(session_id)
                    except Exception as e:
                        logger.warning(f"clear_session: catalog reset failed for {session_id}: {e}")
            return deleted
        finally:
            self._clearing_sessions.discard(session_id)

    def _detect_suspicious_result(self, result: Any) -> bool:
        return _is_suspicious_result_fn(result)

    @staticmethod
    def _classify_failure(
        outcome: Optional[ToolDispatchResult],
        exception: Optional[Exception] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        """design-v3 §recovery：把一次工具失败分类成 failure_class + recovery_action。

        供 step_error SSE / decision_log 附加字段使用（additive，不改变任何
        现有行为与自愈路径）。
        """
        from app.services.planning.models import FailureClass as _FailureClass
        from app.services.planning.recovery import (
            classify_error as _classify_error,
            recovery_action_for as _recovery_action_for,
        )
        try:
            if exception is not None:
                fc = _classify_error(exception=exception)
            else:
                raw = outcome.raw_result if isinstance(outcome.raw_result, dict) else {}
                if raw.get("cancelled"):
                    fc = _FailureClass.cancelled
                else:
                    fc = _classify_error(
                        status=outcome.status,
                        code=raw.get("code"),
                        error_type=raw.get("error_type"),
                        message=outcome.error_msg,
                    )
            ra = _recovery_action_for(fc)
            return fc.value, ra.value
        except Exception as e:  # noqa: BLE001 分类失败不拖垮主流程
            logger.warning(f"[chat_execution_engine] failure 分类失败: {e}")
            return None, None
