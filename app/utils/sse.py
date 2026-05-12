import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

def _serialize_sse_data(data: Any) -> str:
    """
    安全地将数据序列化为 JSON 字符串，防止序列化失败导致流中断。
    支持 dict, list, Pydantic v1 (dict()) 和 Pydantic v2 (model_dump())。
    """
    try:
        # Pydantic v2
        if hasattr(data, "model_dump"):
            return json.dumps(data.model_dump(), ensure_ascii=False)
        # Pydantic v1
        if hasattr(data, "dict"):
            return json.dumps(data.dict(), ensure_ascii=False)
        # Standard types
        return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        logger.error(f"SSE serialization error: {e}, data type: {type(data)}")
        # 尝试提取 session_id 或 task_id 用于基本的错误追踪
        info = {}
        if isinstance(data, dict):
            if "session_id" in data: info["session_id"] = data["session_id"]
            if "task_id" in data: info["task_id"] = data["task_id"]
        
        return json.dumps({
            "error": "Internal serialization error",
            "detail": str(e),
            **info
        }, ensure_ascii=False)


def sse_event(event_type: str, data: Any) -> str:
    """
    构造标准 SSE 格式事件字符串。
    
    格式:
    event: {event_type}
    data: {json_string}
    \n\n
    """
    return f"event: {event_type}\ndata: {_serialize_sse_data(data)}\n\n"
