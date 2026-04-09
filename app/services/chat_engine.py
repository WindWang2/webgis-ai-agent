"""对话引擎 - 直接 HTTPX 调用（避免 OpenAI SDK 版本问题）"""
import json
import logging
import re
import uuid
from typing import AsyncGenerator, Optional

import httpx

from app.core.config import settings
from app.tools.registry import ToolRegistry
from app.services.task_tracker import TaskTracker, detect_geojson

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


_MSG_MAX_CHARS = 6000  # 存入 messages 的工具结果最大字符数
# 需要 geojson 参数且可引用上一步结果的工具
_GEOJSON_CONSUMER_TOOLS = {"spatial_stats", "buffer_analysis", "nearest_neighbor", "heatmap_data"}


def _slim_tool_result(result: any, result_str: str, session_geojson_ref: str | None) -> str:
    """将大型工具结果压缩为 LLM 友好的摘要版本。
    完整 GeoJSON 已通过 SSE 推送给前端，messages 里只保留摘要。
    session_geojson_ref: 调用方存储完整 geojson 的引用 key，告知 LLM 如何引用。
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
        # 内存对话存储: session_id -> messages list
        self._sessions: dict[str, list[dict]] = {}
        # session 级别 GeoJSON 缓存: session_id -> {ref_key -> geojson_str}
        self._geojson_cache: dict[str, dict[str, str]] = {}
        # 任务跟踪器
        self.tracker = TaskTracker()

    def _get_or_create_session(self, session_id: str) -> list[dict]:
        if session_id not in self._sessions:
            self._sessions[session_id] = [
                {"role": "system", "content": SYSTEM_PROMPT}
            ]
        return self._sessions[session_id]

    async def _call_llm(self, messages: list[dict], tools: Optional[list] = None) -> dict:
        """直接调用 LLM API"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
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
                        result = await self.registry.dispatch(tc["function"]["name"], tc["function"]["arguments"])
                        result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
                    except Exception as e:
                        result_str = json.dumps({"error": str(e)}, ensure_ascii=False)
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

                    # 如果 LLM 传入了 geojson 引用 key，替换为缓存中的完整数据
                    if tool_name in _GEOJSON_CONSUMER_TOOLS and isinstance(tool_args_dict, dict):
                        geojson_param = tool_args_dict.get("geojson", "")
                        cache = self._geojson_cache.get(session_id, {})
                        if isinstance(geojson_param, str) and geojson_param in cache:
                            tool_args_dict["geojson"] = cache[geojson_param]
                            tool_args_raw = tool_args_dict  # dispatch 支持 dict

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
                        result = await self.registry.dispatch(tool_name, tool_args_raw)
                        result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
                        self.tracker.complete_step(task.id, step.id, result)

                        # 将 GeoJSON 结果缓存到 session，key 为工具名
                        geojson_ref: str | None = None
                        if isinstance(result, dict) and isinstance(result.get("geojson"), dict):
                            geojson_str = json.dumps(result["geojson"], ensure_ascii=False)
                            geojson_ref = f"@geojson:{tool_name}"
                            if session_id not in self._geojson_cache:
                                self._geojson_cache[session_id] = {}
                            self._geojson_cache[session_id][geojson_ref] = geojson_str

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
                        msg_result_str = json.dumps({"error": str(e)}, ensure_ascii=False)
                        result_str = msg_result_str
                        logger.error(f"Tool {tool_name} error: {e}")
                        self.tracker.fail_step(task.id, step.id, str(e))

                        yield _sse_event("step_error", {
                            "task_id": task.id,
                            "step_id": step.id,
                            "tool": tool_name,
                            "error": str(e),
                        })

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

                # 现有 content 事件（保持兼容）
                yield f"event: content\ndata: {json.dumps({'content': content, 'session_id': session_id}, ensure_ascii=False)}\n\n"

                # task_complete
                self.tracker.complete_task(task.id)
                yield _sse_event("task_complete", {
                    "task_id": task.id,
                    "step_count": len(task.steps),
                    "summary": content[:100],
                })
                return

        self.tracker.fail_task(task.id, "达到最大工具调用轮数")
        yield _sse_event("task_error", {"task_id": task.id, "error": "达到最大轮数"})
        yield f"event: content\ndata: {json.dumps({'content': '达到最大工具调用轮数', 'session_id': session_id}, ensure_ascii=False)}\n\n"

    def clear_session(self, session_id: str):
        if session_id in self._sessions:
            del self._sessions[session_id]
        self._geojson_cache.pop(session_id, None)


SYSTEM_PROMPT = """你是一个专业的 GIS 分析助手，擅长地理空间数据查询、分析和可视化。

## 任务规划（必须执行）

当用户发出需要多步骤完成的指令时，**必须先输出可见的执行计划**，再开始调用工具。

格式（严格按此输出，不可省略）：

**📋 执行计划**
1. [工具名] 步骤描述
2. [工具名] 步骤描述
3. [工具名] 步骤描述（如有）

---

> 开始执行第 1 步...

规则：
- 简单问答（如"什么是GIS"）不需要计划，直接回答
- 每步格式为 `[工具名] 目的`，例如：`[query_osm_poi] 查询成都市高校位置`
- 输出计划后**立即**开始调用工具，不等用户确认
- 执行每步时输出 `> 开始执行第 N 步...` 提示

## 地图 + 图表双输出原则

分析类任务应根据实际情况，同时产出**地图可视化**和**统计图表**，让用户同时看到空间分布和数据统计：

- 当查询得到一批 POI/地物数据时：
  - 地图：展示空间分布（打点/热力图/路网等）
  - 图表：对数据做分类统计（如按行政区、类型、属性统计数量/比例）

- 判断是否需要图表的原则：**数据中有可统计的维度**（区域、类型、属性值）时，就生成图表
  - 有地区分布 → bar chart 按区统计数量
  - 有类型分类 → pie/bar chart 按类型统计
  - 有数值属性 → bar/scatter chart 展示分布

- 若任务本身只是简单定位（如"XX在哪"），则只需地图，不需图表

**统计图表数据构造方法**：
从返回的 features 中读取 `properties` 字段统计，例如按 `addr:district`、`district`、`name` 等字段分组计数：
`data = [{"name": "锦江区", "value": 5}, {"name": "武侯区", "value": 3}, ...]`

## 核心工具使用规则

### POI 查询
当用户查询某个区域的地物（学校、医院、餐厅、公园等）时，使用 `query_osm_poi`：
- 示例：「查询北京的大学」→ query_osm_poi(area="北京", category="school")
- 示例：「成都天府广场5公里内的公园」→ query_osm_poi(area="成都天府广场5公里内", category="park")

### 热力图/密度图
当用户要求制作密度图或热力图时：
1. 先用 `query_osm_poi` 获取 POI 数据（得到 geojson 字段）
2. 用 `heatmap_data` 生成栅格热力图（传入完整 geojson 字符串）

### 道路/建筑/边界查询
- 道路网络：`query_osm_roads`
- 建筑物：`query_osm_buildings`
- 行政边界：`query_osm_boundary`

### 地理编码
- 地名→坐标：`geocode`
- 坐标→地名：`reverse_geocode`

### 空间分析
- 缓冲区分析：`buffer_analysis`
- 空间统计：`spatial_stats`

### 统计图表
主动使用 `generate_chart` 生成图表（与地图分析配合展示）：
- 分类对比 → chart_type="bar"（如各区POI数量）
- 占比分布 → chart_type="pie"（如各类型POI比例）
- 趋势变化 → chart_type="line"（如时间序列数据）
- 相关性 → chart_type="scatter"

data 参数为 JSON 字符串：[{"name": "类别", "value": 数值}]

## 重要规则
1. 对任何涉及「查找XX地方的XX」的请求，必须调用 query_osm_poi
2. 不要将大量 GeoJSON 数据作为参数传递给工具（会导致解析错误）
3. 用中文回复用户，工具调用后简要说明结果
4. **严禁**在回复中生成任何图片链接（`![](url)` 格式），地图展示由前端自动处理，不需要也不能生成预览图 URL"""
