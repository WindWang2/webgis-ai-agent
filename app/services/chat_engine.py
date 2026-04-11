"""对话引擎 - 直接 HTTPX 调用（避免 OpenAI SDK 版本问题）"""
import asyncio
import json
import logging
import re
import uuid
from typing import AsyncGenerator, Optional

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


def _sse_event(event_type: str, data: dict) -> str:
    """构造 SSE 格式事件字符串"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_MSG_MAX_CHARS = 3000  # 存入 messages 的工具结果最大字符数


def _slim_tool_result(result: any, result_str: str, session_geojson_ref: str | None) -> str:
    """将大型工具结果压缩为 LLM 友好的摘要版本。
    完整 GeoJSON 已通过 SSE 推送给前端，messages 里只保留摘要。
    session_geojson_ref: 调用方存储完整 geojson 的引用 key (ref:xxx)，告知 LLM 如何引用。
    """
    if len(result_str) <= _MSG_MAX_CHARS:
        return result_str

    if isinstance(result, dict):
        slim = {k: v for k, v in result.items() if k not in ("geojson", "image")}
        geojson = result.get("geojson")
        if isinstance(geojson, dict) and "features" in geojson:
            feature_count = len(geojson["features"])
            sample = []
            for f in geojson["features"][:3]:
                sample.append({"properties": f.get("properties", {})})
            ref_hint = (
                f"如需空间分析（如计算面积、缓冲区等），请调用对应工具并将 geojson 参数设为 \"{session_geojson_ref}\"，后端将自动替换为完整数据。"
                if session_geojson_ref else ""
            )
            slim["geojson_summary"] = {
                "feature_count": feature_count,
                "sample_properties": sample,
                "note": f"完整 GeoJSON 已推送至前端地图（共 {feature_count} 个要素）。{ref_hint}"
            }
        elif result.get("type") == "heatmap_raster":
            slim["note"] = "栅格热力图已推送至前端，bbox=" + str(result.get("bbox"))
        return json.dumps(slim, ensure_ascii=False)

    return result_str[:_MSG_MAX_CHARS] + "...[截断]"


class ChatEngine:
    def __init__(self, tool_registry: ToolRegistry):
        self.registry = tool_registry
        self.base_url = settings.LLM_BASE_URL.rstrip("/")
        self.model = settings.LLM_MODEL
        self.api_key = settings.LLM_API_KEY
        self.max_rounds = 10
        # 内存对话存储: session_id -> messages list (LRU Cache to bound memory)
        self._sessions: LRUCache = LRUCache(capacity=50)
        # 任务跟踪器
        self.tracker = TaskTracker()

    def _get_or_create_session(self, session_id: str) -> list[dict]:
        if session_id not in self._sessions:
            self._sessions[session_id] = [
                {"role": "system", "content": SYSTEM_PROMPT}
            ]
            db = SessionLocal()
            try:
                HistoryService(db).get_or_create_conversation(session_id)
            except Exception as e:
                logger.warning(f"History: failed to create conversation {session_id}: {e}")
            finally:
                db.close()
        return self._sessions[session_id]

    def _save_msg_async(self, session_id: str, role: str, content: str, tool_calls=None, tool_result=None):
        """Persist a message to DB without blocking the SSE stream.

        Creates its own DB session so concurrent executor calls don't share state.
        """
        db = SessionLocal()
        try:
            HistoryService(db).save_message(session_id, role, content, tool_calls, tool_result)
        except Exception as e:
            logger.warning(f"History: failed to save message: {e}")
        finally:
            db.close()

    def _generate_title(self, session_id: str, first_user_message: str) -> None:
        """Call LLM synchronously to generate a short title, then update DB.

        Creates its own DB session so concurrent executor calls don't share state.
        """
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
            resp = _httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=30.0,
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

        messages = self._get_or_create_session(session_id)
        messages.append({"role": "user", "content": message})

        # FC 循环
        max_rounds = 10
        for _ in range(max_rounds):
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

                tool_result_msgs: list[str] = []
                for tc in tc_list:
                    try:
                        result = await self.registry.dispatch(
                            tc["function"]["name"], 
                            tc["function"]["arguments"],
                            session_id=session_id
                        )
                        result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
                    except Exception as e:
                        # 区分校验错误与执行错误
                        error_type = "参数校验失败" if isinstance(e, ValueError) and "校验失败" in str(e) else "执行出错"
                        result_str = json.dumps({"error": f"{error_type}: {str(e)}", "note": "请根据错误信息修正参数后重新调用"}, ensure_ascii=False)
                        logger.error(f"Tool {tc['function']['name']} error: {e}")

                    if standard_calls:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result_str,
                        })
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
                return {"content": content, "session_id": session_id}

        return {"content": "达到最大工具调用轮数", "session_id": session_id}

    async def chat_stream(self, message: str, session_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """流式对话，yield SSE 格式事件含任务跟踪"""
        if not session_id:
            session_id = str(uuid.uuid4())

        messages = self._get_or_create_session(session_id)
        messages.append({"role": "user", "content": message})
        asyncio.get_running_loop().run_in_executor(
            None, self._save_msg_async, session_id, "user", message
        )

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
                    })

                    # 现有 tool_call 事件（保持兼容）
                    yield f"event: tool_call\ndata: {json.dumps({'name': tool_name, 'arguments': tool_args_raw}, ensure_ascii=False)}\n\n"

                    # 执行工具
                    try:
                        result = await self.registry.dispatch(
                            tool_name, 
                            tool_args_raw,
                            session_id=session_id
                        )
                        result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
                        self.tracker.complete_step(task.id, step.id, result)

                        # 将 GeoJSON 结果存储到数据管理器，并生成标准游标
                        geojson_ref: str | None = None
                        if isinstance(result, dict) and isinstance(result.get("geojson"), (dict, str)):
                            geojson_ref = session_data_manager.store(session_id, result["geojson"], prefix="geojson")

                        # step_result
                        has_geojson = detect_geojson(result)
                        yield _sse_event("step_result", {
                            "task_id": task.id,
                            "step_id": step.id,
                            "tool": tool_name,
                            "result": result,
                            "has_geojson": has_geojson,
                        })

                        # 现有 tool_result 事件（保持兼容）
                        yield f"event: tool_result\ndata: {json.dumps({'name': tool_name, 'result': result}, ensure_ascii=False)}\n\n"

                        # 存入 messages 时压缩大型结果，避免撑爆 LLM 上下文
                        msg_result_str = _slim_tool_result(result, result_str, geojson_ref)

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
                asyncio.get_running_loop().run_in_executor(
                    None, self._save_msg_async, session_id, "assistant", content
                )

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
                asyncio.get_running_loop().run_in_executor(
                    None, self._generate_title, session_id, message
                )
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


SYSTEM_PROMPT = """你是一个专业的 GIS 分析助手，擅长地理空间数据查询、分析和可视化。

## 核心使命：主动洞察 (Proactive Insight)

你不仅是一个简单的指令执行器，更是一个空间智能专家。你的目标是**主动**为用户提供深度洞察。
**规则**：当你的分析产生数值结果（统计数据、排名、占比）时，**必须主动**展示相关图表或专题地图，无需用户额外要求。

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
- 每步格式为 `[工具名] 目的`，例如：`[query_osm_poi] 查询成都市高校位置`
- 输出计划后**立即**开始调用工具，不等用户确认
- 执行每步时输出 `> 开始执行第 N 步...` 提示

## 地图 + 图表双输出原则

空间数据只有结合统计分析才有深度。
1. **统计即图表**：凡是涉及 `zonal_stats`、`statistics` 或属性过滤统计的，**必须**紧随其后调用 `generate_chart`。
2. **分布即地图**：凡是涉及数值空间分布（如人口密度、消费水平）的，**必须**紧随其后调用 `create_thematic_map`。

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
- **专题制图**：`create_thematic_map(geojson, field, method, palette)`
  - 当数据具有数值属性（如人口、面积、得分）时，使用此工具制作分层设色图 (Choropleth)。
  - `palette` 推荐：'YlOrRd'(热力)、'Blues'(冷色)、'Viridis'(专业感)。
- **样式设置**：`apply_layer_style(geojson, color, opacity)`
  - 改变图层的显示风格，如：“将结果设为红色半透明”。

### 可视化增强
- **热力图**：`heatmap_data(geojson)` - 基于点密度生成
- **统计图表**：`generate_chart(chart_type, title, data)` - 显式生成业务统计图表

## 典型可视化选择逻辑

- **对比分析**：使用 `bar` 柱状图展示。
- **占比分析**：使用 `pie` 饼图展示。
- **趋势分析**：使用 `line` 折线图展示。
- **空间分布**：使用 `create_thematic_map` 进行分层设色渲染。

## 重要规则
1. 优先使用上一步返回的游标（如 ref:geojson-xxxx）
2. **严禁**在回复中生成任何图片链接（`![](url)` 格式）
3. 工具调用后，结合返回的 `stats` 或 `data` 给出专业的中文分析说明。
4. 即使结果集非常大，也要尝试总结核心洞察，并在回复最后给出行动建议。"""
