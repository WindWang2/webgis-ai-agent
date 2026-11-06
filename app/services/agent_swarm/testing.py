"""Explicit offline test support; production gateways never instantiate these runners."""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Any, Callable, Optional
from app.services.agent_swarm.delegation import DelegationLease, DelegationOutcome, DelegationStatus
logger = logging.getLogger(__name__)


class FakeSubagentRunner:
    """确定性离线 runner（零 LLM/DB/网络）—— 协议测试专用假件。

    script 按 run 调用次序消费；条目语义：
    - ``"succeed"``            → succeeded + ref:out-<n>；
    - ``{"refs": [...], "summary": ...}`` → 自定义成功产出；
    - ``Exception`` 实例       → 原样抛出（child crash 面）；
    - ``"hang"``               → 永久等待（由测试/取消打破）；
    - ``"sleep:<s>"``          → 延迟后成功（timeout 面）；
    - ``"no_summary"`` / ``"no_refs"`` / ``"alien_cap"`` → 验证面；
    - ``("park", <delay_s>)``  → 立即被取消 + 独立任务延迟投递迟到结果
      （``late_deliver`` 回调，测试接到 ledger.offer 验证 fencing）。
    """

    def __init__(
        self,
        script: Optional[list[Any]] = None,
        *,
        default: Any = "succeed",
        late_deliver: Optional[Callable[[DelegationLease, DelegationOutcome], None]] = None,
    ) -> None:
        self.script = list(script or [])
        self.default = default
        self.calls: list[str] = []
        self.late_deliver = late_deliver
        self._late_tasks: set[asyncio.Task] = set()
        self._n = 0

    def _next(self) -> Any:
        return self.script.pop(0) if self.script else self.default

    async def run(self, lease: DelegationLease) -> DelegationOutcome:
        self._n += 1
        self.calls.append(lease.delegation_id)
        behavior = self._next()

        def _out(**kw: Any) -> DelegationOutcome:
            base = dict(
                delegation_id=lease.delegation_id,
                generation=lease.generation,
                lease_id=lease.lease_id,
                status=DelegationStatus.SUCCEEDED,
                summary=f"fake done {lease.delegation_id}",
                produced_refs=[f"ref:out-{self._n}"],
                session_id=lease.session_id,
                finished_at=time.time(),
            )
            base.update(kw)
            return DelegationOutcome(**base)

        if isinstance(behavior, BaseException):
            raise behavior
        if behavior == "hang":
            await asyncio.Event().wait()
        if isinstance(behavior, str) and behavior.startswith("sleep:"):
            await asyncio.sleep(float(behavior.split(":", 1)[1]))
            return _out()
        if isinstance(behavior, tuple) and behavior and behavior[0] == "park":
            delay = float(behavior[1])

            async def _late() -> None:
                await asyncio.sleep(delay)
                if self.late_deliver is not None:
                    self.late_deliver(lease, _out(summary="late child result"))

            task = asyncio.create_task(_late())
            self._late_tasks.add(task)
            def _done(completed: asyncio.Task) -> None:
                self._late_tasks.discard(completed)
                if not completed.cancelled():
                    error = completed.exception()
                    if error is not None:
                        logger.warning("fake late delivery failed", exc_info=error)
            task.add_done_callback(_done)
            await asyncio.Event().wait()  # 本体服从取消
        if isinstance(behavior, dict):
            return _out(**behavior)
        if behavior == "no_summary":
            return _out(summary="")
        if behavior == "no_refs":
            return _out(produced_refs=[])
        if behavior == "alien_cap":
            return _out(capabilities_used=["cap.not.granted"])
        return _out()
