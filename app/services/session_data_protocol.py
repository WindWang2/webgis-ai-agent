"""SessionStore 接口与数据结构约定。

ADR-0018（保留 14 个细粒度方法）与 ADR-0018 Trigger 2 的落地（删除未兑现的
深层方法层）：本 Protocol 曾声明 3 个「深意图」方法（load_context /
commit_dispatch / get_ref_data），但调查证实它们全部零生产调用方或仅剩薄包装：

- `load_context` —— 0 外部调用方（chat_engine 上的同名调用走 AsyncHistoryService，
  另一协议）；删除。
- `commit_dispatch` —— 1 调用方（tool_dispatch_service._record_event），体只是
  重建 payload 再 append_event("tool_executed")；已 inline 到调用点，删除。
- `get_ref_data` —— 0 调用方（仅测试）；体是 `return await self.get(...)` 的改名；
  删除。

「4 个深方法」的说法（含 update_map_state）从未实现——update_map_state 仅出现在
早期 docstring，从未进入 Protocol 类体，是 vaporware。

剩下的 14 个细粒度方法是真正承重的表面（ADR-0018 详述其各自的关注点：map-state、
ref/alias、event-log、lifecycle），不可强行收口。SessionContext 数据类保留——
get_session_metadata 返回同形态数据，未来若真出现一站式读取需求可重建 load_context。
"""
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class SessionContext:
    """会话聚合上下文数据类。

    保留供未来重建 load_context 使用；当前 get_session_metadata 返回同形态数据。
    """

    session_id: str
    map_state: dict[str, Any] = field(default_factory=dict)
    layers: list[dict[str, Any]] = field(default_factory=list)
    event_log: list[dict[str, Any]] = field(default_factory=list)
    started_at: Optional[str] = None
    refs: dict[str, str] = field(default_factory=dict)


class SessionStoreProtocol(Protocol):
    """SessionStore 细粒度接口协议（ADR-0018）。

    这 14 个方法各自承载 ADR-0018 §2 列举的关注点（frontend perception、ref/alias
    解析、event log、lifecycle），是真正承重的表面。曾声明的 3 个「深意图」方法
    （load_context / commit_dispatch / get_ref_data）已删除——见模块 docstring。
    """

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
