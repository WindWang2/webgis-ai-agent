"""trigger 副作用的 Resource Governor 背压门（只调不改 governor）。

语义：
- 仅在 ``GIS_SPATIAL_EVENT_GOVERNOR_GATE`` 开启时生效（默认关）。
- 用 governor ``BackpressureManager`` 的 ``llm`` 通道为 trigger→mission 这类
  会引发后续 agent 工作的副作用准入（会话键 = org:watch 域，priority 按事件
  优先级映射）。
- defer/超时 → 返回 None（调用方把事件回 pending 带退避）——burst 下不丢事件、
  不无界排队（通道有容量 + FairScheduler aging）。
- fail-open：governor 内部异常/未装配 → 直通（与 governor 自身 fail-open 同纪律）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: 门最长等待（超过则本批放弃、事件回 pending——由事件面自己的退避重试）
_GATE_MAX_WAIT_S = 0.5


class GovernorGate:
    def __init__(self, *, max_wait_s: float = _GATE_MAX_WAIT_S) -> None:
        self._max_wait_s = max_wait_s

    async def acquire(
        self, *, org_id: str, priority: str = "normal"
    ) -> Optional[Any]:
        """占用一个 trigger 执行位。返回 ticket（调用方必须 release）或 None。"""
        from app.services.spatial_events import flags

        if not flags.governor_gate_enabled():
            return _Passthrough
        try:
            from app.services.governor import (
                ResourceClass,
                get_governor,
            )

            governor = get_governor()
            if governor is None or getattr(governor, "bp", None) is None:
                return _Passthrough
            prio = {"interactive": 0, "normal": 1, "batch": 2}.get(priority, 1)
            ticket = await governor.bp.acquire(
                session_id=f"spatial-events:{org_id}",
                resource_class=ResourceClass.LLM,
                priority=prio,
                weight=1.0,
                max_wait_s=self._max_wait_s,
            )
            return ticket
        except Exception as e:  # noqa: BLE001 — 背压门绝不阻断事件面正确性
            logger.debug("[spatial_events] governor gate bypass: %s", e)
            return _Passthrough

    async def release(self, ticket: Optional[Any]) -> None:
        if ticket is None or ticket is _Passthrough:
            return
        try:
            from app.services.governor import get_governor

            governor = get_governor()
            if governor is not None and getattr(governor, "bp", None) is not None:
                await governor.bp.release(ticket, actual_cost=1.0)
        except Exception as e:  # noqa: BLE001 — 归还路径自吞
            logger.debug("[spatial_events] governor gate release failed: %s", e)


class _PassthroughTicket:
    """flag 关闭/降级时的直通票据（release 为空操作）。"""

    __bool__ = lambda self: False  # noqa: E731


_Passthrough = _PassthroughTicket()
