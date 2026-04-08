"""对话引擎 - 直接 HTTPX 调用（避免 OpenAI SDK 版本问题）"""
import json
import logging
import uuid
from typing import AsyncGenerator, Optional

import httpx

from app.core.config import settings
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ChatEngine:
    def __init__(self, tool_registry: ToolRegistry):
        self.registry = tool_registry
        self.base_url = settings.LLM_BASE_URL.rstrip("/")
        self.model = settings.LLM_MODEL
        self.api_key = settings.LLM_API_KEY
        # 内存对话存储: session_id -> messages list
        self._sessions: dict[str, list[dict]] = {}

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
            "max_tokens": 4096,
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

            # 检查是否有 tool_calls
            if assistant_msg.get("tool_calls"):
                # 保存 assistant 消息（含 tool_calls）
                messages.append({
                    "role": "assistant",
                    "content": assistant_msg.get("content", ""),
                    "tool_calls": assistant_msg.get("tool_calls", [])
                })

                # 执行每个 tool call
                for tc in assistant_msg.get("tool_calls", []):
                    try:
                        result = await self.registry.dispatch(tc["function"]["name"], tc["function"]["arguments"])
                        result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
                    except Exception as e:
                        result_str = json.dumps({"error": str(e)}, ensure_ascii=False)
                        logger.error(f"Tool {tc['function']['name']} error: {e}")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_str,
                    })
                continue  # 继续循环让 LLM 处理工具结果
            else:
                # 无 tool_calls，最终回复
                content = assistant_msg.get("content", "")
                messages.append({"role": "assistant", "content": content})
                return {"content": content, "session_id": session_id}

        return {"content": "达到最大工具调用轮数", "session_id": session_id}

    async def chat_stream(self, message: str, session_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """流式对话，yield SSE 格式事件"""
        if not session_id:
            session_id = str(uuid.uuid4())

        messages = self._get_or_create_session(session_id)
        messages.append({"role": "user", "content": message})

        # 先做 FC 循环（工具调用不支持流式），最终回复时流式输出
        max_rounds = 10
        for _ in range(max_rounds):
            tools = self.registry.get_schemas() if self.registry.get_schemas() else None
            response = await self._call_llm(messages, tools)
            choice = response.get("choices", [{}])[0]
            assistant_msg = choice.get("message", {})

            if assistant_msg.get("tool_calls"):
                # 发送 tool_calling 事件
                for tc in assistant_msg.get("tool_calls", []):
                    yield f"event: tool_call\ndata: {json.dumps({'name': tc['function']['name'], 'arguments': tc['function']['arguments']}, ensure_ascii=False)}\n\n"

                messages.append({
                    "role": "assistant",
                    "content": assistant_msg.get("content", ""),
                    "tool_calls": assistant_msg.get("tool_calls", [])
                })

                for tc in assistant_msg.get("tool_calls", []):
                    try:
                        result = await self.registry.dispatch(tc["function"]["name"], tc["function"]["arguments"])
                        result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
                        yield f"event: tool_result\ndata: {json.dumps({'name': tc['function']['name'], 'result': result}, ensure_ascii=False)}\n\n"
                    except Exception as e:
                        result_str = json.dumps({"error": str(e)}, ensure_ascii=False)
                        logger.error(f"Tool {tc['function']['name']} error: {e}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_str,
                    })
                continue
            else:
                # 最终回复
                content = assistant_msg.get("content", "")
                messages.append({"role": "assistant", "content": content})
                yield f"event: content\ndata: {json.dumps({'content': content, 'session_id': session_id}, ensure_ascii=False)}\n\n"
                return

        yield f"event: content\ndata: {json.dumps({'content': '达到最大工具调用轮数', 'session_id': session_id}, ensure_ascii=False)}\n\n"

    def clear_session(self, session_id: str):
        if session_id in self._sessions:
            del self._sessions[session_id]


SYSTEM_PROMPT = """你是一个专业的 GIS 分析助手，擅长地理空间数据查询、分析和可视化。

## 核心工具使用规则

### POI 查询（必须首先使用）
当用户查询某个区域的地物（学校、医院、餐厅、公园等）时，必须使用 `query_osm_poi` 工具：
- 示例：「查询北京的大学」→ query_osm_poi(area="北京", category="school")
- 示例：「成都天府广场5公里内的公园」→ query_osm_poi(area="成都天府广场5公里内", category="park")

### 热力图/密度图（两步操作）
当用户要求制作密度图或热力图时：
1. 先用 `query_osm_poi` 获取 POI 数据
2. 然后「不要」调用 heatmap_data 工具（因为需要传入大量 GeoJSON 数据，容易出错），而是直接告诉用户已获取数据并在地图上显示

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

## 重要规则
1. 对于任何涉及「查找XX地方的XX」的请求，必须调用 query_osm_poi
2. 不要将大量 GeoJSON 数据作为参数传递给工具（会导致 JSON 解析错误）
3. 用中文回复用户
4. 工具调用后，简要说明查询结果"""
