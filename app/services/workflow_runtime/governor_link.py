"""Workflow 节点执行的 governor 接线（ADR-0214 D2）。

workflow_runtime 与 governor 的**唯一**连接面（对齐工具面
``governor/dispatch_adapter.py`` 的职责边界）：节点执行统一走
estimate → admit_and_reserve → 执行 → complete（记账 + 归还 + 校准）。

纪律（与工具面同源，逐条对应）：

- **fail-open**：governor 的任何异常都不阻断 workflow 执行 —— 资源治理
  是护栏不是闸门的主人；
- **kill-switch**：``GIS_WORKFLOW_GOVERNOR=0`` 整体直通（微升级路径）；
- **释放完备性**：success / fail / cancel / timeout / deadline-abandon
  （任务被 cancel → CancelledError）/ 未预期异常，全部路径恰好一次释放
  —— ``close`` 幂等认领（对齐 governor.complete 的 review P1 修复语义）；
- **拒绝诚实**：enforce 拒绝不执行节点，驱动层以 typed 错误码落 FAILED
  （预算类不可重试；排队超时/槽满类可重试退避）—— 绝不伪装成功；
- **校准只观测**：actual 回填进有界 CalibrationStore，生产零自修改。

backpressure/priority/retry token 全部复用 governor 既有机制，本模块
不新建任何闸/队列/账本。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

from app.services.governor.contract import (
    Dimension,
    ResourceDemand,
    ResourceUsage,
    RetryClass,
    Subsystem,
)
from app.services.workflow_runtime.estimate import (
    execution_priority_for_node,
    node_estimate_key,
)

logger = logging.getLogger(__name__)

#: workflow 面 kill-switch（与 GOVERNOR_TOOL_SURFACE 同惯例）
WORKFLOW_SURFACE_ENV = "GIS_WORKFLOW_GOVERNOR"

#: workflow 节点排队上界（秒）。必须显著小于 driver 默认 run deadline
#:（60s），否则排队会吃穿整个 run 的等待预算；超时 → typed
#: RESOURCE_EXHAUSTED（可重试退避），诚实而不是无限等。
DEFAULT_NODE_QUEUE_MAX_WAIT_S = 10.0

#: attempt 计数下限（demand.attempt 语义与 governor RetryBudget 对齐）
_MIN_ATTEMPT = 1


def workflow_surface_enabled() -> bool:
    """kill-switch：GIS_WORKFLOW_GOVERNOR=0 → workflow 面 governor 直通。"""
    return os.getenv(WORKFLOW_SURFACE_ENV, "1") != "0"


def node_queue_max_wait_s() -> float:
    try:
        return max(1.0, float(os.getenv(
            "GIS_WORKFLOW_QUEUE_MAX_WAIT_S",
            str(DEFAULT_NODE_QUEUE_MAX_WAIT_S))))
    except (TypeError, ValueError):
        return DEFAULT_NODE_QUEUE_MAX_WAIT_S


@dataclass
class NodeBudgetSession:
    """一次节点执行的预算会话（close 幂等；结束必须调用恰好一次）。

    ``rejected=True`` 时调用方不得执行节点：``error_code`` 是驱动层应落
    的 typed 失败码，``retryable`` 说明该失败是否应走重试退避。
    """

    session_id: str = ""
    node_key: str = ""
    attempt: int = 1
    estimate: Any = None
    rejected: bool = False
    error_code: str = ""
    retryable: bool = False
    reasons: List[str] = field(default_factory=list)
    #: fail-open / kill-switch 直通标记（close 退化为 no-op 观测）
    pass_through: bool = False
    _governor: Any = None
    _reservation: Any = None
    _ticket: Any = None
    _started: float = field(default_factory=time.monotonic)
    _closed: bool = False

    async def close(
        self, *, ok: bool, error_code: str = "", duration_ms: int = 0,
        rows_emitted: int = 0, cancelled: bool = False,
    ) -> None:
        """结束会话：记账 + 归还 + 校准（幂等；绝不抛）。"""
        if self._closed:
            return
        self._closed = True
        if self.pass_through or self._governor is None:
            return
        if self._reservation is None and self._ticket is None:
            return  # 未获得预留（observe 拒绝等）—— 无槽位可还
        status = "cancelled" if cancelled else ("completed" if ok else "failed")
        wall = max(0.0, time.monotonic() - self._started)
        usage = ResourceUsage(
            session_id=self.session_id,
            tool_name=self.node_key,
            subsystem=Subsystem.WORKFLOW,
            status=status,
            wall_time_s=wall,
            retries=max(0, self.attempt - 1),
        )
        if rows_emitted > 0:
            usage.dims[Dimension.FEATURE_COUNT] = float(rows_emitted)
        try:
            await self._governor.complete(
                self._reservation, self._ticket,
                usage=usage,
                actual={Dimension.WALL_TIME_S: wall},
                estimate=self.estimate,
            )
        except Exception:  # noqa: BLE001 — 记账故障绝不影响节点结果
            logger.exception(
                "[workflow-governor] complete accounting failed node=%s",
                self.node_key)
        if status == "completed":
            # 校准只观测（ADR-0214 D6）；异常绝不外泄
            try:
                from app.services.governor.calibration import (
                    get_calibration_store,
                )

                get_calibration_store().record_usage(
                    self.node_key, usage, self.estimate)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[workflow-governor] calibration record failed")


class NodeGovernorLink:
    """workflow 节点 → governor 管线包装（进程级单例经
    :func:`get_node_governor_link` 获取；测试可注入 governor）。"""

    def __init__(self, governor: Any = None):
        self._governor = governor

    def _resolve_governor(self) -> Any:
        if not workflow_surface_enabled():
            return None
        if self._governor is not None:
            return self._governor
        try:
            from app.services.governor.governor import get_governor

            return get_governor()
        except Exception:  # noqa: BLE001 — fail-open
            return None

    async def begin(
        self, *, node: Any, node_id: str, session_id: str,
        instance_id: str = "", attempt: int = 1, estimate: Any = None,
    ) -> NodeBudgetSession:
        """准入 + 预留（失败/拒绝不抛 —— 拒绝语义在 session.rejected）。"""
        sess = NodeBudgetSession(
            session_id=session_id or "",
            node_key=node_estimate_key(node) if node is not None else "",
            attempt=max(_MIN_ATTEMPT, int(attempt or 1)),
            estimate=estimate,
        )
        governor = self._resolve_governor()
        if governor is None:
            sess.pass_through = True
            return sess
        if estimate is None:
            # 无估算（桥自身 fail-open）→ 无从记账，诚实直通
            sess.pass_through = True
            return sess
        sess._governor = governor
        try:
            demand = ResourceDemand(
                session_id=sess.session_id,
                turn_id=str(instance_id or "")[:64],
                subsystem=Subsystem.WORKFLOW,
                tool_name=sess.node_key,
                estimate=estimate,
                priority=int(execution_priority_for_node(node)),
                max_wait_s=node_queue_max_wait_s(),
                retry_class=(RetryClass.WORKFLOW if sess.attempt > 1
                             else None),
                attempt=sess.attempt,
            )
            decision, reservation, ticket = await governor.admit_and_reserve(
                demand)
        except Exception:  # noqa: BLE001 — 双保险 fail-open（对齐工具面）
            logger.exception(
                "[workflow-governor] admit failed; fail-open node=%s",
                sess.node_key)
            sess.pass_through = True
            sess._governor = None
            return sess

        mode = getattr(getattr(governor, "config", None), "mode", None)
        enforce = getattr(mode, "value", "") == "enforce"
        if not decision.allowed and enforce:
            sess.rejected = True
            sess.reasons = list(decision.reasons)
            sess.error_code, sess.retryable = _classify_rejection(
                decision.reasons)
            return sess
        # observe 模式下拒绝也照常持有 reservation/ticket（回滚语义：
        # 必须执行且必须 complete，否则泄漏槽位 —— 对齐工具面 P0 修复）
        if reservation is None and ticket is None:
            if not decision.allowed:
                # enforce 关闭但 facade 未建预留（理论上不发生）——
                # 诚实直通，绝不假装持有
                sess.pass_through = True
                return sess
        if reservation is not None and sess.attempt > 1:
            # 重试实扣（retry_allowed 已由 facade 咨询过；记账故障不阻断）
            try:
                governor.retries.charge(sess.session_id, RetryClass.WORKFLOW)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[workflow-governor] retry charge failed node=%s",
                    sess.node_key)
        sess._reservation = reservation
        sess._ticket = ticket
        return sess


def _classify_rejection(reasons: List[str]) -> "tuple[str, bool]":
    """拒绝原因 → (typed error_code, retryable)。

    - 排队超时 / 容量饱和 → ``RESOURCE_EXHAUSTED``（瞬时，可退避重试）；
    - 会话取消 → ``CANCELLED``（取消不是失败）；
    - 其余（预算硬限 / 内存压力 / 重试预算耗尽）→
      ``RESOURCE_BUDGET_EXCEEDED``（确定性，重试只会复现）。
    """
    joined = ";".join(reasons or "")
    if "queue_timeout" in joined or "capacity" in joined:
        return "RESOURCE_EXHAUSTED", True
    if "session_cancelled" in joined:
        return "CANCELLED", False
    return "RESOURCE_BUDGET_EXCEEDED", False


_PROCESS_LINK: Optional[NodeGovernorLink] = None


def get_node_governor_link() -> NodeGovernorLink:
    """进程级默认 link（governor 单例延迟解析；构造零成本）。"""
    global _PROCESS_LINK
    if _PROCESS_LINK is None:
        _PROCESS_LINK = NodeGovernorLink()
    return _PROCESS_LINK


def reset_node_governor_link_for_tests(link: Optional[NodeGovernorLink] = None
                                       ) -> NodeGovernorLink:
    global _PROCESS_LINK
    _PROCESS_LINK = link if link is not None else NodeGovernorLink()
    return _PROCESS_LINK


__all__ = [
    "WORKFLOW_SURFACE_ENV",
    "DEFAULT_NODE_QUEUE_MAX_WAIT_S",
    "NodeBudgetSession",
    "NodeGovernorLink",
    "get_node_governor_link",
    "reset_node_governor_link_for_tests",
    "workflow_surface_enabled",
    "node_queue_max_wait_s",
]
