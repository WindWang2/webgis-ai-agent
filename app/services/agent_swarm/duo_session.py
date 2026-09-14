"""CartoAuditDuoSession —— 制图专家 × 审计裁判的对抗博弈闭环（ADR-0189 D4）。

有界对抗循环：

```
R0  cartographer.compose → auditor.audit → pass 即收敛
Ri  cartographer.revise（仅按审计红线 suggested_fix 的 AUTO_SAFE 修复）
    → auditor.audit；修正轮上限 MAX_REPAIR_ROUNDS=2
超限 → failed_escalated（诚实失败，全程 receipts + 审计单上交）
```

挂接总控（ADR-0187）而不改 ``SwarmOrchestrator.run_swarm`` 本体：

- 角色注册即路由：``cartography_specialist`` / ``audit_judge`` 入
  ``SUBAGENT_ROLES`` 后，decomposer 既有相位经 SpecialistDispatcher
  自动获得专属角色档（LLM 委派路径零改动）；
- ``InProcessSpecialistRuntime`` 实现 ``SpecialistRuntime`` Protocol，
  按 capability 在进程内直跑确定性专家，产出经归一的
  ``SubagentReceipt``；
- 会话每轮经 ``SwarmConcurrencyGovernor`` 取放并发额度（集群 ≤3 的
  同一纪律），可注入 ``SwarmAggregator`` 聚合 manifest。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.agent_swarm.contracts import (
    MapSpecDeliveryRef,
    DeliveryAuditReport,
)
from app.services.agent_swarm.delegation_contracts import (
    SwarmReceiptStatus,
    SwarmSpecialistRole,
    SubagentReceipt,
)
from app.services.agent_swarm.specialists.auditor import CriticAuditorAgent
from app.services.agent_swarm.specialists.cartographer import CartographerAgent
from app.services.agent_swarm.specialists.ledger import ArtifactLedger

logger = logging.getLogger(__name__)


@dataclass
class DuoRound:
    """一轮对抗的完整证据（交付券 + 审计单）。"""

    round_index: int
    delivery: MapSpecDeliveryRef
    report: DeliveryAuditReport


@dataclass
class DuoSessionResult:
    """会话终态：收敛 / 诚实升级，全程证据可回放。"""

    status: str  # converged | failed_escalated
    final_delivery: MapSpecDeliveryRef
    final_report: DeliveryAuditReport
    receipts: List[SubagentReceipt] = field(default_factory=list)
    rounds_used: int = 0
    rounds: List[DuoRound] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "rounds_used": self.rounds_used,
            "final_verdict": self.final_report.verdict,
            "final_ref_id": self.final_delivery.ref_id,
            "final_fingerprint": self.final_delivery.mapspec_fingerprint,
            "rounds": [
                {
                    "round": r.round_index,
                    "ref_id": r.delivery.ref_id,
                    "verdict": r.report.verdict,
                    "vetoes": [v.get("veto_id") for v in r.report.vetoes],
                }
                for r in self.rounds
            ],
            "receipts": [r.model_dump() for r in self.receipts],
        }


class CartoAuditDuoSession:
    """两名专家的对抗闭环会话（组合总控原语，不侵入 orchestrator）。"""

    MAX_REPAIR_ROUNDS: int = 2

    def __init__(
        self,
        *,
        cartographer: CartographerAgent,
        auditor: CriticAuditorAgent,
        ledger: Optional[ArtifactLedger] = None,
        governor: Optional[Any] = None,
        aggregator: Optional[Any] = None,
        clock: Any = time.monotonic,
        max_repair_rounds: Optional[int] = None,
        session_id: str = "duo-session",
    ) -> None:
        self._cartographer = cartographer
        self._auditor = auditor
        # 账本防呆：未显式注入时回退用制图专家的账本 —— compose 与 audit
        # 必须共享同一取货位，否则审计永远 V0 不可达。
        self._ledger = ledger or getattr(cartographer, "_ledger", None)
        self._governor = governor
        self._aggregator = aggregator
        self._clock = clock
        self._max_rounds = (
            self.MAX_REPAIR_ROUNDS if max_repair_rounds is None
            else max(0, int(max_repair_rounds))
        )
        self._session_id = session_id
        self._pending_receipts: List[SubagentReceipt] = []
        self.result: Optional[DuoSessionResult] = None

    # ── 主循环 ──────────────────────────────────────────────

    async def run(
        self,
        request: Dict[str, Any],
        *,
        chapter: Optional[Dict[str, Any]] = None,
        map_product: Optional[Dict[str, Any]] = None,
        user_goal: str = "",
        source_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
        run_id: str = "duo-run",
    ) -> DuoSessionResult:
        run_label = str(request.get("title") or "compose")
        self._pending_receipts = []
        delivery = await self._stage(
            run_id, 0, "compose", SwarmSpecialistRole.CARTOGRAPHY_SPECIALIST,
            lambda: self._cartographer.compose(request),
            run_label=run_label,
        )
        report = await self._audit_stage(
            run_id, delivery, chapter, map_product, user_goal,
            source_profiles, round_index=0,
        )
        rounds = [DuoRound(0, delivery, report)]
        rounds_used = 0

        while report.verdict != "pass" and rounds_used < self._max_rounds:
            rounds_used += 1
            delivery = await self._stage(
                run_id, rounds_used, "revise",
                SwarmSpecialistRole.CARTOGRAPHY_SPECIALIST,
                lambda: self._cartographer.revise(delivery, report),
                run_label=run_label,
            )
            report = await self._audit_stage(
                run_id, delivery, chapter, map_product, user_goal,
                source_profiles, round_index=rounds_used,
            )
            rounds.append(DuoRound(rounds_used, delivery, report))

        status = "converged" if report.verdict == "pass" else "failed_escalated"
        result = DuoSessionResult(
            status=status,
            final_delivery=delivery,
            final_report=report,
            receipts=list(self._pending_receipts),
            rounds_used=rounds_used,
            rounds=rounds,
        )
        self.result = result
        if self._aggregator is not None and result.receipts:
            try:
                manifest = await self._aggregator.collect(
                    run_id=run_id,
                    session_id=self._session_id,
                    receipts=result.receipts,
                    tasks=[],
                )
                await self._aggregator.merge(manifest)
            except Exception as exc:  # noqa: BLE001 — 聚合 fail-open（同 SwarmAggregator）
                logger.warning("[duo] aggregator merge failed: %s", exc)
        logger.info(
            "[duo] run=%s status=%s rounds=%s verdict=%s",
            run_id, status, rounds_used, report.verdict,
        )
        return result

    # ── 阶段执行（governor 纪律 + 券产出）───────────────────

    async def _stage(
        self,
        run_id: str,
        round_index: int,
        action: str,
        role: SwarmSpecialistRole,
        fn: Any,
        *,
        run_label: str = "",
    ) -> MapSpecDeliveryRef:
        assignment_id = f"{run_id}:{action}:{round_index}"
        started = self._clock()
        if self._governor is not None:
            await self._governor.acquire(assignment_id, f"swarm.cartography.{action}")
        try:
            value = fn()
        finally:
            if self._governor is not None:
                self._governor.release(assignment_id)
        receipt = SubagentReceipt(
            assignment_id=assignment_id,
            task_id=f"swarm.cartography.{action}",
            role=role,
            status=SwarmReceiptStatus.SUCCEEDED,
            produced_refs=[value.ref_id] if value.ref_id else [],
            summary=(
                f"{action} {run_label}: {value.summary}"
            )[:400],
            wall_time_s=self._clock() - started,
            finished_at=self._clock(),
        )
        self._pending_receipts.append(receipt)
        return value

    async def _audit_stage(
        self,
        run_id: str,
        delivery: MapSpecDeliveryRef,
        chapter: Optional[Dict[str, Any]],
        map_product: Optional[Dict[str, Any]],
        user_goal: str,
        source_profiles: Optional[Dict[str, Dict[str, Any]]],
        *,
        round_index: int,
    ) -> DeliveryAuditReport:
        assignment_id = f"{run_id}:audit:{round_index}"
        started = self._clock()
        if self._governor is not None:
            await self._governor.acquire(assignment_id, "swarm.audit.judge")
        try:
            report = self._auditor.audit(
                delivery,
                chapter=chapter,
                ledger=self._ledger,
                source_profiles=source_profiles,
                map_product=map_product,
                user_goal=user_goal,
                round_index=round_index,
            )
        finally:
            if self._governor is not None:
                self._governor.release(assignment_id)
        receipt = SubagentReceipt(
            assignment_id=assignment_id,
            task_id="swarm.audit.judge",
            role=SwarmSpecialistRole.AUDIT_JUDGE,
            status=SwarmReceiptStatus.SUCCEEDED,
            produced_refs=[],
            summary=(
                f"audit r{round_index}: verdict={report.verdict}, "
                f"vetoes={len(report.vetoes)}"
            )[:400],
            wall_time_s=self._clock() - started,
            finished_at=self._clock(),
        )
        self._pending_receipts.append(receipt)
        return report


class InProcessSpecialistRuntime:
    """ADR-0187 ``SpecialistRuntime`` 的进程内确定性适配器。

    按 assignment capability 路由：``swarm.cartography.compose`` →
    compose、``swarm.cartography.revise`` → revise、
    ``swarm.audit.judge`` → audit。请求面（GeoJSON）由调用方经
    ``register_request`` 注入（Zero Big Data in Context：载荷不过
    assignment 上下文）。LLM 委派路径仍走 ``SubagentDispatcherRuntime``。
    """

    def __init__(
        self,
        *,
        cartographer: CartographerAgent,
        auditor: CriticAuditorAgent,
        ledger: ArtifactLedger,
        audit_kwargs: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._cartographer = cartographer
        self._auditor = auditor
        self._ledger = ledger
        self._audit_kwargs = dict(audit_kwargs or {})
        self._requests: Dict[str, Dict[str, Any]] = {}
        self._last_delivery: Optional[MapSpecDeliveryRef] = None
        self._last_report: Optional[DeliveryAuditReport] = None

    def register_request(self, capability: str, request: Dict[str, Any]) -> None:
        """注入 capability → compose 请求的映射（进程内取货位）。"""
        self._requests[capability] = request

    async def execute(
        self,
        assignment: Any,
        *,
        on_heartbeat: Any = None,
    ) -> SubagentReceipt:
        task = assignment.task
        capability = str(task.capability)
        started = time.monotonic()
        refs: List[str] = []
        summary = ""
        try:
            if capability == "swarm.cartography.compose":
                request = self._requests.get(task.task_id) or self._requests.get(
                    capability,
                )
                if request is None:
                    raise ValueError(
                        f"capability {capability!r} 未注册 compose 请求"
                    )
                delivery = self._cartographer.compose(request)
                self._last_delivery = delivery
                refs = [delivery.ref_id] if delivery.ref_id else []
                summary = delivery.summary
            elif capability == "swarm.cartography.revise":
                if self._last_delivery is None:
                    raise ValueError("revise 前必须先 compose（无在途交付券）")
                delivery = self._cartographer.revise(
                    self._last_delivery, self._last_report,
                )
                self._last_delivery = delivery
                refs = [delivery.ref_id] if delivery.ref_id else []
                summary = delivery.summary
            elif capability == "swarm.audit.judge":
                if self._last_delivery is None:
                    raise ValueError("audit 前必须先 compose（无在途交付券）")
                report = self._auditor.audit(
                    self._last_delivery,
                    ledger=self._ledger,
                    **self._audit_kwargs,
                )
                self._last_report = report
                ref_id = self._ledger.allocate_ref("ref:audit")
                self._ledger.put(ref_id, report.model_dump())
                refs = [ref_id]
                summary = (
                    f"verdict={report.verdict}, vetoes={len(report.vetoes)}"
                )
            else:
                raise ValueError(
                    f"InProcessSpecialistRuntime 不认识 capability {capability!r}"
                )
        except Exception as exc:  # noqa: BLE001 — 异常折算 FAILED 券（诚实失败）
            return SubagentReceipt(
                assignment_id=assignment.assignment_id,
                task_id=task.task_id,
                role=task.role,
                status=SwarmReceiptStatus.FAILED,
                produced_refs=[],
                summary="",
                error=str(exc)[:300],
                error_code="non_retryable",
                wall_time_s=time.monotonic() - started,
                finished_at=time.monotonic(),
            )
        return SubagentReceipt(
            assignment_id=assignment.assignment_id,
            task_id=task.task_id,
            role=task.role,
            status=SwarmReceiptStatus.SUCCEEDED,
            produced_refs=refs,
            summary=summary[:400],
            wall_time_s=time.monotonic() - started,
            finished_at=time.monotonic(),
        )


__all__ = [
    "CartoAuditDuoSession",
    "DuoRound",
    "DuoSessionResult",
    "InProcessSpecialistRuntime",
]
