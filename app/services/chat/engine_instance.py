"""App-level ChatEngine / ToolRegistry 单例持有器（E-2 / #893 分层收口）。

此前两个单例挂在 api/routes/chat.py 的模块全局上（`chat.engine` /
`chat.registry`），services/tools 层需要时就近反向 import 路由模块，形成
api→services→api 隐式环。下沉到 services 层后：main.py lifespan 注入，
路由层与底层各自从同一处读取。
"""
from __future__ import annotations

from typing import Optional

from app.tools.registry import ToolRegistry

_engine = None  # ChatEngine（延迟类型注解避免循环 import）
_registry: Optional[ToolRegistry] = None


def set_chat_engine(engine) -> None:
    """Lifespan 注入 ChatEngine 实例。"""
    global _engine
    _engine = engine


def try_get_chat_engine():
    """返回 ChatEngine 实例；未初始化返回 None（调用方自行决定降级语义）。"""
    return _engine


def set_app_registry(registry: ToolRegistry) -> None:
    """Lifespan 注入 ToolRegistry 实例。"""
    global _registry
    _registry = registry


def try_get_app_registry() -> Optional[ToolRegistry]:
    """返回 ToolRegistry 实例；未初始化返回 None。"""
    return _registry
