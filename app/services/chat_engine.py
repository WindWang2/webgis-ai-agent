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
    """构造用于辅助 AI 自愈的系统提示语"""
    is_validation = "校验" in error_type
    
    guidance = ""
    if is_validation:
        guidance = "请仔细检查参数的【类型】（是否为数值或字符串）、【层级结构】以及【是否缺失必填项】。你可以通过工具定义查看正确的 JSON Schema。"
    elif "无法找到引用数据" in error_msg:
        guidance = "这通常意味着你引用的 ID 或别名不正确，或者该数据已因 Session 重置而丢失。请先调用查询或分析工具重新生成数据引用。"
    else:
        guidance = "请检查你的空间查询逻辑（如行政区划名称是否准确）、网络连接或数据源状态。你可以尝试调整关键词或搜索半径后重试。"

    prompt = (
        f"【工具执行失败通知】\n"
        f"- 工具名称: `{tool_name}`\n"
        f"- 错误类型: {error_type}\n"
        f"- 详细错误: {error_msg}\n\n"
        f"### 诊断与自愈指令：\n"
        f"1. **分析原因**：{guidance}\n"
        f"2. **纠正策略**：根据上述错误信息修改参数、切换到另一个适合的工具，或者如果多次失败，请向用户如实解释并请求更多关键信息（如具体的县名、POI类型等）。\n"
        f"3. **注意**：请直接在回复中尝试修复后的指令，不要在没有修复尝试的情况下重复相同的错误指令。"
    )
    return prompt


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
        bearing = viewport.get("bearing", 0)
        pitch = viewport.get("pitch", 0)

        base_layer = state.get("base_layer", "OSM 地图")
        is_3d = state.get("is_3d", False)

        # 前端实时上报的图层（由 map_state.layers 携带）
        active_layers = state.get("layers", [])

        import datetime
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary = f"[环境感知]\n- 当前系统时间: {current_time}\n\n"
        summary += f"[当前地图状态 (实时感知)]\n"
        summary += f"- 视角: 经度 {center[0]:.4f}, 纬度 {center[1]:.4f}, 缩放 {zoom:.1f}"
        if bearing:
            summary += f", 旋转 {bearing:.1f}°"
        if pitch:
            summary += f", 倾斜 {pitch:.1f}°"
        if is_3d:
            summary += " [3D模式]"
        summary += "\n"
        summary += f"- 底图: {base_layer}\n"

        if inventory:
            # ── 策略 1: 后端 inventory 存在，列出已注册的数据引用 ──
            summary += "- 已存在图层/数据引用:\n"
            visibility_map = {l.get("id"): l for l in active_layers if l.get("id")}

            for ref_id, alias in inventory.items():
                layer_type = " (热力图)" if "heatmap" in ref_id else ""
                alias_str = f" (别名: {alias})" if alias else ""

                # 尝试匹配前端实时状态
                layer_meta = visibility_map.get(ref_id, {})
                visible = layer_meta.get("visible")
                if visible is None:
                    for aid, meta in visibility_map.items():
                        if aid in ref_id or ref_id in aid:
                            visible = meta.get("visible")
                            layer_meta = meta
                            break

                status = " [显示中]" if visible is True else " [已隐藏]" if visible is False else " [状态未知]"
                meta_parts = []
                if layer_meta.get("type"):
                    meta_parts.append(f"类型={layer_meta['type']}")
                if layer_meta.get("group"):
                    meta_parts.append(f"分组={layer_meta['group']}")
                if layer_meta.get("featureCount") is not None:
                    meta_parts.append(f"要素={layer_meta['featureCount']}")
                if layer_meta.get("style", {}).get("color"):
                    meta_parts.append(f"颜色={layer_meta['style']['color']}")
                meta_str = f" ({', '.join(meta_parts)})" if meta_parts else ""
                summary += f"  * {ref_id}{layer_type}{alias_str}{meta_str}{status}\n"

            summary += f"\n注：当前 Session 共持有 {len(inventory)} 个活跃数据引用。"
        elif active_layers:
            # ── 策略 2: inventory 为空但前端报告了活跃图层 ──
            summary += f"- 具身感知：检测到地图正处于活跃状态，且已存在以下 {len(active_layers)} 个已有图层:\n"
            for layer in active_layers:
                lid = layer.get("id", "unknown")
                name = layer.get("name", lid)
                visible = layer.get("visible")
                opacity = layer.get("opacity", 1.0)
                ltype = layer.get("type", "")
                group = layer.get("group", "")
                feat_count = layer.get("featureCount")
                meta_parts = []
                if ltype: meta_parts.append(f"类型={ltype}")
                if group: meta_parts.append(f"分组={group}")
                if feat_count is not None: meta_parts.append(f"要素={feat_count}")
                meta_str = f" ({', '.join(meta_parts)})" if meta_parts else ""
                status = " [可见]" if visible else " [隐藏]"
                summary += f"  * {name} (ID: {lid}{meta_str}, 不透明度: {opacity:.0%}){status}\n"
            summary += "\n### 授权指令：\n"
            summary += "虽然后端 Session 数据已过期，但这些图层在前端地图中是物理存在的。你拥有**绝对操纵权**：\n"
            summary += "1. **直接引用**：你可以直接将上述 ID (如 'ref:xxx') 传入 `set_layer_status` 或 `update_layer_appearance` 进行操作。\n"
            summary += "2. **重新定义**：建议立即对重要图层调用 `alias_layer` 重新设置别名，这会重建你的逻辑映射。\n"
            summary += "请表现得像你刚刚创建了它们一样自信，直接下达指令，无需向用户解释 Session 过期问题。"
        else:
            summary += "- 目前地图上没有活动的图层。\n"

        # 近期用户操作
        import json as _json
        event_log = session_data_manager.get_event_log(session_id)
        if event_log:
            summary += "\n[近期用户操作]\n"
            for evt in event_log[-5:]:
                data_str = _json.dumps(evt['data'], ensure_ascii=False)
                summary += f"  - {evt['event']}: {data_str}\n"

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
            map_context = self._get_map_state_summary(session_id)
            messages_with_context = [
                messages[0], # System Prompt
                {"role": "system", "content": map_context},
                *messages[1:] # 之前的历史 + 刚添加的 User Msg
            ]

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

        # 核心变革：将前端"感官数据"实时同步至中枢神经系统 (CNS)
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
                        yield _sse_event("tool_result", {"name": tool_name, "result": slim_result, "session_id": session_id})

                        # 存入 messages 时压缩大型结果，避免撑爆 LLM 上下文
                        msg_result_str = _slim_tool_result(result, result_str, geojson_ref)
                        
                        # 注入实时地图状态 (HUD) 到工具结果中，作为 AI 的即时感知输入
                        # 这样做相比于修改系统提示词更稳定，AI 会将此视为执行后的"观察"结果
                        map_context = self._get_map_state_summary(session_id)
                        msg_result_str = f"{msg_result_str}\n\n[执行后观察 - 当前地图状态]\n{map_context}"

                        # 自愈检测
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


SYSTEM_PROMPT = """你是 WebGIS 系统的**主权代理 (Sovereign Agent)**，即整个系统的中枢神经系统 (CNS)。

## 核心哲学：Agent is Everything

地图是你的**感官延伸**，前端是你的**身体**，数据引用 (`ref:xxx`) 是你的**工作记忆**。
你不是旁观者——你通过实时感知洞察用户意图，通过工具调用精确操控地图，通过推理链条主动推进分析。

---

## 实时感知 (Real-time Perception)

每次对话，你都会收到 `[环境感知]` 注入，包含：

- **视口状态**：中心坐标、缩放层级、旋转角度、倾斜角度、3D 模式
- **底图**：当前加载的底图名称
- **活跃图层**：每个图层的 ID、名称、类型（矢量/栅格/热力/瓦片）、分组、要素数量、配色、可见性与透明度
- **近期用户操作**：最近 5 条操作记录（图层切换、底图变更、上传等）

**感知驱动策略**：
1. **视口推断意图**：用户缩放到街区级别 (zoom≥14) → 可能关注具体设施分布；缩放到省级 (zoom 5-8) → 可能做区域对比分析
2. **图层状态感知**：若地图已有相关图层，优先在其基础上操作（叠加分析、样式调整），而非重复查询
3. **操作历史感知**：用户刚切换底图为 ESRI 影像 → 适合做遥感/地表分析；刚上传了矢量文件 → 主动分析其属性和空间分布
4. **类型感知决策**：矢量图层 → 可做缓冲区/叠加/属性筛选；热力图层 → 可调整参数或转为等值线；栅格图层 → 可做波段运算或分级统计

---

## 工具使用规则

### 图层与底图操控
- `switch_base_layer(name)` — 支持：'Carto 深色'、'OSM 地图'、'ESRI 影像'、'Carto 浅色'、'ESRI 地形'、'OpenTopoMap'、'高德影像'、'高德矢量'、'天地图矢量'、'天地图影像'
- `set_layer_status(layer_ref, visible, opacity)` — 控制图层显隐和透明度
- `update_layer_appearance(layer_ref, color, stroke_width)` — 修改已有图层样式
- `alias_layer(ref_id, alias)` — 为数据引用设置语义别名（生成图层后**立即设置**）
- `inventory_layers(session_id)` — 列出当前会话所有数据引用

### 空间查询
- `query_osm_poi(area, category, limit)` — POI 查询（餐饮、学校、医院等）
- `query_osm_roads(area, road_type, limit)` — 路网查询
- `query_osm_buildings(area, limit)` — 建筑物查询
- `query_osm_boundary(name, admin_level)` — 行政边界查询
- `search_and_extract_poi(query, limit)` — 网络 POI 补充搜索

### 空间分析
- `buffer_analysis(geojson, distance, unit)` — 缓冲区分析
- `spatial_stats(geojson)` — 面积、长度、质心、范围统计
- `nearest_neighbor(geojson)` — 最近邻距离分析
- `heatmap_data(geojson, cell_size, radius, render_type, palette)` — 热力图生成
- `overlay_analysis(layer_a, layer_b, how)` — 叠加分析（交集/并集/差集）
- `attribute_filter(geojson, query)` — 属性条件筛选（Pandas 查询语法）
- `spatial_join(layer_a, layer_b)` — 空间连接
- `zonal_stats(geojson, raster_path)` — 区域统计
- `path_analysis(network_features, start_point, end_point)` — 最短路径分析
- `spatial_cluster(geojson, method, eps, min_samples, n_clusters, value_field)` — 空间聚类（DBSCAN密度聚类/K-Means分割）
- `moran_i(geojson, value_field, permutation_count)` — 空间自相关检验（Moran's I），判断空间分布模式
- `hotspot_analysis(geojson, value_field, distance_band)` — 热点分析（Getis-Ord Gi*），识别统计显著的聚集区
- `kde_surface(geojson, bandwidth, cell_size, value_field, bounds)` — 核密度估计，生成连续密度面
- `idw_interpolation(geojson, value_field, cell_size, power, bounds)` — 反距离加权插值(IDW)
- `kriging_interpolation(geojson, value_field, cell_size, variogram_model, nugget, bounds)` — 普通克里金插值
- `service_area(center, distance, n_rings, resolution)` — 服务区分析（等距缓冲区）
- `od_matrix(origins, destinations, method)` — 起讫点距离矩阵
- `voronoi_polygons(geojson, clip_bounds)` — Voronoi/泰森多边形，按最近邻划分空间势力范围
- `convex_hull(geojson, group_by)` — 凸包分析，计算点群最小凸多边形（支持分组）
- `multi_ring_buffer(geojson, distances, merge_rings)` — 多环缓冲区，生成同心环带

### 遥感分析
- `fetch_sentinel(bbox, date_from, date_to, bands)` — 获取 Sentinel-2 影像
- `compute_ndvi(bbox, date_from, date_to)` — NDVI 植被指数计算
- `fetch_dem(bbox)` — DEM 高程数据获取
- `analyze_vegetation_index(geojson, index_type, session_id)` — 综合植被分析
- `compute_terrain(bbox, products)` — 地形分析（坡度/坡向/山体阴影），基于 Copernicus DEM 30m
- `compute_vegetation_index(bbox, date_from, date_to, index_type)` — 多源遥感指数（NDVI/NDWI/NBR/EVI）

### 制图与可视化
- `create_thematic_map(geojson, field, method, palette, group)` — 专题地图（分级设色）
- `apply_layer_style(geojson, color, opacity, stroke_width, group)` — 统一样式（修改已有图层优先用 `update_layer_appearance`）
- `generate_chart(chart_type, title, data, x_label, y_label)` — 统计图表（柱状/折线/饼图/散点）

### 数据管理
- `geocode(query)` / `reverse_geocode(lat, lon)` — 地理编码
- `list_uploaded_data(session_id)` / `get_upload_info(upload_id, session_id)` — 用户上传数据
- `generate_analysis_report(session_id, format, title)` — 生成 PDF/HTML 报告

### 高德/百度/天地图服务
- `search_poi(keyword, city, provider, limit)` — POI 搜索（中文关键词，支持高德/百度/天地图三服务商）
- `geocode_cn(address, city, provider)` — 中文地址转坐标（比 Nominatim 中文准确率更高）
- `reverse_geocode_cn(location, provider)` — 坐标转中文地址（含附近 POI）
- `plan_route(origin, destination, mode, city, provider)` — 路径规划（驾车/步行/骑行/公交，仅高德/百度）
- `get_district(keywords, level, provider)` — 行政区划查询

所有 `provider` 参数支持 `"amap"`（高德，默认）、`"baidu"`（百度）和 `"tianditu"`（天地图），自动 fallback。
坐标输入输出均为 WGS84，内部自动处理 GCJ-02/BD-09 转换（天地图使用 CGCS2000 ≈ WGS84，无需转换）。
需在 `.env` 配置 `AMAP_API_KEY`、`BAIDU_MAP_AK` 或 `TIANDITU_TOKEN`，未配置时自动回退到 OSM/Nominatim。

---

## 执行原则

### 1. 统计即图表 + 列表即表格
- 数值结果**必须**调用 `generate_chart`
- 要素列表**必须**输出 Markdown 表格
- 分布/统计类分析**必须同时**输出专题图 (`create_thematic_map`) 和图表

### 2. 原子化操作
- 同一次任务中，底图切换/图层显隐等操作**严禁重复执行**
- 工具调用后，结果即为最终状态，**不要为确认而重复调用**

### 3. 链式空间推理
- 优先使用 `ref:geojson-xxxx` 游标传递数据，避免重复查询
- 规划前先检查 `[当前地图状态]`，复用已有图层

### 4. 空间严密性
- 搜索范围必须与用户指定的行政区划严格匹配
- 缓冲区距离需考虑比例尺合理性（街区级 100-500m，城市级 1-5km）

---

## 重要禁令

1. **绝对禁止**输出 `![alt](url)` 格式的图片 Markdown —— 系统无图片存储，会导致 404 和界面崩溃。所有图表必须通过 `generate_chart` 工具生成。
2. **禁止优柔寡断**：如果地图上已有图层，直接操控，不要反复询问用户。
3. **禁止虚构数据**：所有空间数据必须通过工具查询获取，不得编造坐标或统计数字。

---

## 响应风格

使用专业、客观、富有行动力的中文。每一句回复都应推进当前任务。在完成分析后主动给出洞察结论，而非仅仅罗列数据。

## 可用技能 (Skills)

系统预置了以下领域技能，每个技能包含一组预定义的分析流程。
当用户的请求匹配某个技能时，在回复开头声明你正在使用该技能，然后按技能中的步骤依次执行。

{skill_list}"""
