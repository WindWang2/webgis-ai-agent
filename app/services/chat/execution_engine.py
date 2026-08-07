"""ChatExecutionEngine - 核心 Agent 对话与流式响应执行引擎。

深入封装会话状态 LRU 缓存、按 session_id 并发锁、LLM Token 流式推送、
工具循环重试、上下文组装及 SSE 事件格式化。
"""
import asyncio
import json
import logging
import re
import uuid
from typing import AsyncGenerator, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.tool_catalog import ToolCatalog

from app.core.config import settings
from app.tools.registry import ToolRegistry
from app.services.task_tracker import TaskTracker
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

logger = logging.getLogger(__name__)


class ChatExecutionEngine:
    """Agent 对话与流式响应执行引擎"""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        tool_catalog: Optional["ToolCatalog"] = None,
    ):
        self.registry = tool_registry
        self.catalog = tool_catalog
        self.base_url = settings.LLM_BASE_URL.rstrip("/")
        self.model = settings.LLM_MODEL
        self.api_key = settings.LLM_API_KEY
        self.use_prompt_caching = settings.LLM_PROMPT_CACHING_ENABLED
        self.max_rounds = 60
        self.tracker = TaskTracker()

        from app.services.chat.context_assembler import ChatContextAssembler
        self.context_assembler = ChatContextAssembler()
        self.tool_pipeline = ToolExecutionPipeline(
            self.registry, self.tracker, dispatch_fn=lambda *a, **k: self._dispatch_tool(*a, **k)
        )

        import os as _os
        _SESSION_CACHE_SIZE = int(_os.getenv("SESSION_CACHE_SIZE", "200"))
        self._sessions: LRUCache = LRUCache(capacity=_SESSION_CACHE_SIZE)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._MAX_LOCKS = 200
        self._session_owner_tokens: LRUCache = LRUCache(capacity=_SESSION_CACHE_SIZE)

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
    ) -> list[dict]:
        """从 DB 加载历史对话"""
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
            return [{"role": "system", "content": self._build_system_prompt()}]

    def get_session_owner_token(self, session_id: str) -> Optional[str]:
        """取出并消费 session 的 owner_token（SEC-08）"""
        return self._session_owner_tokens.pop(session_id, None)

    async def _get_map_state_summary(self, session_id: str) -> str:
        return await _build_map_state_summary(session_id)

    @staticmethod
    def _format_layer_lines(inventory: dict, active_layers: list[dict]) -> list[str]:
        return _format_layer_lines(inventory, active_layers)

    async def _compose_request_messages(self, session_id: str, messages: list[dict]) -> list[dict]:
        res = await self.context_assembler.assemble(session_id, messages)
        return res.to_messages()

    def _build_last_analysis_context(self, messages: list[dict]) -> str:
        return _build_last_analysis_context(messages)

    async def _get_or_create_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> list[dict]:
        if session_id in self._sessions:
            return self._sessions[session_id]

        if len(self._session_locks) > self._MAX_LOCKS:
            evict_count = self._MAX_LOCKS // 4
            for sid in list(self._session_locks.keys())[:evict_count]:
                lock_to_evict = self._session_locks[sid]
                if not lock_to_evict.locked():
                    self._session_locks.pop(sid, None)
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = await self._load_session_from_db(session_id, user_id=user_id)
        return self._sessions[session_id]

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

    async def _save_msg_async(self, session_id: str, role: str, content: str, tool_calls=None, tool_result=None, tool_call_id=None, reasoning_content=None):
        """异步保存消息到数据库"""
        try:
            if tool_result is not None and isinstance(tool_result, str) and len(tool_result) > 100000:
                tool_result = tool_result[:100000] + "...[truncated]"
            async with async_db_session() as db:
                await AsyncHistoryService(db).save_message(session_id, role, content, tool_calls, tool_result, tool_call_id, reasoning_content)
        except Exception as e:
            logger.error(f"Failed to save message asynchronously: {e}")

    async def _generate_title(self, session_id: str, first_user_message: str):
        """异步生成对话标题"""
        import httpx as _httpx
        title = None
        try:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "根据用户的首条消息，生成一个简短的对话主题标题。要求：1) 不超过12个字 2) 突出空间分析的核心对象（地名、分析类型等）3) 不要使用引号、书名号或多余的标点。只输出标题文本，不要任何额外内容。"},
                    {"role": "user", "content": first_user_message[:500]},
                ],
                "max_tokens": 64,
            }
            async with _httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                choice = resp.json()["choices"][0]
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
        env = await self._get_map_state_summary(session_id)
        try:
            return await plan_orchestrator.orchestrate_plan(
                self._planner_llm_config(), session_id, message, messages, env
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
    ) -> None:
        from app.services.chat import planner
        from app.services.chat.decision_log import ToolDecisionRecord, log_tool_decision

        if outcome.status == "error":
            quality = "error"
        elif _is_suspicious_result_fn(outcome.raw_result):
            quality = "empty"
        else:
            quality = "ok"
        plan = planner.get_plan(session_id)
        active = self.catalog.active_domains(session_id) if self.catalog else set()
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
            ))
        except Exception as e:
            logger.warning(f"[chat_execution_engine] 决策日志记录失败: {e}")

    async def _call_llm(self, messages: list[dict], tools: Optional[list] = None) -> dict:
        return await call_llm(self._llm_config(), messages, tools)

    def _call_llm_stream(self, messages: list[dict], tools: Optional[list] = None):
        return call_llm_stream(self._llm_config(), messages, tools)

    async def chat(self, message: str, session_id: Optional[str] = None, map_state: Optional[dict] = None, skill_name: Optional[str] = None, user_id: Optional[str] = None) -> dict:
        """非流式对话"""
        if not session_id:
            session_id = str(uuid.uuid4())

        if map_state:
            for k, v in map_state.items():
                await session_data_manager.set_map_state(session_id, k, v)
            from app.services.viewport_naming import schedule_populate_from_map_state
            schedule_populate_from_map_state(map_state)

        messages = await self._get_or_create_session(session_id, user_id=user_id)

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
                    return {"session_id": session_id, "content": "任务已取消", **({"owner_token": owner_token} if owner_token else {})}

                messages_with_context = await self._compose_request_messages(session_id, messages)

                tools = self._select_tools(session_id, messages)
                response = await self._call_llm(messages_with_context, tools)
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
                    if len(tc_list) > 1:
                        tasks = [
                            self.tool_pipeline.execute_tool_call(tc, session_id, task.id, executed_tools)
                            for tc in tc_list
                        ]
                        exec_results = await asyncio.gather(*tasks, return_exceptions=True)
                    else:
                        exec_results = [
                            await self.tool_pipeline.execute_tool_call(tc_list[0], session_id, task.id, executed_tools)
                        ]

                    for tc, exec_res in zip(tc_list, exec_results):
                        if isinstance(exec_res, Exception):
                            logger.error("Tool execution parallel exception for %s: %s", tc, exec_res)
                            llm_payload = f"Tool execution failed: {exec_res}"
                        else:
                            llm_payload = exec_res.llm_payload

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
            self.tracker.fail_task(task.id, "cancelled")
            raise
        except Exception:
            self.tracker.fail_task(task.id, "non-streaming chat exception")
            raise

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        if len(self._session_locks) > self._MAX_LOCKS:
            evict_count = self._MAX_LOCKS // 4
            for sid in list(self._session_locks.keys())[:evict_count]:
                lock_to_evict = self._session_locks[sid]
                if not lock_to_evict.locked():
                    self._session_locks.pop(sid, None)
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    async def chat_stream(self, message: str, session_id: Optional[str] = None, map_state: Optional[dict] = None, skill_name: Optional[str] = None, user_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """流式对话，yield SSE 格式事件含任务跟踪"""
        if not session_id:
            session_id = str(uuid.uuid4())

        lock = self._get_session_lock(session_id)
        async with lock:
            if map_state:
                for k, v in map_state.items():
                    await session_data_manager.set_map_state(session_id, k, v)
                from app.services.viewport_naming import schedule_populate_from_map_state
                schedule_populate_from_map_state(map_state)

            messages = await self._get_or_create_session(session_id, user_id=user_id)

            self._apply_skill(messages, skill_name)
            messages.append({"role": "user", "content": message})
            await self._save_msg_async(session_id, "user", message)

            task = self.tracker.create(session_id, message)
            owner_token = self.get_session_owner_token(session_id)
            task_start_data = {"task_id": task.id, "session_id": session_id}
            if owner_token:
                task_start_data["owner_token"] = owner_token
            yield sse_event("task_start", task_start_data)

        plan = await self._maybe_plan(session_id, message, messages)
        try:
            if plan is not None:
                yield sse_event("plan_ready", {
                    "session_id": session_id,
                    "task_id": task.id,
                    "intent": plan.intent,
                    "domains": plan.domains,
                    "steps": [
                        {"n": s.n, "goal": s.goal, "tool_family": s.tool_family, "done": False}
                        for s in plan.steps
                    ],
                })
        except Exception as e:
            logger.warning(f"[chat_execution_engine] plan_ready 发送失败: {e}")

        def _maybe_plan_finalized_event():
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
            messages_with_context = await self._compose_request_messages(session_id, messages)

            if self.tracker.is_cancelled(task.id):
                pf = _maybe_plan_finalized_event()
                if pf:
                    yield pf
                yield sse_event("task_cancelled", {"task_id": task.id})
                return

            tools = self._select_tools(session_id, messages)

            streamed_content_parts: list[str] = []
            assistant_msg: dict = {}
            # Phase 8: token 事件批处理。LLM 流式输出每个 token 产生一条 SSE
            # （= 一次 HTTP write）；SSEBatcher 按 32 条 / 80ms 窗口合并为更少
            # 更大的 write，降低网络与前端解析开销。只合并 token 热路径——
            # 结构事件（step_start/step_result/...）保持逐条 yield，前端事件
            # 语义不变。done 到来时 flush 尾部，保证流式内容完整到达。
            from app.utils.sse import SSEBatcher
            token_batcher = SSEBatcher(max_events=32, max_delay_s=0.08)
            async for event_type, event_data in self._call_llm_stream(messages_with_context, tools):
                if event_type == "token":
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
                        self.tool_pipeline.execute_tool_call(tc, session_id, task.id, executed_tools)
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

                try:
                    remaining: set[asyncio.Task] = set(all_tasks)
                    while remaining:
                        done, remaining = await asyncio.wait(remaining, timeout=5.0)
                        if not done:
                            yield sse_event("keep_alive", {"message": "ping"})
                            logger.debug("SSE Heartbeat sent for parallel tool wave")
                            continue
                        for t in done:
                            p = task_to_pending[t]
                            step = p["step"]
                            tool_name = p["tool_name"]
                            tool_args_dict = p["tool_args_dict"]

                            try:
                                exec_res = t.result()
                            except asyncio.CancelledError:
                                continue
                            except Exception as e:  # noqa: BLE001 防御（execute_tool_call 内部已兜底）
                                logger.error(f"[chat_execution_engine] tool task raised for {tool_name}: {e}")
                                self.tracker.fail_step(task.id, step.id, str(e))
                                yield sse_event("step_error", {
                                    "task_id": task.id,
                                    "step_id": step.id,
                                    "tool": tool_name,
                                    "error": str(e),
                                })
                                continue

                            outcome = exec_res.outcome

                            from app.services.chat import planner as _planner
                            step_n_matched = _planner.mark_step_done(session_id, tool_name, self.registry)
                            self._log_tool_decision(
                                session_id, round_index, message, tool_name,
                                tool_args_dict, outcome, len(tools or []),
                                step_n=step_n_matched,
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

                            if outcome.status == "repeated":
                                yield sse_event("step_result", {
                                    "task_id": task.id,
                                    "step_id": step.id,
                                    "tool": tool_name,
                                    "result": outcome.slim_event,
                                    "session_id": session_id,
                                })
                            elif outcome.status == "error":
                                self.tracker.fail_step(task.id, step.id, outcome.error_msg or "")
                                yield sse_event("step_error", {
                                    "task_id": task.id,
                                    "step_id": step.id,
                                    "tool": tool_name,
                                    "error": outcome.error_msg,
                                })
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
                                yield sse_event("step_result", step_payload)
                                yield sse_event("tool_result", {"name": tool_name, "result": outcome.slim_event, "session_id": session_id})

                            completion_results[step.id] = {
                                "tc": p["tc"],
                                "msg_result_str": msg_result_str,
                                "tool_name": tool_name,
                            }

                            if self.tracker.is_cancelled(task.id):
                                for r in remaining:
                                    r.cancel()
                                await asyncio.gather(*remaining, return_exceptions=True)
                                pf = _maybe_plan_finalized_event()
                                if pf:
                                    yield pf
                                yield sse_event("task_cancelled", {"task_id": task.id})
                                return
                finally:
                    # 生成器被外部取消（GeneratorExit/CancelledError）时清理未完成任务
                    for t in all_tasks:
                        if not t.done():
                            t.cancel()
                    if all_tasks:
                        await asyncio.gather(*all_tasks, return_exceptions=True)

                # Phase 3: 按原始 tc 顺序对齐 LLM 上下文 + 落库
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

                if xml_calls and tool_result_msgs:
                    messages.append({
                        "role": "user",
                        "content": "[工具执行结果]\n" + "\n".join(tool_result_msgs),
                    })

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
                yield sse_event("task_complete", {
                    "task_id": task.id,
                    "step_count": len(task.steps),
                    "summary": content[:100],
                })
                yield sse_event("done", {"session_id": session_id})
                self._fire_and_forget(self._generate_title, session_id, message)
                return

        self.tracker.fail_task(task.id, "达到最大工具调用轮数")
        pf = _maybe_plan_finalized_event()
        if pf:
            yield pf
        yield sse_event("task_error", {"task_id": task.id, "error": "达到最大轮数"})
        yield sse_event("content", {"content": "达到最大工具调用轮数", "session_id": session_id})
        yield sse_event("done", {"session_id": session_id})

    async def _dispatch_tool(
        self,
        tc: dict,
        session_id: str,
        executed_tools: set[tuple[str, str]],
    ) -> ToolDispatchResult:
        def _broadcast(sid: str, event_type: str, data: dict) -> None:
            self._fire_and_forget(broadcast_ws_event, sid, event_type, data)

        service = ToolDispatchService(registry=self.registry, fire_broadcast=_broadcast)
        return await service.dispatch(tc, session_id, executed_tools)

    async def clear_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        owner_token: Optional[str] = None,
    ) -> bool:
        """删除会话"""
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
            if session_id in self._sessions:
                del self._sessions[session_id]
            self._session_locks.pop(session_id, None)
            self._session_owner_tokens.pop(session_id, None)
            await session_data_manager.clear_session(session_id)
            from app.services.chat import planner
            planner.clear_plan(session_id)
            try:
                from app.services.chat.context.layer_schema import clear_layer_schema_cache
                clear_layer_schema_cache(session_id)
            except ImportError:
                pass
            if self.catalog is not None:
                self.catalog.reset_session(session_id)
        return deleted

    def _detect_suspicious_result(self, result: Any) -> bool:
        return _is_suspicious_result_fn(result)
