"""对话引擎 - ChatEngine 编排实现。

M1 重构（拆 1287 LOC 单体）：纯函数 / 常量 / SSE helpers / LLM 客户端 / SYSTEM_PROMPT
均已搬到 app/services/chat/ 子包。本文件只保留 ChatEngine 类本身（会话、工具调度、
SSE 流序列化、self-healing 闭环）。旧的下划线函数名（如 _slim_tool_result）作为别名
re-export，保持外部 import 兼容。
"""
import asyncio
import json
import logging
import re
import uuid
from typing import AsyncGenerator, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.tool_catalog import ToolCatalog

import httpx

from app.core.config import settings
from app.tools.registry import ToolRegistry
from app.services.task_tracker import TaskTracker, detect_geojson
from app.services.session_data import session_data_manager
from app.services.ws_service import broadcast_ws_event
from app.tools._utils import async_db_session
from app.services.history_service_async import AsyncHistoryService
from app.utils.sse import sse_event

# ─── M1: 从拆出的子模块 re-export，保留旧符号兼容 ───────────
from app.services.chat.sse_helpers import (
    LRUCache,
    MSG_MAX_CHARS as _MSG_MAX_CHARS,
    parse_minimax_xml_tool_calls as _parse_minimax_xml_tool_calls,
    normalize_tool_args as _normalize_tool_args,
    is_error_dict as _is_error_dict,
    wrap_error_dict_for_llm as _wrap_error_dict_for_llm,
    slim_tool_result as _slim_tool_result,
    calculate_bbox as _calculate_bbox,
    slim_event_result as _slim_event_result,
)
from app.services.chat.prompt import (
    SYSTEM_PROMPT,
    construct_self_healing_message as _construct_self_healing_message,
)
from app.services.chat.llm_client import LLMConfig, call_llm, call_llm_stream
from app.services.chat.context_builder import (
    build_map_state_summary as _build_map_state_summary,
    format_layer_lines as _format_layer_lines,
    build_last_analysis_context as _build_last_analysis_context,
    compose_request_messages as _compose_request_messages_fn,
)
from app.services.chat.dispatcher import (
    dispatch_tool as _dispatch_tool_fn,
    is_suspicious_result as _is_suspicious_result_fn,
)

logger = logging.getLogger(__name__)

class ChatEngine:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        tool_catalog: Optional["ToolCatalog"] = None,
    ):
        self.registry = tool_registry
        # 可选的分层工具目录。给定时按 (用户消息 + 会话粘性) 选 schema 子集，
        # 否则回退到 registry.get_schemas() 全推 (向后兼容)。
        self.catalog = tool_catalog
        self.base_url = settings.LLM_BASE_URL.rstrip("/")
        self.model = settings.LLM_MODEL
        self.api_key = settings.LLM_API_KEY
        self.use_prompt_caching = settings.LLM_PROMPT_CACHING_ENABLED
        self.max_rounds = 60
        self.tracker = TaskTracker()
        # 内存对话存储: session_id -> messages list (LRU Cache to bound memory)
        # 审计 M2：之前 capacity=50，>50 并发会话会 evict 最老的 -> 后续请求
        # 需从 DB 重载 + in-flight 持有旧 list 引用的请求可能与新请求分歧。
        # 提到 200（与 _MAX_LOCKS 对齐），生产环境可通过环境变量进一步调。
        import os as _os
        _SESSION_CACHE_SIZE = int(_os.getenv("SESSION_CACHE_SIZE", "200"))
        self._sessions: LRUCache = LRUCache(capacity=_SESSION_CACHE_SIZE)
        # 每会话锁，覆盖 _get_or_create_session 的检查-赋值竞态
        # 审计 M1：之前 _session_locks 无界增长 -- clear_session 只 pop 一个，
        # 但被遗弃的 session（从未 clear）的 Lock 永久泄漏。加 _MAX_LOCKS 上限，
        # 超限时清理最旧的（按 dict 插入顺序，Python 3.7+ 保证有序）。
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._MAX_LOCKS = 200

    def _select_tools(self, session_id: Optional[str], messages: list[dict]) -> Optional[list[dict]]:
        """选出本轮要推给 LLM 的工具 schema 列表。

        优先用 ToolCatalog（按最近一条用户消息 + 会话粘性筛选）；
        若未配置 catalog，回退到完整 get_schemas() 保持原行为。
        """
        if self.catalog is not None:
            # 取最近一条 user 消息文本作为触发源；找不到就空串（仅 tier 1）。
            user_text = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    content = m.get("content")
                    if isinstance(content, str):
                        user_text = content
                    elif isinstance(content, list):
                        # OpenAI 多模态格式
                        user_text = " ".join(
                            seg.get("text", "") for seg in content if isinstance(seg, dict)
                        )
                    break
            # 计划声明的 domain 驱动工具子集选择（与关键词检测取并集）
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
        """Build system prompt with dynamically injected skill list."""
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
        if base_url: self.base_url = base_url.rstrip("/")
        if model: self.model = model
        if api_key: self.api_key = api_key
        if use_prompt_caching is not None: self.use_prompt_caching = use_prompt_caching
        logger.info(f"ChatEngine config updated: model={self.model}, base_url={self.base_url}")

    def get_config(self) -> dict:
        """获取当前配置"""
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key": "***" + self.api_key[-4:] if self.api_key else "",
            "use_prompt_caching": self.use_prompt_caching
        }

    def _fire_and_forget(self, func, *args, **kwargs):
        """异步执行背景任务，不阻塞主线程，并捕获异常。"""
        # 如果 func 是 coroutine function，直接用 create_task
        # 如果 func 是普通函数，用 run_in_executor
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
        """Convert a DB message model to LLM-compatible dictionary."""
        d = {"role": msg.role, "content": msg.content or ""}
        if msg.reasoning_content:
            d["reasoning_content"] = msg.reasoning_content
        if msg.tool_calls:
            try:
                # Store tool_calls as list of dicts
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
        """Async DB call to load conversation history. user_id 用于新建时记录 owner（A2）。"""
        history_messages = []
        try:
            async with async_db_session() as db:
                conv = await AsyncHistoryService(db).get_or_create_conversation(session_id, user_id=user_id)
                if conv and conv.messages:
                    sorted_msgs = sorted(conv.messages, key=lambda x: x.id)
                    history_messages = [self._db_msg_to_llm(m) for m in sorted_msgs]
        except Exception as e:
            logger.warning(f"History: failed to load conversation {session_id}: {e}")

        has_system = any(m.get("role") == "system" for m in history_messages)
        if not has_system:
            history_messages.insert(0, {"role": "system", "content": self._build_system_prompt()})

        return history_messages

    # M1 深水区：上下文组装委托给 chat/context_builder.py（纯函数，方便单测）
    def _get_map_state_summary(self, session_id: str) -> str:
        return _build_map_state_summary(session_id)

    @staticmethod
    def _format_layer_lines(inventory: dict, active_layers: list[dict]) -> list[str]:
        return _format_layer_lines(inventory, active_layers)

    async def _compose_request_messages(self, session_id: str, messages: list[dict]) -> list[dict]:
        return await _compose_request_messages_fn(session_id, messages)

    def _build_last_analysis_context(self, messages: list[dict]) -> str:
        return _build_last_analysis_context(messages)


    async def _get_or_create_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> list[dict]:
        # 快路径：已缓存直接返回，绝大多数请求走这里，零锁开销。
        if session_id in self._sessions:
            return self._sessions[session_id]

        # 慢路径：可能多个 coroutine 同时进入；按 session_id 分粒度加锁，
        # 防止两个并发请求都触发 _load_session_from_db 造成双倍 DB 读 + 后续写时序错乱
        # (审计 B2: 原实现是检查-然后-赋值的经典 TOCTOU 竞态)。
        # 审计 M1：_session_locks 上限保护 -- 超过 _MAX_LOCKS 时清理最旧的，
        # 防止被遗弃 session 的 Lock 永久泄漏。
        if len(self._session_locks) > self._MAX_LOCKS:
            # 删除最旧的 25%（dict 插入顺序，Python 3.7+ 保证）
            evict_count = self._MAX_LOCKS // 4
            for sid in list(self._session_locks.keys())[:evict_count]:
                # 只删当前没被持有的锁（避免打断在途请求）
                lock_to_evict = self._session_locks[sid]
                if not lock_to_evict.locked():
                    self._session_locks.pop(sid, None)
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            # 重新检查：第一个进锁的协程加载完，后续协程拿到锁后应直接复用
            if session_id not in self._sessions:
                self._sessions[session_id] = await self._load_session_from_db(session_id, user_id=user_id)
        return self._sessions[session_id]

    def _apply_skill(self, messages: list[dict], skill_name: Optional[str]) -> None:
        """注入或刷新 skill 指令，保证 messages 里同一 skill 只有一份 system body。

        会话累积多轮带 skill_name 的请求时，旧实现会把同一段 system body 不断 append；
        这里先扫历史移除该 skill 已有的 system，再追加最新版，避免上下文膨胀。
        """
        if not skill_name:
            return
        from app.tools.skills import get_md_skill
        skill = get_md_skill(skill_name)
        if not skill:
            return
        marker = f"[Skill指令: {skill_name}]"
        # 移除该 skill 在历史里残留的旧 system 消息（去重 + 重新置于尾部）
        messages[:] = [
            m for m in messages
            if not (m.get("role") == "system" and isinstance(m.get("content"), str) and m["content"].startswith(marker))
        ]
        messages.append({"role": "system", "content": f"{marker}\n\n{skill['body']}"})

    async def _save_msg_async(self, session_id: str, role: str, content: str, tool_calls=None, tool_result=None, tool_call_id=None, reasoning_content=None):
        """异步保存消息到数据库，带重试机制。

        审计 M8：之前 _save_msg_async 对 tool_result content 不截断，但 streaming
        路径（chat_stream）截断到 100000 字符。非流式 chat() 路径用 _save_msg_async
        保存 tool result 时不截断 -> 超大 GeoJSON tool result 可能撑爆 SQLite 行。
        统一截断到 100000（与 streaming 一致）。
        """
        try:
            # 审计 M8：tool_result 可能是 MB 级 GeoJSON；截断到 100000 字符
            # （与 chat_stream 的 db_save_content[:100000] 一致）。
            if tool_result is not None and isinstance(tool_result, str) and len(tool_result) > 100000:
                tool_result = tool_result[:100000] + "...[truncated]"
            async with async_db_session() as db:
                await AsyncHistoryService(db).save_message(session_id, role, content, tool_calls, tool_result, tool_call_id, reasoning_content)
        except Exception as e:
            logger.error(f"Failed to save message asynchronously: {e}")

    async def _generate_title(self, session_id: str, first_user_message: str):
        """异步生成对话标题。"""
        import httpx as _httpx
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

            # Validate: strip quotes and enforce length
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

    def _llm_config(self) -> LLMConfig:
        """打包当前 ChatEngine 的 LLM 配置成一个不可变 dataclass，传给 chat/llm_client。"""
        return LLMConfig(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            use_prompt_caching=self.use_prompt_caching,
        )

    def _planner_llm_config(self) -> LLMConfig:
        """规划阶段的 LLM 配置：LLM_PLANNER_MODEL 非空时覆盖 model。"""
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
        """启发式门控通过则跑规划阶段，返回新生成的 Plan；跳过 / 失败均返回 None。

        规划是增强，失败静默降级——chat_stream 据返回值决定是否发 plan_ready 事件。
        """
        from app.services.chat import planner
        has_plan = planner.get_plan(session_id) is not None
        if not planner.should_plan(message, messages, has_plan):
            return None
        env = self._get_map_state_summary(session_id)
        try:
            return await planner.make_plan(self._planner_llm_config(), session_id, message, env)
        except Exception as e:  # noqa: BLE001 — 规划绝不能拖垮对话
            logger.warning(f"[chat_engine] 规划阶段异常，降级无计划: {e}")
            return None

    def _log_tool_decision(
        self,
        session_id: str,
        round_index: int,
        message: str,
        tool_name: str,
        tool_args: dict,
        outcome: dict,
        subset_size: int,
        step_n: int | None = None,
    ) -> None:
        """落一条工具决策记录。可观测性，绝不影响主流程。

        subset_size 由调用方传入本轮已算好的工具子集大小——不在此处重算，
        因为 select_schemas 会衰减 ToolCatalog 的 sticky TTL，重复调用会
        让 sticky domain 过早失效。

        step_n 由调用方先调用 planner.mark_step_done 算好，本方法只写入
        决策日志——与 chat_stream 的 SSE 事件共用一个 step_n 值。
        """
        from app.services.chat import planner
        from app.services.chat.decision_log import ToolDecisionRecord, log_tool_decision
        from app.services.chat.dispatcher import is_suspicious_result

        if outcome.get("is_error"):
            quality = "error"
        elif is_suspicious_result(outcome.get("result")):
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
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[chat_engine] 决策日志记录失败: {e}")

    async def _call_llm(self, messages: list[dict], tools: Optional[list] = None) -> dict:
        """委托给 chat/llm_client.call_llm — 历史方法名保留以免外部代码 / 测试断裂。"""
        return await call_llm(self._llm_config(), messages, tools)

    def _call_llm_stream(self, messages: list[dict], tools: Optional[list] = None):
        """委托给 chat/llm_client.call_llm_stream — 返回 async generator。

        历史上这里曾经有一行死掉的 `yield (...)` 跟在 return 后面。即便不可达，
        Python 解析时也会把整个函数标记为 sync generator —— 结果 `async for` 时
        会拿到 sync generator 抛 "requires __aiter__, got generator"。删掉就好。
        """
        return call_llm_stream(self._llm_config(), messages, tools)

    async def chat(self, message: str, session_id: Optional[str] = None, map_state: Optional[dict] = None, skill_name: Optional[str] = None, user_id: Optional[str] = None) -> dict:
        """非流式对话"""
        if not session_id:
            session_id = str(uuid.uuid4())

        # 同步前端实时状态到 Session
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

        # 非流式路径也需要重复调用拦截，避免 LLM 在同一任务里循环刷同一工具
        executed_tools: set[tuple[str, str]] = set()

        # 审计 M4：非流式 chat() 之前不注册 TaskTracker -> 通过 /chat/completions
        # 发起的任务在 /tasks 端点不可见，也无法 cancel。注册一个 task 让它可见。
        task = self.tracker.create(session_id, message)

        # FC 循环
        try:
            for _ in range(self.max_rounds):
                # 审计 M4：cooperative cancel 检查（与 chat_stream 一致）
                if self.tracker.is_cancelled(task.id):
                    return {"session_id": session_id, "content": "任务已取消"}

                messages_with_context = await self._compose_request_messages(session_id, messages)

                tools = self._select_tools(session_id, messages)
                response = await self._call_llm(messages_with_context, tools)
                choice = response.get("choices", [{}])[0]
                assistant_msg = choice.get("message", {})

                # 提取文本内容，优先 content，次之 reasoning
                raw_content = assistant_msg.get("content") or ""
                reasoning = assistant_msg.get("reasoning") or assistant_msg.get("reasoning_content") or ""

                # 检查是否有 tool_calls（OpenAI 标准格式或 MiniMax XML 格式）
                standard_calls = assistant_msg.get("tool_calls") or []
                xml_calls: list[dict] = []
                if not standard_calls:
                    if "minimax:tool_call" in raw_content:
                        xml_calls = _parse_minimax_xml_tool_calls(raw_content)

                tc_list = standard_calls or xml_calls

                if tc_list:
                    content_text = raw_content
                    if xml_calls:
                        # Strip XML artifact from content before storing
                        content_text = re.sub(r'\s*minimax:tool_call[\s\S]*', '', content_text).strip()

                    entry: dict = {"role": "assistant", "content": content_text}
                    if reasoning:
                        entry["reasoning_content"] = reasoning
                    if standard_calls:
                        entry["tool_calls"] = standard_calls
                    messages.append(entry)
                    await self._save_msg_async(session_id, "assistant", content_text, tc_list, reasoning_content=reasoning)

                    tool_result_msgs: list[str] = []
                    for tc in tc_list:
                        # 审计 M4：注册 step（与 chat_stream 一致），让进度可查
                        tool_name = tc["function"]["name"]
                        tool_args_dict = tc["function"]["arguments"]
                        if isinstance(tool_args_dict, str):
                            try:
                                tool_args_dict = json.loads(tool_args_dict)
                            except Exception as e:
                                tool_args_dict = {}
                        step = self.tracker.start_step(task.id, tool_name, tool_args_dict if isinstance(tool_args_dict, dict) else {})
                        outcome = await self._dispatch_tool(tc, session_id, executed_tools)
                        self.tracker.complete_step(task.id, step.id, outcome["result"])
                        llm_payload = outcome["llm_payload"]

                        if standard_calls:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": llm_payload,
                            })
                            await self._save_msg_async(session_id, "tool", "", None, llm_payload, tc["id"])
                        else:
                            tool_result_msgs.append(f"{tc['function']['name']}: {llm_payload}")

                    if xml_calls and tool_result_msgs:
                        messages.append({
                            "role": "user",
                            "content": "[工具执行结果]\n" + "\n".join(tool_result_msgs),
                        })
                    continue  # 继续循环让 LLM 处理工具结果
                else:
                    # 无 tool_calls，最终回复
                    content = raw_content

                    entry = {"role": "assistant", "content": content}
                    if reasoning:
                        entry["reasoning_content"] = reasoning
                    messages.append(entry)
                    await self._save_msg_async(session_id, "assistant", content, reasoning_content=reasoning)
                    self.tracker.complete_task(task.id)
                    return {"session_id": session_id, "content": content, "reasoning": reasoning}

            self.tracker.complete_task(task.id)
            return {"content": "达到最大工具调用轮数", "session_id": session_id}
        except Exception as e:
            self.tracker.fail_task(task.id, "non-streaming chat exception")
            raise

    async def chat_stream(self, message: str, session_id: Optional[str] = None, map_state: Optional[dict] = None, skill_name: Optional[str] = None, user_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """流式对话，yield SSE 格式事件含任务跟踪"""
        if not session_id:
            session_id = str(uuid.uuid4())

        # 把前端上报的实时地图状态同步进 session_data_manager，下一轮注入感知用
        if map_state:
            for k, v in map_state.items():
                await session_data_manager.set_map_state(session_id, k, v)
            from app.services.viewport_naming import schedule_populate_from_map_state
            schedule_populate_from_map_state(map_state)

        messages = await self._get_or_create_session(session_id, user_id=user_id)

        self._apply_skill(messages, skill_name)
        messages.append({"role": "user", "content": message})
        await self._save_msg_async(session_id, "user", message)

        # 创建任务
        task = self.tracker.create(session_id, message)
        yield sse_event("task_start", {"task_id": task.id, "session_id": session_id})

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
        except Exception as e:  # noqa: BLE001 — 发事件失败永远不能拖垮工具循环
            logger.warning(f"[chat_engine] plan_ready 发送失败: {e}")

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
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[chat_engine] plan_finalized 构造失败: {e}")
                return None

        # 初始化局部哨兵，防止 AI 在单次任务中陷入相同指令的无限循环
        executed_tools = set()

        for round_index in range(self.max_rounds):
            messages_with_context = await self._compose_request_messages(session_id, messages)

            # 检查取消
            if self.tracker.is_cancelled(task.id):
                pf = _maybe_plan_finalized_event()
                if pf: yield pf
                yield sse_event("task_cancelled", {"task_id": task.id})
                return

            tools = self._select_tools(session_id, messages)

            # ── Streaming LLM call: yield tokens in real-time ──
            streamed_content_parts: list[str] = []
            assistant_msg: dict = {}
            async for event_type, event_data in self._call_llm_stream(messages_with_context, tools):
                if event_type == "token":
                    # Forward each token chunk to the frontend for real-time display
                    streamed_content_parts.append(event_data["content"])
                    yield sse_event("token", {"content": event_data["content"], "is_reasoning": event_data.get("is_reasoning", False), "session_id": session_id})
                elif event_type == "done":
                    assistant_msg = event_data["message"]

            # 检查是否有 tool_calls（OpenAI 标准格式或 MiniMax XML 格式）
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
                
                # 将规划文本推送到前端
                if content_text:
                    yield sse_event("content", {"content": "\n", "session_id": session_id})

                entry: dict = {"role": "assistant", "content": content_text}
                if reasoning:
                    entry["reasoning_content"] = reasoning
                if standard_calls:
                    entry["tool_calls"] = standard_calls
                messages.append(entry)
                # 保存完整 tc_list（含 MiniMax XML 解析出的 call），避免 DB 重载后链路断裂
                await self._save_msg_async(session_id, "assistant", content_text, tc_list, reasoning_content=reasoning)

                tool_result_msgs: list[str] = []

                for tc in tc_list:
                    tool_name = tc["function"]["name"]
                    tool_args_raw = tc["function"]["arguments"]

                    # 解析参数用于跟踪
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

                    # 用统一 helper 跑工具，外层包一层心跳保活
                    dispatch_task = asyncio.create_task(
                        self._dispatch_tool(tc, session_id, executed_tools)
                    )
                    try:
                        while not dispatch_task.done():
                            done, _pending = await asyncio.wait([dispatch_task], timeout=5.0)
                            if not done:
                                yield ": keep-alive\n\n"
                                logger.debug(f"SSE Heartbeat sent for tool: {tool_name}")
                        outcome = await dispatch_task
                    except (asyncio.CancelledError, GeneratorExit):
                        # 审计 C5：SSE 客户端断开时 FastAPI 会 cancel 生成器，
                        # 但之前 dispatch_task 没被显式 cancel/await → 它在后台继续
                        # 跑（Celery 派发、GeoJSON 序列化、DB 写入）直到自然完成，
                        # 浪费资源且无界增长。这里显式 cancel 并让上层重抛。
                        dispatch_task.cancel()
                        raise

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
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"[chat_engine] plan_step_done 发送失败: {e}")

                    msg_result_str = outcome["llm_payload"]

                    if outcome["repeated"]:
                        # 重复调用拦截：不更新 tracker（没有真实执行），只发 step_result
                        yield sse_event("step_result", {
                            "task_id": task.id,
                            "step_id": step.id,
                            "tool": tool_name,
                            "result": outcome["slim_event"],
                            "session_id": session_id,
                        })
                    elif outcome["is_error"]:
                        self.tracker.fail_step(task.id, step.id, outcome["error_msg"])
                        yield sse_event("step_error", {
                            "task_id": task.id,
                            "step_id": step.id,
                            "tool": tool_name,
                            "error": outcome["error_msg"],
                        })
                        yield sse_event("tool_result", {"name": tool_name, "result": msg_result_str, "session_id": session_id})
                    else:
                        self.tracker.complete_step(task.id, step.id, outcome["result"])
                        yield sse_event("step_result", {
                            "task_id": task.id,
                            "step_id": step.id,
                            "tool": tool_name,
                            "result": outcome["slim_event"],
                            "geojson_ref": outcome["geojson_ref"],
                            "has_geojson": outcome["has_geojson"],
                            "session_id": session_id,
                        })
                        yield sse_event("tool_result", {"name": tool_name, "result": outcome["slim_event"], "session_id": session_id})

                    if standard_calls:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": msg_result_str,
                        })
                        # 写入 DB 时再次截断，防止单条消息体积过大撑爆 SQLite
                        db_save_content = msg_result_str[:100000] if len(msg_result_str) > 100000 else msg_result_str
                        await self._save_msg_async(session_id, "tool", "", None, db_save_content, tc["id"])
                    else:
                        tool_result_msgs.append(f"{tool_name}: {msg_result_str}")

                    # 检查取消（每步执行后）
                    if self.tracker.is_cancelled(task.id):
                        pf = _maybe_plan_finalized_event()
                        if pf: yield pf
                        yield sse_event("task_cancelled", {"task_id": task.id})
                        return

                if xml_calls and tool_result_msgs:
                    messages.append({
                        "role": "user",
                        "content": "[工具执行结果]\n" + "\n".join(tool_result_msgs),
                    })

                continue
            else:
                # 最终回复
                content = raw_content
                
                entry = {"role": "assistant", "content": content}
                if reasoning:
                    entry["reasoning_content"] = reasoning
                messages.append(entry)
                await self._save_msg_async(session_id, "assistant", content, reasoning_content=reasoning)

                # Emit a final content event (empty, since tokens were already sent)
                # This signals to the frontend that the message is complete
                yield sse_event("content", {"content": "", "session_id": session_id, "streaming_done": True})

                # task_complete
                pf = _maybe_plan_finalized_event()
                if pf: yield pf
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
        if pf: yield pf
        yield sse_event("task_error", {"task_id": task.id, "error": "达到最大轮数"})
        yield sse_event("content", {"content": "达到最大工具调用轮数", "session_id": session_id})
        yield sse_event("done", {"session_id": session_id})

    async def _dispatch_tool(
        self,
        tc: dict,
        session_id: str,
        executed_tools: set[tuple[str, str]],
    ) -> dict:
        """委托给 chat/dispatcher.dispatch_tool — 显式注入 registry 与 WS 广播回调。"""
        def _broadcast(sid: str, event_type: str, data: dict) -> None:
            self._fire_and_forget(broadcast_ws_event, sid, event_type, data)

        return await _dispatch_tool_fn(
            tc,
            session_id,
            executed_tools,
            registry=self.registry,
            fire_broadcast=_broadcast,
        )

    async def clear_session(self, session_id: str, user_id: Optional[str] = None) -> bool:
        """删除会话；user_id 用于所有权检查（A2）。

        返回是否真的删除：False = 不存在或越权（让路由层映射成 404）。
        匿名调用 / NULL owner 仍走旧能力令牌语义。
        """
        deleted = False
        try:
            async with async_db_session() as db:
                deleted = await AsyncHistoryService(db).delete_session(session_id, user_id=user_id)
        except Exception as e:
            logger.warning(f"History: failed to delete session {session_id}: {e}")
            return False
        if deleted:
            if session_id in self._sessions:
                del self._sessions[session_id]
            self._session_locks.pop(session_id, None)
            await session_data_manager.clear_session(session_id)
            from app.services.chat import planner
            planner.clear_plan(session_id)
            # 审计 M9：清 layer_schema_cache，否则清空后重建同 session_id 会读到旧 schema
            try:
                from app.services.chat.context.layer_schema import clear_layer_schema_cache
                clear_layer_schema_cache(session_id)
            except ImportError:
                pass
            if self.catalog is not None:
                self.catalog.reset_session(session_id)
        return deleted

    def _detect_suspicious_result(self, result: Any) -> bool:
        """委托给 chat/dispatcher.is_suspicious_result。"""
        return _is_suspicious_result_fn(result)

