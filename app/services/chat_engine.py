"""对话引擎 - ChatEngine 编排实现。

本文件中的 ChatEngine 继承自 app.services.chat.execution_engine.ChatExecutionEngine。
保留所有旧的导出的函数名与结构，保持外部 import 完全兼容。
"""
import logging

from app.services.chat.execution_engine import ChatExecutionEngine

# ─── 从拆出的子模块 re-export，保留旧符号兼容 ───────────
from app.services.chat.llm_client import (
    LLMConfig,
    LRUCache,
    call_llm,
    call_llm_stream,
    parse_minimax_xml_tool_calls as _parse_minimax_xml_tool_calls,
)
from app.services.chat.prompt import (
    SYSTEM_PROMPT,
    construct_self_healing_message as _construct_self_healing_message,
)
from app.services.chat.context_builder import (
    build_map_state_summary as _build_map_state_summary,
    format_layer_lines as _format_layer_lines,
    build_last_analysis_context as _build_last_analysis_context,
    compose_request_messages as _compose_request_messages_fn,
)
from app.services.tool_dispatch_service import (
    ToolDispatchResult,
    ToolDispatchService,
    is_suspicious_result as _is_suspicious_result_fn,
    slim_tool_result as _slim_tool_result,
)
from app.services.chat.tool_pipeline import ToolExecutionPipeline, ToolExecutionResult

logger = logging.getLogger(__name__)


class ChatEngine(ChatExecutionEngine):
    """ChatEngine 兼容层 — 继承自深层 ChatExecutionEngine"""
    pass
