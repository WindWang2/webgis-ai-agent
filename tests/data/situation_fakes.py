"""GIS Situation 测试假件（方向 2）。

FakeSituationStore / FakeMapspecStore 实现编译器实际消费的最小接口面
（get_map_state / list_refs / get_event_log / get_ref_descriptor /
get_state_field / set_map_state / get_mapspec），全部内存态 —— 与
MemorySessionStore 语义一致（无 Redis 环境下的测试默认后端）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class FakeSituationStore:
    def __init__(
        self,
        map_state: Optional[Dict[str, Any]] = None,
        refs: Optional[Dict[str, str]] = None,
        event_log: Optional[List[Dict[str, Any]]] = None,
        descriptors: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self._map_state: Dict[str, Any] = dict(map_state or {})
        self._refs: Dict[str, str] = dict(refs or {})
        self._event_log: List[Dict[str, Any]] = list(event_log or [])
        self._descriptors: Dict[str, Dict[str, Any]] = dict(descriptors or {})
        self.map_state_reads = 0

    async def get_map_state(self, session_id: str) -> Dict[str, Any]:
        self.map_state_reads += 1
        return dict(self._map_state)

    async def list_refs(self, session_id: str) -> Dict[str, str]:
        return dict(self._refs)

    async def get_event_log(self, session_id: str) -> List[Dict[str, Any]]:
        return list(self._event_log)

    async def get_ref_descriptor(
        self, session_id: str, ref_id: str
    ) -> Optional[Dict[str, Any]]:
        desc = self._descriptors.get(ref_id)
        return dict(desc) if desc is not None else None

    async def get_state_field(self, session_id: str, field: str) -> Any:
        return self._map_state.get(field)

    async def set_map_state(
        self, session_id: str, key: str, value: Any, seq: Optional[int] = None
    ) -> bool:
        self._map_state[key] = value
        return True


class FailingStore(FakeSituationStore):
    """单源故障注入：指定方法抛异常（partial source unavailable 降级测试）。"""

    def __init__(self, fail: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._fail = fail

    async def _boom(self, *args, **kwargs):
        raise RuntimeError(f"source {self._fail} down")

    async def get_map_state(self, session_id):
        if self._fail == "map_state":
            await self._boom()
        self.map_state_reads += 1
        return dict(self._map_state)

    async def list_refs(self, session_id):
        if self._fail == "refs":
            await self._boom()
        return dict(self._refs)

    async def get_event_log(self, session_id):
        if self._fail == "event_log":
            await self._boom()
        return list(self._event_log)

    async def get_state_field(self, session_id, field):
        return self._map_state.get(field)

    async def set_map_state(self, session_id, key, value, seq=None):
        if self._fail == "set_map_state":
            await self._boom()
        self._map_state[key] = value
        return True


class FakeMapspecStore:
    def __init__(self, spec: Optional[Dict[str, Any]] = None) -> None:
        self.spec = spec or {}

    async def get_mapspec(self, session_id: str) -> Dict[str, Any]:
        return dict(self.spec)
