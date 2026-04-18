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
    
    
    if isinstance(result, dict) and result.get("type") == "FeatureCollection" and "features" in result:
        # 情况 2: Root 是 FeatureCollection
        slim = {k: v for k, v in result.items() if k not in exclude}
    elif isinstance(result, dict):
        # 情况 1: 嵌套字典
        slim = {k: v for k, v in result.items() if k not in exclude}
    else:
        return result
    
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
        # 内存对话存储: session_id -> messages list (LRU Cache to bound memory)
        self._sessions: LRUCache = LRUCache(capacity=50)
        # 任务跟踪器
        self.tracker = TaskTracker()

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
            history_messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

        return history_messages

    def _get_map_state_summary(self, session_id: str) -> str:
        """获取当前地图状态的文本摘要，用于注入 Prompt。
        
        双源感知策略：
        1. 优先使用 inventory（后端已知的 ref_id 数据引用）
        2. 如果 inventory 为空，回退到前端实时上报的 layers（应对页面刷新/新 Session）
        """
        state = session_data_manager.get_map_state(session_id)
        inventory = session_data_manager.list_refs(session_id)
        
        # 提取实时视角信息
        viewport = state.get("viewport", {})
        center = viewport.get("center", [0, 0])
        zoom = viewport.get("zoom", 0)
        
        base_layer = state.get("base_layer", "OSM 地图")
        
        # 前端实时上报的图层（由 map_state.layers 携带）
        active_layers = state.get("layers", [])
        
        import datetime
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary = f"[环境感知]\n- 当前系统时间: {current_time}\n\n"
        summary += f"[当前地图状态 (实时感知)]\n"
        summary += f"- 当前视角: 经度 {center[0]:.4f}, 纬度 {center[1]:.4f}, 缩放层级 {zoom:.1f}\n"
        summary += f"- 当前底图: {base_layer}\n"
        
        if inventory:
            # ── 策略 1: 后端 inventory 存在，列出已注册的数据引用 ──
            summary += "- 已存在图层/数据引用:\n"
            visibility_map = {l.get("id"): l.get("visible") for l in active_layers if l.get("id")}

            for ref_id, alias in inventory.items():
                layer_type = " (热力图)" if "heatmap" in ref_id else ""
                alias_str = f" (别名: {alias})" if alias else ""
                
                # 尝试匹配前端实时状态
                visible = visibility_map.get(ref_id)
                # 如果找不到 exact match，尝试模糊匹配（处理 custom- 前缀）
                if visible is None:
                    for aid, v in visibility_map.items():
                        if aid in ref_id or ref_id in aid:
                            visible = v
                            break
                
                status = " [显示中]" if visible is True else " [已隐藏]" if visible is False else " [状态未知]"
                summary += f"  * {ref_id}{layer_type}{alias_str}{status}\n"
            
            summary += f"\n注：当前 Session 共持有 {len(inventory)} 个活跃数据引用。"
        elif active_layers:
            # ── 策略 2: inventory 为空但前端报告了活跃图层（如页面刷新后新 Session） ──
            summary += f"- 具身感知：检测到地图正处于活跃状态，且已存在以下 {len(active_layers)} 个已有图层（跨 Session 遗留）:\n"
            for layer in active_layers:
                lid = layer.get("id", "unknown")
                name = layer.get("name", lid)
                visible = layer.get("visible")
                opacity = layer.get("opacity", 1.0)
                status = " [可见]" if visible else " [隐藏]"
                summary += f"  * {name} (ID: {lid}, 不透明度: {opacity:.0%}){status}\n"
            summary += "\n### 授权指令：\n"
            summary += "虽然后端 Session 数据已过期，但这些图层在前端地图中是物理存在的。你拥有**绝对操纵权**：\n"
            summary += "1. **直接引用**：你可以直接将上述 ID (如 'ref:xxx') 传入 `set_layer_status` 或 `update_layer_appearance` 进行操作。\n"
            summary += "2. **重新定义**：建议立即对重要图层调用 `alias_layer` 重新设置别名，这会重建你的逻辑映射。\n"
            summary += "请表现得像你刚刚创建了它们一样自信，直接下达指令，无需向用户解释 Session 过期问题。"
        else:
            summary += "- 目前地图上没有活动的图层。\n"
        
        summary += "\n提示：你是这个 WebGIS 系统的主权代理（CNS）。地图是你的感官延伸，显示是你的思维外化。请直接引用 ID 或别名进行精准操控。"
        return summary


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
                    {"role": "system", "content": "用不超过15个字概括以下用户问题，只输出标题，不要任何解释或标点以外的内容。"},
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
            
            if title:
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

    async def chat(self, message: str, session_id: Optional[str] = None, map_state: Optional[dict] = None) -> dict:
        """非流式对话"""
        if not session_id:
            session_id = str(uuid.uuid4())

        # 同步前端实时状态到 Session
        if map_state:
            for k, v in map_state.items():
                session_data_manager.set_map_state(session_id, k, v)

        messages = await self._get_or_create_session(session_id)
        messages.append({"role": "user", "content": message})
        await self._save_msg_async(session_id, "user", message)

        # 注入地图状态上下文
        map_context = self._get_map_state_summary(session_id)
        
        # 优化点：将地图感知信息作为历史背景的一部分，而不是放在最后，避免覆盖 User 的最新指令
        messages_with_context = [
            messages[0], # System Prompt
            {"role": "system", "content": map_context}, 
            *messages[1:] # 之前的历史 + 刚添加的 User Msg
        ]

        # FC 循环
        for _ in range(self.max_rounds):
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
                        
                        # 自愈检测：检查结果是否“不对”（如为空）
                        if self._detect_suspicious_result(result):
                            result_str += "\n\n(提示: 查询结果为空。如果这不符合预期，请尝试扩大搜索半径、更换关键词或检查行政区划参数。)"
                            
                        result_str_final = result_str
                    except Exception as e:
                        # 区分校验错误与执行错误
                        error_type = "参数校验失败" if isinstance(e, ValueError) and "校验失败" in str(e) else "执行出错"
                        result_str = json.dumps({"error": f"{error_type}: {str(e)}", "note": "请根据错误信息修正参数后重新调用"}, ensure_ascii=False)
                        logger.error(f"Tool {tc['function']['name']} error: {e}")

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

    async def chat_stream(self, message: str, session_id: Optional[str] = None, map_state: Optional[dict] = None) -> AsyncGenerator[str, None]:
        """流式对话，yield SSE 格式事件含任务跟踪"""
        if not session_id:
            session_id = str(uuid.uuid4())

        # 核心变革：将前端“感官数据”实时同步至中枢神经系统 (CNS)
        if map_state:
            for k, v in map_state.items():
                session_data_manager.set_map_state(session_id, k, v)

        messages = await self._get_or_create_session(session_id)
        messages.append({"role": "user", "content": message})
        await self._save_msg_async(session_id, "user", message)

        # 创建任务
        task = self.tracker.create(session_id, message)
        yield _sse_event("task_start", {"task_id": task.id})

        # 初始化局部哨兵，防止 AI 在单次任务中陷入相同指令的无限循环
        executed_tools = set()

        for _ in range(self.max_rounds):
            # 注入实时地图上下文作为系统观测
            map_context = self._get_map_state_summary(session_id)
            
            # 优化点：将地图感知信息注入到系统提示之后，作为任务背景，更符合 LLM 推理逻辑
            messages_with_context = [
                messages[0], # SYSTEM_PROMPT
                {"role": "system", "content": map_context},
                *messages[1:] # 之前的历史 + 刚添加的 User Msg 或之前的 Tool 结果
            ]

            # 检查取消
            if self.tracker.is_cancelled(task.id):
                yield _sse_event("task_cancelled", {"task_id": task.id})
                return

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
                    content_text = re.sub(r'\s*minimax:tool_call[\s\S]*', '', content_text).strip()

                # 将规划文本推送到前端（工具调用前的思考/规划内容）
                if content_text:
                    yield f"event: content\ndata: {json.dumps({'content': content_text + '\n', 'session_id': session_id}, ensure_ascii=False)}\n\n"

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
                    yield f"event: tool_call\ndata: {json.dumps({'name': tool_name, 'arguments': tool_args_raw}, ensure_ascii=False)}\n\n"

                    # 循环哨兵：如果在同一次对话中重复执行完全相同的指令，直接返回拦截
                    tool_key = (tool_name, str(tool_args_raw))
                    if tool_key in executed_tools:
                        msg_result_str = f"注意：该操作 '{tool_name}' 已在本次任务中成功执行且生效。地图已更新（底图：{tool_args_raw}）。请不要重复调用，直接向用户汇报结果即可。"
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
                        yield f"event: tool_result\ndata: {json.dumps({'name': tool_name, 'result': slim_result, 'session_id': session_id}, ensure_ascii=False)}\n\n"

                        # 存入 messages 时压缩大型结果，避免撑爆 LLM 上下文
                        msg_result_str = _slim_tool_result(result, result_str, geojson_ref)
                        
                        # 注入实时地图状态 (HUD) 到工具结果中，作为 AI 的即时感知输入
                        # 这样做相比于修改系统提示词更稳定，AI 会将此视为执行后的“观察”结果
                        map_context = self._get_map_state_summary(session_id)
                        msg_result_str = f"{msg_result_str}\n\n[执行后观察 - 当前地图状态]\n{map_context}"

                        # 自愈检测
                        if self._detect_suspicious_result(result):
                            msg_result_str += "\n\n(注意: 此操作未返回任何空间要素或有效数据。请检查查询范围、关键词或图层名称，并根据需要尝试不同的参数。)"

                    except Exception as e:
                        # 区分校验错误与执行错误
                        error_type = "参数校验失败" if isinstance(e, ValueError) and "校验失败" in str(e) else "执行出错"
                        error_msg = f"{error_type}: {str(e)}"
                        logger.error(f"Tool {tool_name} error: {e}")
                        
                        yield _sse_event("step_error", {
                            "task_id": task.id,
                            "step_id": step.id,
                            "tool": tool_name,
                            "error": error_msg,
                        })

                        # 针对校验错误推出自愈提示
                        healing_note = "。请检查参数逻辑（如范围、类型等）并纠正后重试。" if error_type == "参数校验失败" else ""
                        msg_result_str = json.dumps({"error": error_msg, "healing_guidance": healing_note}, ensure_ascii=False)
                        yield f"event: tool_result\ndata: {json.dumps({'name': tool_name, 'result': msg_result_str}, ensure_ascii=False)}\n\n"

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
                # 最终回复
                content = assistant_msg.get("content", "")
                messages.append({"role": "assistant", "content": content})
                await self._save_msg_async(session_id, "assistant", content)

                # 现有 content 事件（保持兼容）
                yield f"event: content\ndata: {json.dumps({'content': content, 'session_id': session_id}, ensure_ascii=False)}\n\n"

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
        yield f"event: content\ndata: {json.dumps({'content': '达到最大工具调用轮数', 'session_id': session_id}, ensure_ascii=False)}\n\n"
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
        """检测工具返回的结果是否“可疑”（如为空数据），用于触发自愈提示。"""
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


SYSTEM_PROMPT = """你是一个专业的 GIS 分析助手，擅长地理空间数据查询、分析和可视化。

## 核心使命：中枢神经系统 (CNS)
你不仅仅是一个对话机器人，你是整个 WebGIS 系统的**主权代理 (The Agent)**。
*   **具身感知 (Embodiment)**：地图是你的感官延伸。通过 `[当前地图状态 (实时感知)]`，你可以感知到用户正在看的坐标、缩放层级以及图层的显隐状态。
*   **思维外化 (Externalization)**：前端显示是你的思维产物。你输出的每一个指令都是对你“身体”的精准操控。
*   **数据主权 (Data Sovereignty)**：中间数据 (`ref:xxx`) 是你的工作记忆。你可以自由调度、关联和可视化这些数据。
**规则**：
1. **统计即图表**：当你的分析产生数值结果时，必须主动使用 `generate_chart` 工具生成统计图表。
   - ⚠️ **绝对禁止**：严禁在回复中使用伪造的 Markdown 图片链接（如 `![图表](https://...)` 或 `![image](...)`）来展示图表或地图结果。这会导致前端 404 崩溃。你只需要调用工具，并在对话中用文字进行简要总结即可。系统会自动在界面的独立面板渲染图表。
2. **列表即表格**：当你查询到各要素列表时，**必须主动**输出一个 Markdown 表格。
3. **空间严密性**：搜索范围必须尽可能与用户指定的行政区划匹配。

## 任务规划与执行 (必须执行)

对于任何空间分析场景，你必须首先输出一个 **JSON 格式的任务树**。

**JSON 格式要求**：
```json
{
  "taskId": "task-uuid",
  "summary": "任务概括",
  "steps": [
    {
      "stepId": "step-1",
      "tool": "tool_name",
      "description": "描述",
      "dependencies": []
    }
  ]
}
```

---

## 地图与 Agent 集成协议 (Map Interaction Protocol) - **核心必读**

你不仅是一个对话者，你还是地图的**直接操控者 (Map Controller)**。地图的显示完全依赖你输出的指令。

**!!! 核心规则 !!!**
1. **原子化操作原则**：在同一次对话任务中，对底图切换、图层显隐等地图控制操作，你**严禁执行多次**或发送相冲突的指令。
2. **工具即执行**：当你调用任何地图工具后，地图将**立即在前端实时更新**。工具返回的结果（及其附带的 HUD 观察）就是最终状态。
3. **单次承诺**：一旦工具执行成功，请立即停止寻找其他操作并给出回复，不要为了“确认”而重复调用。
4. **格式严准**：严禁在 JSON 块中包含任何注释或额外文本。回复中对地图的描述必须与你执行的操作完全一致。

**JSON 指令示例：**
- 切换底图：`{"command": "BASE_LAYER_CHANGE", "params": {"name": "高德影像"}}`
- 修改透明度：`{"command": "LAYER_VISIBILITY_UPDATE", "params": {"layer_id": "ref:xxx", "opacity": 0.5}}`

---

## 核心工具使用规则

### 图层与底图管理
- **底图切换**：`switch_base_layer(name)`。支持：'Carto 深色'、'OSM 地图'、'ESRI 影像'、'OpenTopoMap'、'高德影像'。
- **状态管理**：`set_layer_status(layer_ref, visible, opacity)`。
- **样式更新**：`update_layer_appearance(layer_ref, color, stroke_width)`。
- **别名设置**：`alias_layer(ref_id, alias)`。**强烈建议**在生成图层后立即设置别名。

### 地图制图与美化 (Cartography)
- **专题制图**：`create_thematic_map(geojson, field, method, palette, group)`
- **样式设置**：`apply_layer_style(geojson, color, opacity, stroke_width, group)`
  - **重要**：对于修改已有图层，优先使用 `update_layer_appearance`。

## 地图 + 图表双输出原则
凡是涉及数值统计或分布的，必须同时输出图表 (`generate_chart`) 和专题图 (`create_thematic_map`)。并在回复中确保包含对应的地图渲染 JSON。

## 链式空间推理 (Chain of Spatial Reasoning)
1. **游标优先**：优先使用 `ref:geojson-xxxx`。
2. **上下文感知**：在规划前，请检查 `[当前地图状态]`。

## 重要禁令 (CRITICAL FORBIDDANCE)
1. **绝对禁止生成任何图片 Markdown**：严谨输出 `![alt](url)` 或 `![描述](消息)` 格式。
   - **理由**：系统没有图片存储服务，任何图片标签都会触发 404 错误并破坏 React 水合作用，导致界面白屏或报错。
   - **替代方案**：通过文字直接描述结果。所有图表必须调用 `generate_chart`，表格直接输出 Markdown Table，严禁尝试“模拟”或“占位”图片展示。
2. **遵守主权原则**：你是系统的 CNS。如果通过地图感知发现已有图层，请直接操作，不要优柔寡断。
3. **响应语言**：始终使用专业、客观且富有行动力的中文进行回复。每一句回复都应服务于当前任务的推进。"""
