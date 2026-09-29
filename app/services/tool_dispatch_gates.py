"""工具调度并发原语与 V3 action_id 铸造（自 tool_dispatch_service 抽出）。

god-modules 棘轮（ISSUE-044）：tool_dispatch_service.py 合并 W12/H 系后
超 2000 行红线，按内聚缝抽出 —— 本模块只承载调度面原语：
- ``_MultiSlotAcquire``：wave 信号量的逐槽获取（RUN-09 语义）；
- ``_SessionWaveGate``：会话级波次公平门（#1522）；
- ``_decode_data_url_png``：data URL PNG 解码（受限预览通道）；
- ``_mint_map_action_id`` / ``_cap_requested_snapshot``：V3 地图动作
  action_id 铸造与快照请求封顶。

原模块经 re-export 保持全部既有 import 面（tests 直接从
tool_dispatch_service 引用这些名字）。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ─── V3 地图动作 action_id 铸造（Harness–Map Interaction Closed Loop）─────
# 动作 id 形如 ``ma-<uuid4hex[:16]>``，写入工具结果的 command/commands[] dict，
# 随 SSE step_result 直达前端；前端 ack 时原样回传，供后端按 action_id 精确匹配。
MAP_ACTION_ID_PREFIX = "ma-"
REQUESTED_SNAPSHOT_MAX_BYTES = 2048


def _mint_map_action_id() -> str:
    """铸一个唯一的地图动作 id。"""
    return f"{MAP_ACTION_ID_PREFIX}{uuid.uuid4().hex[:16]}"


def _cap_requested_snapshot(
    params: Any, max_bytes: int = REQUESTED_SNAPSHOT_MAX_BYTES
) -> Dict[str, Any]:
    """请求目标参数快照（~2KB 上限）：按序保留键，直到序列化超限为止。

    供 harness 记录 issued 证据（requested 侧），避免把超大 params 全量落盘。
    """
    if not isinstance(params, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in params.items():
        probe = {**out, k: v}
        try:
            size = len(json.dumps(probe, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            continue
        if size > max_bytes:
            break
        out[k] = v
    return out


def _decode_data_url_png(image: str) -> Optional[bytes]:
    """Decode a data-URL PNG. Non-data URLs return None (caller uses the ref)."""
    if not image.startswith("data:image"):
        return None
    _, _, encoded = image.partition(",")
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded, validate=False)
    except Exception:
        return None


class _MultiSlotAcquire:
    """#1062: 在一个 asyncio.Semaphore 上按槽位数获取/释放的 async 上下文。

    asyncio.Semaphore 没有参数化 acquire(n)；heavy 工具（cost=heavy）占
    2 槽、其余 1 槽，逐槽获取语义等价。异常路径由 __aexit__ 统一释放
    已获取的槽，保证不泄漏。
    """

    def __init__(self, sem: asyncio.Semaphore, slots: int = 1):
        self._sem = sem
        self._slots = max(1, int(slots))
        self._held = 0

    def _release_held(self) -> None:
        for _ in range(self._held):
            self._sem.release()
        self._held = 0

    async def __aenter__(self) -> "_MultiSlotAcquire":
        try:
            for _ in range(self._slots):
                await self._sem.acquire()
                self._held += 1
        except BaseException:
            # RUN-09: ``async with`` never calls ``__aexit__`` when
            # ``__aenter__`` raises — a cancel while waiting for the 2nd slot
            # must return the already-held slots or the wave semaphore leaks
            # one permit per cancellation until tool dispatch stalls.
            self._release_held()
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._release_held()


class _SessionWaveGate:
    """ADR-0100：进程级 wave 信号量之上的**按会话公平门**。

    问题（/goal §11 fairness）：wave 信号量是进程级 FIFO —— 一个工具密集
    会话一轮 12-20 个并发调用可以把全部槽位占满，跨会话（其它用户）的
    单个轻工具排在其后饿等。会话内本就有天然的顺序需求（同 turn 工具
    相互观察 MapSpec），跨会话才是公平性所在。

    语义：每会话最多 ``cap`` 个并发工具进 wave；会话达到上限时**该会话
    等待**（不持任何全局槽 —— 先过会话门再抢全局信号量，无死锁环）。
    等待者经 Condition 唤醒，其它会话不受影响。

    计数器归零即从表中删除 —— 会话表天然有界（活跃会话数）。
    """

    def __init__(self, cap: int = 2):
        self._cap = max(1, int(cap))
        self._held: dict = {}
        self._cond: Optional[asyncio.Condition] = None

    def _condition(self) -> asyncio.Condition:
        # 惰性创建：绑定当前事件循环（多 loop 测试场景安全）。
        if self._cond is None:
            self._cond = asyncio.Condition()
        return self._cond

    async def acquire(self, session_id: str) -> None:
        cond = self._condition()
        async with cond:
            await cond.wait_for(
                lambda: self._held.get(session_id, 0) < self._cap
            )
            self._held[session_id] = self._held.get(session_id, 0) + 1

    async def release(self, session_id: str) -> None:
        cond = self._condition()
        async with cond:
            remaining = self._held.get(session_id, 0) - 1
            if remaining <= 0:
                self._held.pop(session_id, None)
            else:
                self._held[session_id] = remaining
            cond.notify_all()

    @property
    def held_sessions(self) -> int:
        return len(self._held)
