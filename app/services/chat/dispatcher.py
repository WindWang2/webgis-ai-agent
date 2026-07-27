"""工具调度残留：is_suspicious_result 纯函数。

unified-tool-dispatch 票据 03：原 dispatch_tool 的六项横切职责已迁入
ToolDispatchService（app/services/tool_dispatch_service.py），legacy ChatEngine
路径与 Pi 路径现在共用该服务。本模块仅保留 is_suspicious_result 纯函数——
它被 chat_engine._log_tool_decision 与 ToolDispatchService 内部副本同时使用。

票据 04（contract）会在收敛完成后决定该函数的最终归宿（迁入 service 或独立
helper 模块），届时本文件可能被整体删除。
"""
from __future__ import annotations

from typing import Any


def is_suspicious_result(result: Any) -> bool:
    """检测工具返回的结果是否"可疑"（空数据 / 错误响应），用于触发自愈提示。

    纯函数无副作用，方便单测枚举所有可疑形状。
    """
    if not result:
        return True
    if isinstance(result, dict):
        if result.get("success") is False:
            return True
        if result.get("type") == "FeatureCollection" and not result.get("features"):
            return True
        if "data" in result and isinstance(result["data"], list) and not result["data"]:
            return True
        if "poi_count" in result and result["poi_count"] == 0:
            return True
    if isinstance(result, list) and not result:
        return True
    return False
