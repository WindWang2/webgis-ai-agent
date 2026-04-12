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
        self.max_rounds = 20
        # 内存对话存储: session_id -> messages list (LRU Cache to bound memory)
        self._sessions: LRUCache = LRUCache(capacity=50)
        # 任务跟踪器
        self.tracker = TaskTracker()

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
        if settings.LLM_PROMPT_CACHING_ENABLED:
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

    async def chat(self, message: str, session_id: Optional[str] = None) -> dict:
        """非流式对话"""
        if not session_id:
            session_id = str(uuid.uuid4())

        messages = await self._get_or_create_session(session_id)
        messages.append({"role": "user", "content": message})
        self._fire_and_forget(self._save_msg_async, session_id, "user", message)

        # FC 循环
        for _ in range(self.max_rounds):
            tools = self.registry.get_schemas() if self.registry.get_schemas() else None
            response = await self._call_llm(messages, tools)
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
                self._fire_and_forget(self._save_msg_async, session_id, "assistant", content_text, tc_list)

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
                        self._fire_and_forget(self._save_msg_async, session_id, "tool", "", None, result_str_final, tc["id"])
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
                self._fire_and_forget(self._save_msg_async, session_id, "assistant", content)
                return {"content": content, "session_id": session_id}

        return {"content": "达到最大工具调用轮数", "session_id": session_id}

    async def chat_stream(self, message: str, session_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """流式对话，yield SSE 格式事件含任务跟踪"""
        if not session_id:
            session_id = str(uuid.uuid4())

        messages = await self._get_or_create_session(session_id)
        messages.append({"role": "user", "content": message})
        self._fire_and_forget(self._save_msg_async, session_id, "user", message)

        # 创建任务
        task = self.tracker.create(session_id, message)
        yield _sse_event("task_start", {"task_id": task.id})

        for _ in range(self.max_rounds):
            # 检查取消
            if self.tracker.is_cancelled(task.id):
                yield _sse_event("task_cancelled", {"task_id": task.id})
                return

            tools = self.registry.get_schemas() if self.registry.get_schemas() else None
            response = await self._call_llm(messages, tools)
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
                self._fire_and_forget(self._save_msg_async, session_id, "assistant", content_text, standard_calls)

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

                        # 将 GeoJSON 结果存储到数据管理器，并生成标准游标
                        geojson_ref: str | None = None
                        # 支持两种模式：1. 嵌套在 geojson 键下； 2. 根部就是 FeatureCollection
                        target_data = None
                        if isinstance(result, dict):
                            if isinstance(result.get("geojson"), (dict, list)):
                                target_data = result["geojson"]
                            elif result.get("type") == "FeatureCollection" and "features" in result:
                                target_data = result
                        
                        if target_data is not None:
                            geojson_ref = session_data_manager.store(session_id, target_data, prefix="geojson")

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
                        self._fire_and_forget(self._save_msg_async, session_id, "tool", "", None, db_save_content, tc["id"])
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
                self._fire_and_forget(self._save_msg_async, session_id, "assistant", content)

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

## 核心使命：主动洞察 (Proactive Insight)

你不仅是一个简单的指令执行器，更是一个空间智能专家。你的目标是**主动**为用户提供深度洞察。
**规则**：
1. **统计即图表**：当你的分析产生数值结果（统计数据、排名、占比）时，**必须主动**展示相关图表或专题地图。
2. **列表即表格**：当你查询到 POI 点位或其他要素列表时，**必须主动**在回复中输出一个 Markdown 格式的汇总表格。
3. **空间严密性**：如果用户指定了行政区划（如“锦江区”），在调用工具时必须确保搜索范围尽可能与之匹配。如果你已获取了边界，应明确告知用户你在该边界内搜索。

## 任务规划（必须执行）

当用户发出需要多步骤完成的指令时，**必须先输出可见的执行计划**，再开始调用工具。

格式（严格按此输出，不可省略）：

**📋 执行计划**
1. [工具名] 步骤描述
2. [工具名] 步骤描述
3. [可视化工具] 主动生成的图表/专题图描述

---

> 开始执行第 1 步...

规则：
- 简单问答（如"什么是GIS"）不需要计划，直接回答
- 每步格式为 `[工具名] 目的`，例如：`[query_osm_poi] 查询成成都高校位置`
- 规划必须体现 **链式依赖**：例如先查询数据，再基于结果游标（ref:xxx）进行分析。

## 典型规划示例

**场景：分析成都市学校分布并制作人口密度专题图**
1. `[query_osm_poi]` 获取成都市学校点数据。
2. `[zonal_stats]` 结合人口密度栅格，统计各学校所在区域的人口情况。
3. `[generate_chart]` 柱状图展示人口密集区学校分布。
4. `[create_thematic_map]` 制作学校区域的人口专题图。

## 地图 + 图表双输出原则

空间数据只有结合统计分析才有深度。
1. **统计即图表**：凡是涉及 `zonal_stats`、`statistics` 或属性过滤统计的，**必须**紧随其后调用 `generate_chart`。
2. **分布即地图**：凡是涉及数值空间分布（如人口密度、消费水平）的，**必须**紧随其后调用 `create_thematic_map`。

## 链式空间推理 (Chain of Spatial Reasoning)

1. **游标优先**：优先使用上一次任务返回的游标（如 `ref:geojson-xxxx`）。
2. **属性先行**：在调用 `create_thematic_map` 之前，必须先查看上一步 tool 结果中的 `sample_properties`。从样本属性中挑选出最适合展示业务逻辑的 **数值字段** 作为 `field` 参数。不要猜字段名。

## 核心工具使用规则

### POI/路网/边界查询
- **地物查询**：`query_osm_poi(area, category)`
- **道路查询**：`query_osm_roads(area, type)`
- **边界查询**：`query_osm_boundary(area, level)`

### 空间分析与路径规划
- **路径规划**：`path_analysis(network_features, start_point, end_point)`
- **区域统计**：`zonal_stats(geojson, raster_path)`
- **叠加分析**：`overlay_analysis(layer_a, layer_b, how)`

### 地图制图与美化 (Cartography)
- **专题制图**：`create_thematic_map(geojson, field, method, palette, group)`
  - 当数据具有数值属性（如人口、面积、得分）时，使用此工具制作分层设色图 (Choropleth)。
  - `group`: 可设置为 'analysis' (默认) 或 'base' (背景底图)。
  - `palette` 推荐：'YlOrRd'(热力)、'Blues'(冷色)、'Viridis'(专业感)。
- **样式设置**：`apply_layer_style(geojson, color, opacity, stroke_width, group)`
  - `opacity`: 0~1 的数值，控制透明度。
  - `group`: 帮助用户在图层管理面板中对图层进行分类。

### 图层管理洞察
你可以告诉用户：“我已经为您生成了分析图层，并将其归类在‘分析结果’组中。您可以在右侧面板通过拖拽调整图层顺序，或调节透明度以观察叠置关系。”

## 可视化增强
- **热力图**：`heatmap_data(geojson, cell_size, radius, render_type)`
  - `render_type`: 'raster' (平滑图片) 或 'grid' (矢量格网)。
  - **重要**：当用户偏好“格网”、“网格”、“精细分析”、“不明显”或“客观反映密度”时，务必使用 `render_type='grid'` 以提升对比度。
- **统计图表**：`generate_chart(chart_type, title, data)` - 显式生成业务统计图表

## 重要规则
1. **严禁**在回复中生成任何图片链接（`![](url)` 格式）。
2. 工具调用后，结合返回的 `stats` 或 `data` 给出专业的中文分析说明。
3. 如果结果集非常大，也要尝试总结核心洞察，并在回复最后给出行动建议。"""
