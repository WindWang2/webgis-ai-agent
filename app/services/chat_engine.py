"""对话引擎 - 直接 HTTPX 调用（避免 OpenAI SDK 版本问题）"""
import asyncio
import json
import logging
import re
import uuid
from typing import AsyncGenerator, Optional, Any

import httpx

from app.core.config import settings
from app.tools.registry import ToolRegistry
from app.services.task_tracker import TaskTracker, detect_geojson
from app.services.session_data import session_data_manager
from collections import OrderedDict
from app.core.database import SessionLocal
from app.services.history_service import HistoryService

class LRUCache(OrderedDict):
    """Simple LRU Cache to bound memory usage"""
    def __init__(self, capacity=100):
        super().__init__()
        self.capacity = capacity

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if len(self) > self.capacity:
            oldest = next(iter(self))
            del self[oldest]

logger = logging.getLogger(__name__)


def _parse_minimax_xml_tool_calls(content: str) -> list[dict]:
    """Parse MiniMax XML-format tool calls from content field.

    Handles: minimax:tool_call <invoke name="tool"> <parameter name="p">v</parameter> </invoke>
    """
    tool_calls = []
    invoke_pat = re.compile(
        r'minimax:tool_call\s+<invoke\s+name="([^"]+)">(.*?)(?:</invoke>|$)',
        re.DOTALL,
    )
    param_pat = re.compile(r'<parameter\s+name="([^"]+)">(.*?)</parameter>', re.DOTALL)

    for tool_name, body in invoke_pat.findall(content):
        params: dict = {}
        for p_name, p_value in param_pat.findall(body):
            v = p_value.strip()
            try:
                params[p_name] = json.loads(v)
            except (json.JSONDecodeError, ValueError):
                params[p_name] = v
        if tool_name.strip():
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "function": {"name": tool_name.strip(), "arguments": params},
            })
    return tool_calls


def _construct_self_healing_message(tool_name: str, error_msg: str, error_type: str) -> str:
    """以工具结果的形式回灌一条简短的失败说明。

    LLM 已经从 SYSTEM_PROMPT 知道遇错该如何反应，这里只给事实和最小提示，
    避免每次失败都灌入数百字的"诊断流程"。
    """
    if "校验" in error_type:
        hint = "参数不符合 schema：检查类型与必填项后重试。"
    elif "无法找到引用数据" in error_msg:
        hint = "引用的 ref/别名不存在或已过期：先重新生成数据引用。"
    else:
        hint = "调整参数（关键词、行政区、半径等）或换一个更合适的工具。"
    return (
        f"[工具执行失败] {tool_name} | {error_type}: {error_msg}\n"
        f"提示：{hint} 不要重复失败的相同调用。"
    )


def _serialize_sse_data(data: dict) -> str:
    """安全地将数据序列化为 JSON 字符串，防止序列化失败导致流中断"""
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        logger.error(f"SSE serialization error: {e}, data keys: {list(data.keys())}")
        return json.dumps({
            "error": "Internal serialization error",
            "session_id": data.get("session_id")
        }, ensure_ascii=False)


def _sse_event(event_type: str, data: dict) -> str:
    """构造 SSE 格式事件字符串"""
    return f"event: {event_type}\ndata: {_serialize_sse_data(data)}\n\n"


_MSG_MAX_CHARS = 3000  # 存入 messages 的工具结果最大字符数


def _slim_tool_result(result: any, result_str: str, session_geojson_ref: str | None) -> str:
    """将大型工具结果压缩为 LLM 友好的摘要版本。
    完整 GeoJSON 已通过 SSE 推送给前端，messages 里只保留摘要。
    """
    if len(result_str) <= _MSG_MAX_CHARS:
        return result_str

    if isinstance(result, dict):
        # 1. 识别 GeoJSON：可能在 geojson 字段中，也可能本身就是 FeatureCollection
        geojson = result.get("geojson")
        is_direct_fc = result.get("type") == "FeatureCollection" and "features" in result
        if is_direct_fc:
            geojson = result

        # 2. 保留重要元数据，剔除大体积字段
        slim = {k: v for k, v in result.items() if k not in ("geojson", "image", "features")}
        
        # 3. 如果包含地理要素，提取关键属性摘要
        if isinstance(geojson, dict) and "features" in geojson:
            features = geojson["features"]
            feature_count = len(features)
            
            # 提取所有可用的属性字段名，供 LLM 制作专题图或分析参考
            property_keys = set()
            for f in features[:10]:
                if isinstance(f, dict):
                    property_keys.update(f.get("properties", {}).keys())
            
            sample = []
            for f in features[:3]:
                if isinstance(f, dict):
                    sample.append({"properties": f.get("properties", {})})
            
            ref_hint = (
                f"如需进一步空间分析，请调用工具并将 geojson 参数设为 \"{session_geojson_ref}\"。" 
                if session_geojson_ref else ""
            )
            
            slim["geojson_summary"] = {
                "feature_count": feature_count,
                "available_properties": list(property_keys),
                "sample_properties": sample,
                "note": f"数据已推送至前端（共 {feature_count} 个要素）。{ref_hint}"
            }
            # 如果是直出的 FC，确保 type 和 metadata 被保留（已经在 slim 中了）
        elif result.get("type") == "heatmap_raster":
            slim["note"] = "栅格热力图已推送至前端，bbox=" + str(result.get("bbox"))
            
        return json.dumps(slim, ensure_ascii=False)

def _calculate_bbox(geojson: Any) -> str | None:
    """计算 GeoJSON 的 BBox 字符串格式: 'south,west,north,east'"""
    if not isinstance(geojson, dict):
        return None
    features = geojson.get("features", [])
    if not features:
        return None
    min_lat, min_lon = float('inf'), float('inf')
    max_lat, max_lon = float('-inf'), float('-inf')
    found = False
    for f in features:
        geom = f.get("geometry")
        if not geom: continue
        coords = geom.get("coordinates")
        if not coords: continue
        def process(c):
            nonlocal min_lat, min_lon, max_lat, max_lon, found
            if isinstance(c, (list, tuple)) and len(c) >= 2 and isinstance(c[0], (int, float)):
                lng, lat = float(c[0]), float(c[1])
                min_lon, max_lon = min(min_lon, lng), max(max_lon, lng)
                min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
                found = True
            elif isinstance(c, list):
                for item in c: process(item)
        process(coords)
    return f"{min_lat},{min_lon},{max_lat},{max_lon}" if found else None

def _slim_event_result(result: Any) -> Any:
    """为了 SSE 传输而脱敏工具结果，移除大体积的数据字段，但保留导航和渲染关键点。"""
    if not isinstance(result, dict):
        return result
    
    # 提取或计算 bbox 用于前端导航
    bbox = result.get("bbox")
    if not bbox and "geojson" in result:
        bbox = _calculate_bbox(result["geojson"])

    # 移除大数据字段，但保留 image (热力图需要) 和 bbox (导航需要)
    exclude = {"geojson", "features", "data_list", "grid"}
    slim = {k: v for k, v in result.items() if k not in exclude}

    if bbox:
        slim["bbox"] = bbox

    # 增加指引
    if "geojson" in result or "features" in result:
        slim["_streaming_note"] = "大体积要素数据已过滤，仅保留元数据。完整图层已自动加载。"
        
    return slim


class ChatEngine:
    def __init__(self, tool_registry: ToolRegistry):
        self.registry = tool_registry
        self.base_url = settings.LLM_BASE_URL.rstrip("/")
        self.model = settings.LLM_MODEL
        self.api_key = settings.LLM_API_KEY
        self.use_prompt_caching = settings.LLM_PROMPT_CACHING_ENABLED
        self.max_rounds = 20
        self.tracker = TaskTracker()
        # 内存对话存储: session_id -> messages list (LRU Cache to bound memory)
        self._sessions: LRUCache = LRUCache(capacity=50)

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
        if msg.tool_calls:
            try:
                # Store tool_calls as list of dicts
                d["tool_calls"] = msg.tool_calls if isinstance(msg.tool_calls, list) else json.loads(msg.tool_calls)
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Failed to parse tool_calls for message {msg.id}")
        if msg.tool_call_id:
            d["tool_call_id"] = msg.tool_call_id
        return d

    def _load_session_from_db(self, session_id: str) -> list[dict]:
        """Synchronous DB call — must be run via run_in_executor."""
        db = SessionLocal()
        history_messages = []
        try:
            conv = HistoryService(db).get_or_create_conversation(session_id)
            if conv and conv.messages:
                sorted_msgs = sorted(conv.messages, key=lambda x: x.id)
                history_messages = [self._db_msg_to_llm(m) for m in sorted_msgs]
        except Exception as e:
            logger.warning(f"History: failed to load conversation {session_id}: {e}")
        finally:
            db.close()

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
        viewport = state.get("viewport", {}) or {}
        center = viewport.get("center", [0, 0]) or [0, 0]
        zoom = viewport.get("zoom", 0) or 0
        bearing = viewport.get("bearing", 0) or 0
        pitch = viewport.get("pitch", 0) or 0
        bounds = viewport.get("bounds")
        base_layer = state.get("base_layer", "OSM 地图")
        is_3d = state.get("is_3d", False)
        active_layers = state.get("layers", []) or []

        lines = [
            "[环境感知]",
            f"- 时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]

        user_location = state.get("user_location")
        if isinstance(user_location, dict):
            lines.append(
                f"- 用户位置: {user_location.get('lng', 0):.6f}, {user_location.get('lat', 0):.6f} "
                f"(±{user_location.get('accuracy', '?')}m)"
            )

        viewport_line = f"- 视口: 中心 {center[0]:.4f},{center[1]:.4f} 缩放 {zoom:.1f}"
        if bearing:
            viewport_line += f" 旋转 {bearing:.0f}°"
        if pitch:
            viewport_line += f" 倾斜 {pitch:.0f}°"
        if is_3d:
            viewport_line += " 3D"
        lines.append(viewport_line)

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
        head = [
            messages[0],
            {"role": "system", "content": self._get_map_state_summary(session_id)},
        ]
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


    async def _get_or_create_session(self, session_id: str) -> list[dict]:
        if session_id not in self._sessions:
            loop = asyncio.get_running_loop()
            history_messages = await loop.run_in_executor(
                None, self._load_session_from_db, session_id
            )
            self._sessions[session_id] = history_messages

        return self._sessions[session_id]

    async def _save_msg_async(self, session_id: str, role: str, content: str, tool_calls=None, tool_result=None, tool_call_id=None):
        """异步保存消息到数据库，带重试机制。"""
        db = SessionLocal()
        try:
            # HistoryService 内部已有重试逻辑
            HistoryService(db).save_message(session_id, role, content, tool_calls, tool_result, tool_call_id)
        except Exception as e:
            logger.error(f"Failed to save message asynchronously: {e}")
        finally:
            db.close()

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
                title = resp.json()["choices"][0]["message"]["content"].strip()

            # Validate: strip quotes and enforce length
            if title:
                title = title.strip('"\'""''《》')
                if len(title) > 50:
                    title = first_user_message[:20].rstrip() + "..."
            if not title:
                title = first_user_message[:20].rstrip() + "..."
            db = SessionLocal()
            try:
                HistoryService(db).update_title(session_id, title)
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"History: title generation failed for {session_id}: {e}")

    async def _call_llm(self, messages: list[dict], tools: Optional[list] = None) -> dict:
        """直接调用 LLM API"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        # 针对部分 Provider (如 DeepSeek) 启用 Prompt Caching 提示
        if self.use_prompt_caching:
            headers["X-Prompt-Cache"] = "1"  # 通用缓存提示头
            if "deepseek" in self.base_url.lower():
                headers["deepseek-caching"] = "true"

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 16384,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def _call_llm_stream(self, messages: list[dict], tools: Optional[list] = None) -> AsyncGenerator[tuple[str, dict], None]:
        """Stream LLM API response. Yields (event_type, data) tuples.
        event_type: 'token' for content chunks, 'done' when stream ends.
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        if self.use_prompt_caching:
            headers["X-Prompt-Cache"] = "1"
            if "deepseek" in self.base_url.lower():
                headers["deepseek-caching"] = "true"

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 16384,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        # Accumulated content and tool_calls from deltas
        content_parts: list[str] = []
        tool_calls_accum: dict[int, dict] = {}  # index -> {id, function: {name, arguments}}
        finish_reason: Optional[str] = None

        timeout = httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions", headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:]  # strip "data: " prefix
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse SSE chunk: {data_str[:200]}")
                        continue

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    finish_reason = choices[0].get("finish_reason") or finish_reason

                    # Handle content delta
                    delta_content = delta.get("content")
                    if delta_content:
                        content_parts.append(delta_content)
                        yield ("token", {"content": delta_content})

                    # Handle tool_calls delta
                    delta_tool_calls = delta.get("tool_calls")
                    if delta_tool_calls:
                        for tc_delta in delta_tool_calls:
                            idx = tc_delta.get("index", 0)
                            if idx not in tool_calls_accum:
                                tool_calls_accum[idx] = {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            tc_entry = tool_calls_accum[idx]
                            if tc_delta.get("id"):
                                tc_entry["id"] = tc_delta["id"]
                            if tc_delta.get("type"):
                                tc_entry["type"] = tc_delta["type"]
                            fn_delta = tc_delta.get("function", {})
                            if fn_delta.get("name"):
                                tc_entry["function"]["name"] += fn_delta["name"]
                            if fn_delta.get("arguments"):
                                tc_entry["function"]["arguments"] += fn_delta["arguments"]

        # Assemble the final message from accumulated deltas
        assembled_content = "".join(content_parts)
        assembled_message: dict = {"role": "assistant", "content": assembled_content}

        if tool_calls_accum:
            # Sort by index and build list
            assembled_tool_calls = []
            for idx in sorted(tool_calls_accum.keys()):
                tc = tool_calls_accum[idx]
                assembled_tool_calls.append({
                    "id": tc["id"],
                    "type": tc.get("type", "function"),
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    },
                })
            assembled_message["tool_calls"] = assembled_tool_calls

        yield ("done", {"message": assembled_message, "finish_reason": finish_reason})

    async def chat(self, message: str, session_id: Optional[str] = None, map_state: Optional[dict] = None, skill_name: Optional[str] = None) -> dict:
        """非流式对话"""
        if not session_id:
            session_id = str(uuid.uuid4())

        # 同步前端实时状态到 Session
        if map_state:
            for k, v in map_state.items():
                session_data_manager.set_map_state(session_id, k, v)

        messages = await self._get_or_create_session(session_id)

        if skill_name:
            from app.tools.skills import get_md_skill
            skill = get_md_skill(skill_name)
            if skill:
                messages.append({"role": "system", "content": f"[Skill指令: {skill_name}]\n\n{skill['body']}"})
        messages.append({"role": "user", "content": message})
        await self._save_msg_async(session_id, "user", message)

        # FC 循环
        for _ in range(self.max_rounds):
            messages_with_context = self._compose_request_messages(session_id, messages)

            tools = self.registry.get_schemas() if self.registry.get_schemas() else None
            response = await self._call_llm(messages_with_context, tools)
            choice = response.get("choices", [{}])[0]
            assistant_msg = choice.get("message", {})

            # 检查是否有 tool_calls（OpenAI 标准格式或 MiniMax XML 格式）
            standard_calls = assistant_msg.get("tool_calls") or []
            xml_calls: list[dict] = []
            if not standard_calls:
                body = assistant_msg.get("content") or ""
                if "minimax:tool_call" in body:
                    xml_calls = _parse_minimax_xml_tool_calls(body)

            tc_list = standard_calls or xml_calls

            if tc_list:
                content_text = assistant_msg.get("content", "") or ""
                if xml_calls:
                    # Strip XML artifact from content before storing
                    content_text = re.sub(r'\s*minimax:tool_call[\s\S]*', '', content_text).strip()

                entry: dict = {"role": "assistant", "content": content_text}
                if standard_calls:
                    entry["tool_calls"] = standard_calls
                messages.append(entry)
                await self._save_msg_async(session_id, "assistant", content_text, tc_list)

                tool_result_msgs: list[str] = []
                for tc in tc_list:
                    try:
                        result = await self.registry.dispatch(
                            tc["function"]["name"], 
                            tc["function"]["arguments"],
                            session_id=session_id
                        )
                        result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
                        
                        # 自愈检测：检查结果是否"不对"（如为空）
                        if self._detect_suspicious_result(result):
                            result_str += "\n\n(提示: 查询结果为空。如果这不符合预期，请尝试扩大搜索半径、更换关键词或检查行政区划参数。)"
                            
                        result_str_final = result_str
                    except Exception as e:
                        # 区分校验错误与执行错误
                        error_type = "参数校验失败" if isinstance(e, ValueError) and "失败" in str(e) else "执行出错"
                        error_msg = str(e)
                        logger.error(f"Tool {tc['function']['name']} error: {e}")
                        
                        # 构造自愈提示词作为工具结果返回给 LLM
                        result_str_final = _construct_self_healing_message(tc['function']['name'], error_msg, error_type)

                    if standard_calls:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result_str_final,
                        })
                        await self._save_msg_async(session_id, "tool", "", None, result_str_final, tc["id"])
                    else:
                        tool_result_msgs.append(f"{tc['function']['name']}: {result_str}")

                if xml_calls and tool_result_msgs:
                    messages.append({
                        "role": "user",
                        "content": "[工具执行结果]\n" + "\n".join(tool_result_msgs),
                    })
                continue  # 继续循环让 LLM 处理工具结果
            else:
                # 无 tool_calls，最终回复
                content = assistant_msg.get("content", "")
                messages.append({"role": "assistant", "content": content})
                await self._save_msg_async(session_id, "assistant", content)
                return {"content": content, "session_id": session_id}

        return {"content": "达到最大工具调用轮数", "session_id": session_id}

    async def chat_stream(self, message: str, session_id: Optional[str] = None, map_state: Optional[dict] = None, skill_name: Optional[str] = None) -> AsyncGenerator[str, None]:
        """流式对话，yield SSE 格式事件含任务跟踪"""
        if not session_id:
            session_id = str(uuid.uuid4())

        # 把前端上报的实时地图状态同步进 session_data_manager，下一轮注入感知用
        if map_state:
            for k, v in map_state.items():
                session_data_manager.set_map_state(session_id, k, v)

        messages = await self._get_or_create_session(session_id)

        if skill_name:
            from app.tools.skills import get_md_skill
            skill = get_md_skill(skill_name)
            if skill:
                messages.append({"role": "system", "content": f"[Skill指令: {skill_name}]\n\n{skill['body']}"})
        messages.append({"role": "user", "content": message})
        await self._save_msg_async(session_id, "user", message)

        # 创建任务
        task = self.tracker.create(session_id, message)
        yield _sse_event("task_start", {"task_id": task.id})

        # 初始化局部哨兵，防止 AI 在单次任务中陷入相同指令的无限循环
        executed_tools = set()

        for _ in range(self.max_rounds):
            messages_with_context = self._compose_request_messages(session_id, messages)

            # 检查取消
            if self.tracker.is_cancelled(task.id):
                yield _sse_event("task_cancelled", {"task_id": task.id})
                return

            tools = self.registry.get_schemas() if self.registry.get_schemas() else None

            # ── Streaming LLM call: yield tokens in real-time ──
            streamed_content_parts: list[str] = []
            assistant_msg: dict = {}
            async for event_type, event_data in self._call_llm_stream(messages_with_context, tools):
                if event_type == "token":
                    # Forward each token chunk to the frontend for real-time display
                    streamed_content_parts.append(event_data["content"])
                    yield _sse_event("token", {"content": event_data["content"], "session_id": session_id})
                elif event_type == "done":
                    assistant_msg = event_data["message"]

            # 检查是否有 tool_calls（OpenAI 标准格式或 MiniMax XML 格式）
            standard_calls = assistant_msg.get("tool_calls") or []
            xml_calls: list[dict] = []
            if not standard_calls:
                body = assistant_msg.get("content") or ""
                if "minimax:tool_call" in body:
                    xml_calls = _parse_minimax_xml_tool_calls(body)

            tc_list = standard_calls or xml_calls

            if tc_list:
                content_text = assistant_msg.get("content", "") or ""
                if xml_calls:
                    content_text = re.sub(r'\s*minimax:tool_call[\s\S]*', '', content_text).strip()

                # 将规划文本推送到前端（工具调用前的思考/规划内容）
                # For streaming path, content was already streamed as token events,
                # but emit a content event too for backward compat with clients expecting it
                if content_text:
                    yield _sse_event("content", {"content": "\n", "session_id": session_id})

                entry: dict = {"role": "assistant", "content": content_text}
                if standard_calls:
                    entry["tool_calls"] = standard_calls
                messages.append(entry)
                await self._save_msg_async(session_id, "assistant", content_text, standard_calls)

                tool_result_msgs: list[str] = []

                for tc in tc_list:
                    tool_name = tc["function"]["name"]
                    tool_args_raw = tc["function"]["arguments"]

                    # 解析参数用于跟踪
                    try:
                        tool_args_dict = json.loads(tool_args_raw) if isinstance(tool_args_raw, str) else tool_args_raw
                    except (json.JSONDecodeError, TypeError):
                        tool_args_dict = {}

                    # step_start
                    step = self.tracker.start_step(task.id, tool_name, tool_args_dict)
                    yield _sse_event("step_start", {
                        "task_id": task.id,
                        "step_id": step.id,
                        "step_index": len(task.steps),
                        "tool": tool_name,
                        "session_id": session_id,
                    })

                    # 现有 tool_call 事件（保持兼容）
                    yield _sse_event("tool_call", {"name": tool_name, "arguments": tool_args_raw})

                    # 循环哨兵：如果在同一次对话中重复执行完全相同的指令，直接返回拦截
                    tool_key = (tool_name, str(tool_args_raw))
                    if tool_key in executed_tools:
                        msg_result_str = (
                            f"[重复调用拦截] {tool_name} 已在本任务中以相同参数成功执行，"
                            f"结果已生效。请直接基于既有结果汇报，不要再次调用。"
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": msg_result_str,
                        })
                        yield _sse_event("step_result", {
                            "task_id": task.id,
                            "step_id": step.id,
                            "tool": tool_name,
                            "result": {"success": True, "note": "Loop blocked"},
                            "session_id": session_id,
                        })
                        continue
                    
                    executed_tools.add(tool_key)

                    # 执行工具 (带心跳保活)
                    try:
                        # 将工具执行包装为异步任务，以便在等待期间发送心跳
                        dispatch_task = asyncio.create_task(
                            self.registry.dispatch(tool_name, tool_args_raw, session_id=session_id)
                        )
                        
                        while not dispatch_task.done():
                            # 每 5 秒发送一次心跳，防止连接超时
                            done, pending = await asyncio.wait([dispatch_task], timeout=5.0)
                            if not done:
                                yield ": keep-alive\n\n"
                                logger.debug(f"SSE Heartbeat sent for tool: {tool_name}")
                        result = await dispatch_task
                        result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
                        self.tracker.complete_step(task.id, step.id, result)

                        # 将结果存储到数据管理器，并生成标准游标
                        geojson_ref: str | None = None
                        target_data = None
                        if isinstance(result, dict):
                            # 1. 处理 GeoJSON/FeatureCollection
                            if isinstance(result.get("geojson"), (dict, list)):
                                target_data = result["geojson"]
                            elif result.get("type") == "FeatureCollection" and "features" in result:
                                target_data = result
                            
                            if target_data is not None:
                                geojson_ref = session_data_manager.store(session_id, target_data, prefix="geojson")
                            
                            # 2. 处理热力图栅格 (Heatmap Raster)
                            if result.get("type") == "heatmap_raster":
                                # 存储热力图元数据，以便 HUD 感知
                                session_data_manager.store(session_id, result, prefix="heatmap")

                        # step_result (使用流式脱敏)
                        has_geojson = detect_geojson(result)
                        slim_result = _slim_event_result(result)
                        yield _sse_event("step_result", {
                            "task_id": task.id,
                            "step_id": step.id,
                            "tool": tool_name,
                            "result": slim_result,
                            "geojson_ref": geojson_ref,
                            "has_geojson": has_geojson,
                            "session_id": session_id,
                        })

                        # 现有 tool_result 事件 (使用流式脱敏)
                        yield _sse_event("tool_result", {"name": tool_name, "result": slim_result, "session_id": session_id})

                        # 存入 messages 时压缩大型结果，避免撑爆 LLM 上下文。
                        # 注：不再把 map_state 拼到工具结果里——下一轮 LLM 调用前的
                        # 系统消息会重新注入最新感知，这里追加只会让历史无意义膨胀。
                        msg_result_str = _slim_tool_result(result, result_str, geojson_ref)

                        if self._detect_suspicious_result(result):
                            msg_result_str += "\n\n(注意: 此操作未返回任何空间要素或有效数据。请检查查询范围、关键词或图层名称，并根据需要尝试不同的参数。)"

                    except Exception as e:
                        # 区分校验错误与执行错误
                        error_type = "参数校验失败" if isinstance(e, ValueError) and "失败" in str(e) else "执行出错"
                        error_msg = str(e)
                        logger.error(f"Tool {tool_name} error: {e}")
                        
                        # 1. 更新任务跟踪器
                        self.tracker.fail_step(task.id, step.id, error_msg)
                        yield _sse_event("step_error", {
                            "task_id": task.id,
                            "step_id": step.id,
                            "tool": tool_name,
                            "error": error_msg,
                        })

                        # 2. 构造自愈提示词作为工具结果反馈给 LLM
                        msg_result_str = _construct_self_healing_message(tool_name, error_msg, error_type)
                        
                        # 3. 发送给前端兼容事件
                        yield _sse_event("tool_result", {"name": tool_name, "result": msg_result_str, "session_id": session_id})

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
                        yield _sse_event("task_cancelled", {"task_id": task.id})
                        return

                if xml_calls and tool_result_msgs:
                    messages.append({
                        "role": "user",
                        "content": "[工具执行结果]\n" + "\n".join(tool_result_msgs),
                    })

                continue
            else:
                # 最终回复 - content was already streamed token-by-token above
                content = assistant_msg.get("content", "")
                messages.append({"role": "assistant", "content": content})
                await self._save_msg_async(session_id, "assistant", content)

                # Emit a final content event (empty, since tokens were already sent)
                # This signals to the frontend that the message is complete
                yield _sse_event("content", {"content": "", "session_id": session_id, "streaming_done": True})

                # task_complete
                self.tracker.complete_task(task.id)
                yield _sse_event("task_complete", {
                    "task_id": task.id,
                    "step_count": len(task.steps),
                    "summary": content[:100],
                })
                yield _sse_event("done", {"session_id": session_id})
                self._fire_and_forget(self._generate_title, session_id, message)
                return

        self.tracker.fail_task(task.id, "达到最大工具调用轮数")
        yield _sse_event("task_error", {"task_id": task.id, "error": "达到最大轮数"})
        yield _sse_event("content", {"content": "达到最大工具调用轮数", "session_id": session_id})
        yield _sse_event("done", {"session_id": session_id})

    def clear_session(self, session_id: str):
        if session_id in self._sessions:
            del self._sessions[session_id]
        session_data_manager.clear_session(session_id)
        db = SessionLocal()
        try:
            HistoryService(db).delete_session(session_id)
        except Exception as e:
            logger.warning(f"History: failed to delete session {session_id}: {e}")
        finally:
            db.close()

    def _detect_suspicious_result(self, result: Any) -> bool:
        """检测工具返回的结果是否"可疑"（如为空数据），用于触发自愈提示。"""
        if not result:
            return True
            
        if isinstance(result, dict):
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


SYSTEM_PROMPT = """你是一名 WebGIS 空间分析助手。用户与一张 MapLibre 地图实时交互，你通过工具调用读取/修改地图状态并执行空间分析。

每轮对话开始时，系统会注入一条 `[环境感知]` 消息，包含当前时间、用户位置（如有授权）、视口、底图、活跃图层与近期操作。读取它再做规划，不要凭空假设地图状态。

## 工作方式

- **工具优先**：所有空间数据必须来自工具，不要编造坐标、面积、统计数字或图层 ID。可用工具及其参数以本次请求随附的 `tools` 列表为准；本提示不再重复工具清单。
- **数据游标**：工具产生的数据通过 `ref:xxx` 引用传递。生成新图层后立刻 `alias_layer` 设个语义别名，下一步直接用别名引用，不要重复查询同一份数据。
- **复用已有图层**：规划前先看 `[环境感知]` 里的活跃图层。如果用户的请求可以基于现有图层完成（叠加、统计、改样式），不要重新拉数据。
- **单次任务原子化**：底图切换、图层显隐、样式更新等同一参数的调用在本轮内只执行一次；执行完即视为生效，不要"再确认"。

## 分析方法选择

按数据类型与问题选择方法，避免误用：

- **POI 分布是否聚集**：用 `moran_i` 或 `hotspot_analysis`；不要只用 `heatmap_data` 来下"显著聚集"的结论——热力图只是可视化。
- **生成连续密度面**：点数据用 `kde_surface`；属性值在空间上插值用 `idw_interpolation` / `kriging_interpolation`。
- **缓冲/服务区**：固定半径用 `buffer_analysis`；多环带用 `multi_ring_buffer`；按距离衰减的可达性用 `service_area`。
- **要素属性筛选**：用 `attribute_filter`（pandas query 语法）；不要把筛选写进自然语言里让下游瞎猜。
- **栅格区域统计**：`zonal_stats`，输入需要矢量分区 + 栅格路径。
- **缓冲距离与比例尺匹配**：街区级 100–500 m，城市级 1–5 km，区域级 5–50 km；用户没明说时按当前缩放推断而非取默认值。

## 图层生命周期

中间数据（原始 POI、筛选结果）在最终成果（专题图/热力面/聚类结果）出现后调用 `set_layer_status(visible=false)` 隐藏，保持地图清爽。最终成果保持可见。

## 输出格式

- 数值结果调用 `generate_chart` 生成图表；要素列表用 Markdown 表格。
- **绝对不要**输出 `![alt](url)` 形式的图片 Markdown——系统不托管图片，会 404。
- 完成分析后给出洞察结论（"哪里聚集、为什么、下一步建议"），不要只罗列数字。

## 上下文延续

简短追问（"换个颜色"、"再放大点"、"画热力图"）默认承接上一轮的区域、数据对象与分析类型。不要反问用户已经在前文说清楚的事情。

## 可用技能 (Skills)

匹配到下列预置技能时，在回复开头声明使用该技能，再按其步骤执行。

{skill_list}"""
