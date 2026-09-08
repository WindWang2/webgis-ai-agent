"""ExtDemo 健康检查（manifest.diagnostics_entry = "health:check"）。

宿主在激活后与健康探针时调用本模块的 ``check()``：返回宿主约定的
``{status, messages}`` 形态（status ∈ healthy | degraded | unhealthy）。
"""

from __future__ import annotations


def check():
    """静态示例包：目录文件随包发行，恒为 healthy（无外部依赖）。"""
    return {"status": "healthy", "messages": []}
