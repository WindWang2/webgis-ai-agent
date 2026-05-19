"""对话引擎 - ChatEngine 编排实现。

M1 重构（拆 1287 LOC 单体）：纯函数 / 常量 / SSE helpers / LLM 客户端 / SYSTEM_PROMPT
均已搬到 app/services/chat/ 子包。本文件只保留 ChatEngine 类本身（会话、工具调度、
SSE 流序列化、self-healing 闭环）。旧的下划线函数名（如 _slim_tool_result）作为别名
re-export，保持外部 import 兼容。
"""
import asyncio
import json
import logging
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
        self.max_rounds = 30
        self.tracker = TaskTracker()
        # 内存对话存储: session_id -> messages list (LRU Cache to bound memory)
        self._sessions: LRUCache = LRUCache(capacity=50)
        # 每会话锁，覆盖 _get_or_create_session 的检查-赋值竞态
        self._session_locks: dict[str, asyncio.Lock] = {}

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
            schemas = self.catalog.select_schemas(user_text, session_id=session_id)
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

    def _get_map_state_summary(self, session_id: str) -> str:
        """构造一份紧凑的当前地图状态摘要，作为系统消息注入。

        双源策略：优先用后端 inventory 的 ref_id 数据引用；inventory 为空时
        回退到前端 map_state.layers 上报的活跃图层（页面刷新/新 Session 时）。
        只输出事实，不在 prompt 里夹杂"应该怎么做"的元指令。
        """
        import datetime
        import json as _json

        state = session_data_manager.get_map_state(session_id)
        inventory = session_data_manager.list_refs(session_id)
        viewport = state.get("viewport") or {}
        center = viewport.get("center")
        zoom = viewport.get("zoom")
        bearing = viewport.get("bearing", 0) or 0
        pitch = viewport.get("pitch", 0) or 0
        bounds = viewport.get("bounds")
        base_layer = state.get("base_layer", "OSM 地图")
        is_3d = state.get("is_3d", False)
        active_layers = state.get("layers", []) or []

        lines = [
            "[环境感知 — 当前地图实时状态，必读，不要凭空假设位置]",
            f"- 时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]

        user_location = state.get("user_location")
        if isinstance(user_location, dict):
            lines.append(
                f"- 用户位置: {user_location.get('lng', 0):.6f}, {user_location.get('lat', 0):.6f} "
                f"(±{user_location.get('accuracy', '?')}m)"
            )
        else:
            lines.append("- 用户位置: 未授权")

        if isinstance(center, (list, tuple)) and len(center) == 2 and zoom is not None:
            viewport_line = (
                f"- 视口中心(WGS84 经纬度): lng={center[0]:.4f}, lat={center[1]:.4f}, zoom={zoom:.2f}"
            )
            if bearing:
                viewport_line += f", bearing={bearing:.0f}°"
            if pitch:
                viewport_line += f", pitch={pitch:.0f}°"
            if is_3d:
                viewport_line += ", 3D"
            lines.append(viewport_line)
        else:
            lines.append("- 视口: 未知（前端尚未上报，回答位置类问题前请先告知用户无法获取地图状态）")

        if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
            w, s, e, n = bounds
            lines.append(f"- 可视范围: W{w:.3f} S{s:.3f} E{e:.3f} N{n:.3f}")

        lines.append(f"- 底图: {base_layer}")

        layer_lines = self._format_layer_lines(inventory, active_layers)
        if layer_lines:
            lines.append("- 活跃图层:")
            lines.extend(f"  * {ln}" for ln in layer_lines)
        else:
            lines.append("- 活跃图层: 无")

        event_log = session_data_manager.get_event_log(session_id)
        if event_log:
            lines.append("- 近期操作:")
            for evt in event_log[-5:]:
                lines.append(f"  * {evt['event']}: {_json.dumps(evt['data'], ensure_ascii=False)}")

        return "\n".join(lines)

    @staticmethod
    def _format_layer_lines(inventory: dict, active_layers: list[dict]) -> list[str]:
        """渲染图层一行式描述。inventory 优先，缺失时回退到前端上报。"""
        visibility_map = {l.get("id"): l for l in active_layers if l.get("id")}
        out: list[str] = []
        if inventory:
            for ref_id, alias in inventory.items():
                meta = visibility_map.get(ref_id) or next(
                    (m for aid, m in visibility_map.items() if aid in ref_id or ref_id in aid),
                    {},
                )
                visible = meta.get("visible")
                status = "可见" if visible is True else "隐藏" if visible is False else "未知"
                attrs = []
                if alias:
                    attrs.append(f"别名={alias}")
                if meta.get("type"):
                    attrs.append(f"类型={meta['type']}")
                if meta.get("featureCount") is not None:
                    attrs.append(f"要素={meta['featureCount']}")
                if meta.get("style", {}).get("color"):
                    attrs.append(f"色={meta['style']['color']}")
                tail = f" [{', '.join(attrs)}]" if attrs else ""
                out.append(f"{ref_id}{tail} ({status})")
            return out
        for layer in active_layers:
            lid = layer.get("id", "unknown")
            name = layer.get("name", lid)
            attrs = []
            if layer.get("type"):
                attrs.append(f"类型={layer['type']}")
            if layer.get("featureCount") is not None:
                attrs.append(f"要素={layer['featureCount']}")
            opacity = layer.get("opacity", 1.0)
            attrs.append(f"不透明度={opacity:.0%}")
            status = "可见" if layer.get("visible") else "隐藏"
            out.append(f"{name} (id={lid}, {', '.join(attrs)}) ({status})")
        return out

    def _compose_request_messages(self, session_id: str, messages: list[dict]) -> list[dict]:
        """组装一次 LLM 请求的消息列表：SYSTEM_PROMPT + 实时感知 + (可选)对话上下文摘要 + 历史。

        感知状态只在每次请求前注入一次，不再写进工具结果里——保持历史紧凑。
        chat() 与 chat_stream() 共享此入口，避免两条路径行为漂移。
        """
        env_summary = self._get_map_state_summary(session_id)
        logger.debug(f"[ENV-INJECT] session={session_id}\n{env_summary}")

        # Merge env summary directly into the system prompt so it is always read.
        # Injecting it as a separate system message is unreliable — many LLMs
        # (including MiniMax) silently drop all but the first system entry.
        sys_msg = dict(messages[0])
        sys_msg["content"] = sys_msg["content"] + "\n\n" + env_summary

        head = [sys_msg]
        last_ctx = self._build_last_analysis_context(messages)
        if last_ctx:
            head.append({"role": "system", "content": last_ctx})
        head.extend(messages[1:])
        return head

    def _build_last_analysis_context(self, messages: list[dict]) -> str:
        """从最近的历史消息中提取分析上下文摘要，帮助 LLM 维持追问连贯性。"""
        # 找到最近的 assistant 文本消息（非 tool_call）
        last_user_msg = ""
        last_assistant_msg = ""
        data_refs: list[str] = []

        for msg in reversed(messages):
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            if role == "assistant" and content and not last_assistant_msg:
                # 截取前 300 字作为摘要
                last_assistant_msg = content[:300]
            elif role == "user" and content and not last_user_msg:
                last_user_msg = content[:200]
            # 收集已有的 ref 数据引用
            if "ref:" in content:
                import re
                refs = re.findall(r'(ref:[\w-]+)', content)
                data_refs.extend(refs)
            if last_assistant_msg and last_user_msg:
                break

        if not last_assistant_msg and not last_user_msg:
            return ""

        ctx = "[最近对话上下文]\n"
        if last_user_msg:
            ctx += f"- 用户上一次请求：{last_user_msg}\n"
        if last_assistant_msg:
            ctx += f"- 你上一次回复摘要：{last_assistant_msg}...\n"
        if data_refs:
            # 去重保留最后 5 个
            unique_refs = list(dict.fromkeys(data_refs))[-5:]
            ctx += f"- 可复用的数据引用：{', '.join(unique_refs)}\n"
        ctx += "\n如果用户的新消息是简短的追问（如「绘制热力图」「换个颜色」「放大看看」），请基于以上上下文直接执行，不要重新询问区域或数据。"
        return ctx


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
        """异步保存消息到数据库，带重试机制。"""
        try:
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

    async def _call_llm(self, messages: list[dict], tools: Optional[list] = None) -> dict:
        """委托给 chat/llm_client.call_llm — 历史方法名保留以免外部代码 / 测试断裂。"""
        return await call_llm(self._llm_config(), messages, tools)

    def _call_llm_stream(self, messages: list[dict], tools: Optional[list] = None):
        """委托给 chat/llm_client.call_llm_stream — 返回 async generator。"""
        return call_llm_stream(self._llm_config(), messages, tools)

        yield ("done", {"message": assembled_message, "finish_reason": finish_reason})

    async def chat(self, message: str, session_id: Optional[str] = None, map_state: Optional[dict] = None, skill_name: Optional[str] = None, user_id: Optional[str] = None) -> dict:
        """非流式对话"""
        if not session_id:
            session_id = str(uuid.uuid4())

        # 同步前端实时状态到 Session
        if map_state:
            for k, v in map_state.items():
                session_data_manager.set_map_state(session_id, k, v)

        messages = await self._get_or_create_session(session_id, user_id=user_id)

        self._apply_skill(messages, skill_name)
        messages.append({"role": "user", "content": message})
        await self._save_msg_async(session_id, "user", message)

        # 非流式路径也需要重复调用拦截，避免 LLM 在同一任务里循环刷同一工具
        executed_tools: set[tuple[str, str]] = set()

        # FC 循环
        for _ in range(self.max_rounds):
            messages_with_context = self._compose_request_messages(session_id, messages)

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
                    outcome = await self._dispatch_tool(tc, session_id, executed_tools)
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
                return {"session_id": session_id, "content": content, "reasoning": reasoning}

        return {"content": "达到最大工具调用轮数", "session_id": session_id}

    async def chat_stream(self, message: str, session_id: Optional[str] = None, map_state: Optional[dict] = None, skill_name: Optional[str] = None, user_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """流式对话，yield SSE 格式事件含任务跟踪"""
        if not session_id:
            session_id = str(uuid.uuid4())

        # 把前端上报的实时地图状态同步进 session_data_manager，下一轮注入感知用
        if map_state:
            for k, v in map_state.items():
                session_data_manager.set_map_state(session_id, k, v)

        messages = await self._get_or_create_session(session_id, user_id=user_id)

        self._apply_skill(messages, skill_name)
        messages.append({"role": "user", "content": message})
        await self._save_msg_async(session_id, "user", message)

        # 创建任务
        task = self.tracker.create(session_id, message)
        yield sse_event("task_start", {"task_id": task.id, "session_id": session_id})

        # 初始化局部哨兵，防止 AI 在单次任务中陷入相同指令的无限循环
        executed_tools = set()

        for _ in range(self.max_rounds):
            messages_with_context = self._compose_request_messages(session_id, messages)

            # 检查取消
            if self.tracker.is_cancelled(task.id):
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
                    yield sse_event("token", {"content": event_data["content"], "session_id": session_id})
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
                    while not dispatch_task.done():
                        done, _pending = await asyncio.wait([dispatch_task], timeout=5.0)
                        if not done:
                            yield ": keep-alive\n\n"
                            logger.debug(f"SSE Heartbeat sent for tool: {tool_name}")
                    outcome = await dispatch_task

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
        yield sse_event("task_error", {"task_id": task.id, "error": "达到最大轮数"})
        yield sse_event("content", {"content": "达到最大工具调用轮数", "session_id": session_id})
        yield sse_event("done", {"session_id": session_id})

    async def _dispatch_tool(
        self,
        tc: dict,
        session_id: str,
        executed_tools: set[tuple[str, str]],
    ) -> dict:
        """统一的工具执行入口，chat 与 chat_stream 共用。

        负责：
        1. 重复调用拦截（同 session 内同参数同名工具只执行一次）
        2. 调用 registry.dispatch（含 ref 解析、参数校验、异常包装）
        3. 错误自愈消息构造（同时识别 std_error_response 字典与异常抛出两条路径）
        4. 大型结果压缩 + GeoJSON 落入 session_data_manager 形成 ref 游标
        5. 把工具动作回写到 event_log，让下一轮 [环境感知] 反映最新地图变化

        返回 dict 字段：
            - result: 原始工具返回（可能是 dict/str/list/error_dict）
            - llm_payload: 给 LLM 看的字符串（已压缩、已附加自愈提示）
            - slim_event: 给前端 SSE 用的脱敏版本
            - geojson_ref: 若工具产出新图层数据则非空
            - has_geojson: bool
            - repeated: 是否被重复调用拦截
            - is_error: 是否是错误（影响前端 step_error 派发）
            - error_msg: 错误消息字符串（仅 is_error 时有值）
        """
        tool_name = tc["function"]["name"]
        tool_args_raw = tc["function"]["arguments"]

        tool_key = (tool_name, _normalize_tool_args(tool_args_raw))
        if tool_key in executed_tools:
            note = (
                f"[重复调用拦截] {tool_name} 已在本任务中以相同参数成功执行，"
                f"结果已生效。请直接基于既有结果汇报，不要再次调用。"
            )
            return {
                "result": {"success": True, "note": "Loop blocked"},
                "llm_payload": note,
                "slim_event": {"success": True, "note": "Loop blocked"},
                "geojson_ref": None,
                "has_geojson": False,
                "repeated": True,
                "is_error": False,
                "error_msg": "",
            }
        executed_tools.add(tool_key)

        is_error = False
        error_msg = ""
        try:
            result = await self.registry.dispatch(tool_name, tool_args_raw, session_id=session_id)
        except Exception as e:
            # 这里只有 _resolve_references 抛 ValueError 才会走到（其余路径都返回 std_error_response dict）
            error_type = "参数校验失败" if isinstance(e, ValueError) and "失败" in str(e) else "执行出错"
            error_msg = str(e)
            logger.error(f"Tool {tool_name} error: {e}")
            llm_payload = _construct_self_healing_message(tool_name, error_msg, error_type)
            return {
                "result": {"success": False, "code": error_type, "message": error_msg, "data": None},
                "llm_payload": llm_payload,
                "slim_event": {"success": False, "code": error_type, "message": error_msg},
                "geojson_ref": None,
                "has_geojson": False,
                "repeated": False,
                "is_error": True,
                "error_msg": error_msg,
            }

        # registry 返回 std_error_response dict 的统一路径
        if _is_error_dict(result):
            is_error = True
            error_msg = result.get("message", "")
            llm_payload = _wrap_error_dict_for_llm(tool_name, result)
            session_data_manager.append_event(
                session_id,
                "tool_failed",
                {"tool": tool_name, "code": result.get("code"), "message": error_msg[:200]},
            )
            return {
                "result": result,
                "llm_payload": llm_payload,
                "slim_event": _slim_event_result(result),
                "geojson_ref": None,
                "has_geojson": False,
                "repeated": False,
                "is_error": True,
                "error_msg": error_msg,
            }

        # 正常路径：把大型 GeoJSON 存为 ref，热力图等元数据落地
        geojson_ref: Optional[str] = None
        target_data = None
        if isinstance(result, dict):
            if isinstance(result.get("geojson"), (dict, list)):
                target_data = result["geojson"]
            elif result.get("type") == "FeatureCollection" and "features" in result:
                target_data = result
            if target_data is not None:
                geojson_ref = session_data_manager.store(session_id, target_data, prefix="geojson")
            if result.get("type") == "heatmap_raster":
                session_data_manager.store(session_id, result, prefix="heatmap")

        result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
        llm_payload = _slim_tool_result(result, result_str, geojson_ref) or result_str

        if self._detect_suspicious_result(result):
            llm_payload += (
                "\n\n(注意: 此操作未返回任何空间要素或有效数据。请检查查询范围、关键词或图层名称，"
                "并根据需要尝试不同的参数。不要重复完全相同的调用。)"
            )

        # 把工具动作回写到事件日志：下一轮 [环境感知] 会看到最新地图变化
        event_payload: dict = {"tool": tool_name}
        if geojson_ref:
            event_payload["ref"] = geojson_ref
            # ─── REAL-TIME MAP UPDATE BROADCAST ───
            # 立即通过 WebSocket 推送给前端，让地图在对话流还在生成时就开始渲染
            self._fire_and_forget(
                broadcast_ws_event, 
                session_id, 
                "geojson_update", 
                {"step_id": tc.get("id"), "geojson": geojson_ref, "tool": tool_name}
            )
        
        if isinstance(result, dict):
            for k in ("layer_id", "bbox", "feature_count", "alias"):
                v = result.get(k)
                if v is not None:
                    event_payload[k] = v
        session_data_manager.append_event(session_id, "tool_executed", event_payload)

        return {
            "result": result,
            "llm_payload": llm_payload,
            "slim_event": _slim_event_result(result),
            "geojson_ref": geojson_ref,
            "has_geojson": detect_geojson(result),
            "repeated": False,
            "is_error": is_error,
            "error_msg": error_msg,
        }

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
            session_data_manager.clear_session(session_id)
        return deleted

    def _detect_suspicious_result(self, result: Any) -> bool:
        """检测工具返回的结果是否"可疑"（空数据/错误响应），用于触发自愈提示。"""
        if not result:
            return True

        if isinstance(result, dict):
            # std_error_response 形状
            if result.get("success") is False:
                return True
            # GeoJSON 检查
            if result.get("type") == "FeatureCollection" and not result.get("features"):
                return True
            # 通用结果列表检查
            if "data" in result and isinstance(result["data"], list) and not result["data"]:
                return True
            # OSM POI 检查
            if "poi_count" in result and result["poi_count"] == 0:
                return True

        if isinstance(result, list) and not result:
            return True

        return False

