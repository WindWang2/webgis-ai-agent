"""对话引擎 - 支持地图联动的智能对话系统"""
import json
import logging
import uuid
from typing import AsyncGenerator, Optional
import httpx
from app.tools.registry import ToolRegistry
logger = logging.getLogger(__name__)

# 内存对话存储: session_id -> messages list
_session_history: dict[str, list[dict]] = {}

# 地图操作指令缓存: session_id -> map_action dict
_map_actions: dict[str, dict] = {}


def _get_or_create_session(session_id: str) -> list[dict]:
    """获取或创建会话历史"""
    if session_id not in _session_history:
        _session_history[session_id] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
    return _session_history[session_id]


def _set_map_action(session_id: str, action: dict):
    """设置地图操作指令（供工具调用后设置）"""
    _map_actions[session_id] = action


class ChatEngine:
    def __init__(self, tool_registry: ToolRegistry):
        self.registry = tool_registry
        # 从 settings 获取 LLM 配置（如果没有配置则使用默认值）
        try:
            from app.core.config import settings
            self.base_url = settings.LLM_BASE_URL.rstrip("/") if settings.LLM_BASE_URL else "http://192.168.193.70:8000/v1"
            self.model = settings.LLM_MODEL if hasattr(settings, 'LLM_MODEL') and settings.LLM_MODEL else "MiniMax-Text-01"
            self.api_key = settings.LLM_API_KEY if hasattr(settings, 'LLM_API_KEY') and settings.LLM_API_KEY else "EMPTY"
        except Exception:
            # 默认值
            self.base_url = "http://192.168.193.70:8000/v1"
            self.model = "MiniMax-Text-01"
            self.api_key = "EMPTY"

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
        """非流式对话，支持工具调用和地图联动"""
        if not session_id:
            session_id = str(uuid.uuid4())

        messages = _get_or_create_session(session_id)
        messages.append({"role": "user", "content": message})
        
        # 清除该会话的地图操作指令
        if session_id in _map_actions:
            del _map_actions[session_id]

        # FC 循环
        max_rounds = 10
        for _ in range(max_rounds):
            tool_schemas = self.registry.get_schemas() if self.registry.get_schemas() else None
            response = await self._call_llm(messages, tool_schemas)
            choice = response.get("choices", [{}])[0]
            assistant_msg = choice.get("message", {})

            # 检查是否有 tool_calls
            if assistant_msg.get("tool_calls"):
                # 保存 assistant 消息（含 tool_calls）
                messages.append({
                    "role": "assistant",
                    "content": assistant_msg.get("content", ""),
                    "tool_call": assistant_msg.get("tool_calls", [])
                })

                # 执行每个 tool call
                for tc in assistant_msg.get("tool_calls", []):
                    try:
                        result = await self.registry.dispatch(
                            tc["function"]["name"], 
                            tc["function"]["arguments"]
                        )
                        # 检查结果是否包含 map_action
                        if isinstance(result, dict) and result.get("action"):
                            _set_map_action(session_id, result)
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
                # 无 tool_call，最终回复
                content = assistant_msg.get("content", "")
                messages.append({"role": "assistant", "content": content})
                
                # 获取地图操作指令
                map_action = _map_actions.get(session_id)
                
                return {
                    "content": content, 
                    "session_id": session_id,
                    "map_action": map_action
                }

        return {"content": "达到最大工具调用轮数", "session_id": session_id}

    async def chat_stream(self, message: str, session_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """流式对话，yield SSE 格式事件"""
        if not session_id:
            session_id = str(uuid.uuid4())

        messages = _get_or_create_session(session_id)
        messages.append({"role": "user", "content": message})
        
        # 清除该会话的地图操作指令
        if session_id in _map_actions:
            del _map_actions[session_id]

        # 先做 FC 循环（工具调用不支持流式），最终回复时流式输出
        max_rounds = 10
        for _ in range(max_rounds):
            tool_schemas = self.registry.get_schemas() if self.registry.get_schemas() else None
            response = await self._call_llm(messages, tool_schemas)
            choice = response.get("choices", [{}])[0]
            assistant_msg = choice.get("message", {})

            if assistant_msg.get("tool_calls"):
                # 发送 tool_calling 事件
                for tc in assistant_msg.get("tool_calls", []):
                    yield f"event: tool_call\ndata: {json.dumps({'name': tc['function']['name'], 'arguments': tc['function']['arguments']}, ensure_ascii=False)}\n\n"

                messages.append({
                    "role": "assistant",
                    "content": assistant_msg.get("content", ""),
                    "tool_call": assistant_msg.get("tool_calls", [])
                })

                for tc in assistant_msg.get("tool_calls", []):
                    try:
                        result = await self.registry.dispatch(
                            tc["function"]["name"], 
                            tc["function"]["arguments"]
                        )
                        # 检查结果是否包含 map_action
                        if isinstance(result, dict) and result.get("action"):
                            _set_map_action(session_id, result)
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
                
                # 获取地图操作指令
                map_action = _map_actions.get(session_id)
                
                yield f"event: content\ndata: {json.dumps({'content': content, 'session_id': session_id, 'map_action': map_action}, ensure_ascii=False)}\n\n"
                return

        yield f"event: content\ndata: {json.dumps({'content': '达到最大工具调用轮数', 'session_id': session_id}, ensure_ascii=False)}\n\n"

    def clear_session(self, session_id: str):
        """清除会话历史"""
        if session_id in _session_history:
            del _session_history[session_id]
        if session_id in _map_actions:
            del _map_actions[session_id]


SYSTEM_PROMPT = """你是一个专业的 GIS 分析助手。你可以帮助用户进行地理空间数据分析、地图操作和遥感数据处理。

你可以使用以下工具：
- 地理编码：将地名转换为坐标（geocoding）
- OSM 查询：查询 OpenStreetMap 中的 POI、 路网、建筑等数据（osm）
- 遥感数据：获取 Sentinel 卫星影像、 DEM 高程数据（remote_sensing）
- 空间分析：缓冲区分析、叠加分析、空间统计等（spatial）
- 地图操作：设置地图视图、 添加图层、 清除图层（map_action）
  * set_map_view: 设置地图中心点和缩放级别
  * add_map_layer: 在地图上添加 GeoJSON 图层
  * clear_map_layer: 清除地图上的图层
  * get_map_state: 获取当前地图状态

**地图联动**：当你需要展示地理数据到地图上时，使用 map_action 工具返回地图操作指令。
请用中文回复用户。当用户询问地理位置或空间分析问题时，主动使用工具获取数据并联动地图展示。"""