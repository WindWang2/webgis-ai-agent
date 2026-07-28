"""SessionStore 提深接口与数据结构约定。

收拢 14 个浅层 API 为 4 个主意图接口：
- `load_context`: 一站式获取会话状态、图层与历史
- `commit_dispatch`: 原子化提交工具调度结果与状态
- `update_map_state`: 原子化更新图层与全局 Viewport 状态
- `get_ref_data`: 面向 Fetch-on-Demand 的游标数据读取
"""
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class SessionContext:
    """会话聚合上下文数据类。"""

    session_id: str
    map_state: dict[str, Any] = field(default_factory=dict)
    layers: list[dict[str, Any]] = field(default_factory=list)
    event_log: list[dict[str, Any]] = field(default_factory=list)
    started_at: Optional[str] = None
    refs: dict[str, str] = field(default_factory=dict)


class SessionStoreProtocol(Protocol):
    """深层 SessionStore 统一接口协议。"""

    async def load_context(self, session_id: str) -> SessionContext: ...

    async def commit_dispatch(
        self,
        session_id: str,
        tool_name: str,
        geojson_ref: Optional[str] = None,
        event_payload: Optional[dict] = None,
        result: Optional[Any] = None,
    ) -> None: ...

    async def get_ref_data(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]: ...

    # 兼容过渡 API
    async def get(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]: ...
    async def store(self, session_id: str, data: Any, prefix: str = "data") -> str: ...
    async def set_alias(self, session_id: str, ref_id: str, alias: str) -> None: ...
    async def list_refs(self, session_id: str) -> dict[str, str]: ...
    async def resolve_alias(self, session_id: str, ref_or_alias: str) -> str: ...
    async def get_map_state(self, session_id: str) -> dict[str, Any]: ...
    async def set_map_state(self, session_id: str, key: str, value: Any) -> None: ...
    async def update_layer_in_state(self, session_id: str, layer_id: str, updates: dict) -> None: ...
    async def remove_layer_from_state(self, session_id: str, layer_id: str) -> None: ...
    async def get_event_log(self, session_id: str) -> list[dict]: ...
    async def append_event(self, session_id: str, event: str, data: dict) -> None: ...
    async def get_started_at(self, session_id: str) -> Optional[str]: ...
    async def get_session_metadata(self, session_id: str) -> dict[str, Any]: ...
    async def clear_session(self, session_id: str) -> None: ...


# 保留旧 Protocol 名以兼容现有 import
SessionDataProtocol = SessionStoreProtocol
