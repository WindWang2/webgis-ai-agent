"""SSE / 工具结果脱敏兼容模块（已废弃）。

职责已重构收拢：
- `ToolDispatchService` (app/services/tool_dispatch_service.py) 拥有脱敏、BBox 计算与自愈包装职责
- `llm_client` (app/services/chat/llm_client.py) 拥有 XML 解析与 LRUCache 职责

本文件仅保留符号 re-export，以兼容第三方或旧测试导入。
"""
from app.services.chat.llm_client import LRUCache, parse_minimax_xml_tool_calls
from app.services.tool_dispatch_service import (
    calculate_bbox,
    is_error_dict,
    normalize_tool_args,
    slim_event_result,
    slim_tool_result,
    wrap_error_dict_for_llm,
)

__all__ = [
    "LRUCache",
    "parse_minimax_xml_tool_calls",
    "normalize_tool_args",
    "is_error_dict",
    "wrap_error_dict_for_llm",
    "slim_tool_result",
    "calculate_bbox",
    "slim_event_result",
]
