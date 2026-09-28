"""CartoAuditDuoSession 对抗闭环与 InProcessSpecialistRuntime 路由（ADR-0189 D4）。

有界对抗循环：compose → audit → pass 即收敛；fail 按审计红线 revise
（≤ MAX_REPAIR_ROUNDS 轮）→ 超限诚实升级（failed_escalated，全程
receipts + 审计单上交）。会话每轮经 Governor 取放并发额度；聚合
fail-open（同 SwarmAggregator 纪律）。

receipt status 语义注记：``status`` 是**工件交付态**（该阶段的
compose/revise/audit 调用是否成功产出工件），不是审计裁决态 ——
审计判 fail 时审计阶段 receipt 仍为 SUCCEEDED，裁决记录在
DeliveryAuditReport.verdict 与 receipt.summary 里。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.services.agent_swarm.contracts import (
    DeliveryAuditReport,
)
from app.services.agent_swarm.delegation_contracts import (
    SwarmReceiptStatus,
    SwarmSpecialistRole,
    SwarmTaskDescriptor,
)
from app.services.agent_swarm.duo_session import (
    CartoAuditDuoSession,
    DuoRound,
    InProcessSpecialistRuntime,
)
from app.services.agent_swarm.specialists.auditor import CriticAuditorAgent
from app.services.agent_swarm.specialists.cartographer import CartographerAgent
from app.services.agent_swarm.specialists.ledger import ArtifactLedger


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeGovernor:
    """记录 acquire/release 配对的并发额度假件。"""

    def __init__(self) -> None:
        self.acquired: List[str] = []
        self.released: List[str] = []

    async def acquire(self, assignment_id: str, task_id: str) -> None:
        self.acquired.append(assignment_id)

    def release(self, assignment_id: str) -> None:
        self.released.append(assignment_id)

    def snapshot(self) -> dict:
        return {"active_count": len(self.acquired) - len(self.released)}


class RecordingAggregator:
    """记录 collect/merge 调用的聚合器假件。"""

    def __init__(self, *, boom: bool = False) -> None:
        self.boom = boom
        self.collects: List[Dict[str, Any]] = []
        self.merges: List[Any] = []

    async def collect(self, **kwargs: Any):
        if self.boom:
            raise RuntimeError("collect down")
        self.collects.append(kwargs)
        from app.services.agent_swarm.delegation_contracts import SwarmAssetManifest

        return SwarmAssetManifest(
            run_id=kwargs["run_id"], session_id=kwargs["session_id"]
        )

    async def merge(self, manifest: Any):
        if self.boom:
            raise RuntimeError("merge down")
        self.merges.append(manifest)
        return "ref:swarm-1"


class _PassingAuditor(CriticAuditorAgent):
    """恒判 pass 的审计替身（首轮收敛面）。"""

    def audit(self, delivery, **kwargs):
        return DeliveryAuditReport(
            audited_ref_id=str(getattr(delivery, "ref_id", "") or ""),
            verdict="pass",
            review_status="passed",
        )


class _FailingAuditor(CriticAuditorAgent):
    """恒判 fail 的审计替身（revise 回路 / 诚实升级面）。"""

    def audit(self, delivery, *, round_index: int = 0, **kwargs):
        return DeliveryAuditReport(
            audited_ref_id=str(getattr(delivery, "ref_id", "") or ""),
            verdict="fail",
            vetoes=[{"veto_id": "V1", "rule_id": "legend.unclosed"}],
            review_status="failed",
            round_index=round_index,
        )


class _ExplodingAuditor(CriticAuditorAgent):
    """audit 抛异常的替身（会话级 fail-open 面）。"""

    def audit(self, delivery, **kwargs):
        raise RuntimeError("auditor crashed")


def _square(lng: float, lat: float) -> List[List[float]]:
    d = 0.02
    return [
        [lng, lat], [lng + d, lat], [lng + d, lat + d], [lng, lat + d], [lng, lat],
    ]


def _geojson(values: List[float]) -> Dict[str, Any]:
    features = []
    for i, v in enumerate(values):
        features.append({
            "type": "Feature",
            "properties": {"schools": v, "district": f"d{i}"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [_square(104.0 + i * 0.05, 30.5 + i * 0.05)],
            },
        })
    return {"type": "FeatureCollection", "features": features}


_REQUEST: Dict[str, Any] = {
    "geojson": _geojson([10, 14, 18, 25, 30, 40, 52, 68, 90, 120]),
    "field": "schools",
    "title": "成都各区学校数量分布",
    "purpose": "screen_16_9",
    "source_id": "districts",
}


def _session(
    auditor: Any,
    *,
    governor: Any = None,
    aggregator: Any = None,
    max_repair_rounds: Optional[int] = None,
    session_id: str = "duo-s1",
) -> CartoAuditDuoSession:
    ledger = ArtifactLedger()
    cartographer = CartographerAgent(ledger=ledger)
    return CartoAuditDuoSession(
        cartographer=cartographer,
        auditor=auditor,
        ledger=ledger,
        governor=governor,
        aggregator=aggregator,
        clock=FakeClock(),
        max_repair_rounds=max_repair_rounds,
        session_id=session_id,
    )


class TestConvergence:
    """pass 即收敛（R0）。"""

    async def test_first_round_pass_converges(self):
        session = _session(_PassingAuditor())
        result = await session.run(_REQUEST, run_id="run-1")
        assert result.status == "converged"
        assert result.rounds_used == 0
        assert len(result.rounds) == 1
        assert result.rounds[0].round_index == 0
        assert result.final_report.verdict == "pass"

    async def test_converged_receipts_cover_compose_and_audit(self):
        session = _session(_PassingAuditor())
        result = await session.run(_REQUEST, run_id="run-2")
        assert len(result.receipts) == 2
        compose, audit = result.receipts
        assert compose.task_id == "swarm.cartography.compose"
        assert compose.role == SwarmSpecialistRole.CARTOGRAPHY_SPECIALIST
        assert compose.status == SwarmReceiptStatus.SUCCEEDED
        assert compose.produced_refs  # compose 产出交付券
        assert audit.task_id == "swarm.audit.judge"
        assert audit.role == SwarmSpecialistRole.AUDIT_JUDGE
        assert audit.produced_refs == []  # 审计单不产 mapspec 券

    async def test_result_stored_on_session(self):
        session = _session(_PassingAuditor())
        result = await session.run(_REQUEST)
        assert session.result is result

    async def test_final_delivery_is_ledger_backed(self):
        session = _session(_PassingAuditor())
        result = await session.run(_REQUEST)
        assert result.final_delivery.ref_id is not None
        assert session._ledger.get(result.final_delivery.ref_id) is not None


class TestEscalation:
    """不可修复 → 诚实升级（receipts + 审计单全程上交）。"""

    async def test_zero_repair_rounds_escalates_immediately(self):
        session = _session(_FailingAuditor(), max_repair_rounds=0)
        result = await session.run(_REQUEST, run_id="run-3")
        assert result.status == "failed_escalated"
        assert result.rounds_used == 0
        assert len(result.rounds) == 1
        assert result.final_report.verdict == "fail"

    async def test_repair_rounds_exhausted_escalates(self):
        session = _session(_FailingAuditor())  # 缺省 MAX_REPAIR_ROUNDS=2
        result = await session.run(_REQUEST, run_id="run-4")
        assert result.status == "failed_escalated"
        assert result.rounds_used == CartoAuditDuoSession.MAX_REPAIR_ROUNDS == 2
        assert len(result.rounds) == 3  # R0 + 2 轮 revise

    async def test_revise_increments_revision_each_round(self):
        session = _session(_FailingAuditor())
        result = await session.run(_REQUEST)
        revisions = [r.delivery.revision for r in result.rounds]
        assert revisions == [0, 1, 2]

    async def test_escalated_result_keeps_all_receipts(self):
        session = _session(_FailingAuditor())
        result = await session.run(_REQUEST)
        # 每轮 compose/revise + audit 各一券
        assert len(result.receipts) == 6
        assert all(r.status == SwarmReceiptStatus.SUCCEEDED for r in result.receipts)

    async def test_rounds_are_duo_round_instances(self):
        session = _session(_FailingAuditor())
        result = await session.run(_REQUEST)
        assert all(isinstance(r, DuoRound) for r in result.rounds)


class TestResultProjection:
    """DuoSessionResult.to_dict 的有界证据面。"""

    async def test_to_dict_shape(self):
        session = _session(_FailingAuditor(), max_repair_rounds=1)
        result = await session.run(_REQUEST, run_id="run-5")
        payload = result.to_dict()
        assert payload["status"] == "failed_escalated"
        assert payload["rounds_used"] == 1
        assert payload["final_verdict"] == "fail"
        assert payload["final_ref_id"] == result.final_delivery.ref_id
        assert payload["final_fingerprint"] == result.final_delivery.mapspec_fingerprint
        assert len(payload["rounds"]) == 2
        assert payload["rounds"][0]["round"] == 0
        assert payload["rounds"][0]["verdict"] == "fail"
        assert payload["rounds"][0]["vetoes"] == ["V1"]
        assert len(payload["receipts"]) == 4

    async def test_to_dict_receipts_are_model_dumps(self):
        session = _session(_PassingAuditor())
        result = await session.run(_REQUEST)
        payload = result.to_dict()
        assert isinstance(payload["receipts"][0], dict)
        assert payload["receipts"][0]["task_id"] == "swarm.cartography.compose"


class TestGovernorDiscipline:
    """每阶段取放并发额度（集群 ≤3 同一纪律）。"""

    async def test_slots_acquired_and_released_per_stage(self):
        governor = FakeGovernor()
        session = _session(_FailingAuditor(), governor=governor, max_repair_rounds=1)
        await session.run(_REQUEST, run_id="run-6")
        # R0 compose+audit + R1 revise+audit = 4 阶段
        assert len(governor.acquired) == 4
        assert sorted(governor.released) == sorted(governor.acquired)

    async def test_assignment_ids_are_stable_per_stage(self):
        governor = FakeGovernor()
        session = _session(_PassingAuditor(), governor=governor)
        await session.run(_REQUEST, run_id="run-7")
        assert governor.acquired == [
            "run-7:compose:0",
            "run-7:audit:0",
        ]

    async def test_converged_run_releases_all_slots(self):
        governor = FakeGovernor()
        session = _session(_PassingAuditor(), governor=governor)
        await session.run(_REQUEST)
        assert governor.snapshot()["active_count"] == 0


class TestAggregatorIntegration:
    """会话级聚合：receipts → manifest → merge；fail-open。"""

    async def test_aggregator_collects_receipts(self):
        aggregator = RecordingAggregator()
        session = _session(_PassingAuditor(), aggregator=aggregator, session_id="duo-agg")
        await session.run(_REQUEST, run_id="run-8")
        assert len(aggregator.collects) == 1
        collect = aggregator.collects[0]
        assert collect["session_id"] == "duo-agg"
        assert collect["run_id"] == "run-8"
        assert len(collect["receipts"]) == 2
        assert collect["tasks"] == []
        assert len(aggregator.merges) == 1

    async def test_aggregator_failure_is_fail_open(self):
        aggregator = RecordingAggregator(boom=True)
        session = _session(_PassingAuditor(), aggregator=aggregator)
        # 聚合异常只记日志，不影响会话终态
        result = await session.run(_REQUEST)
        assert result.status == "converged"

    async def test_no_aggregator_means_no_merge(self):
        session = _session(_PassingAuditor())
        result = await session.run(_REQUEST)
        assert result.status == "converged"


class TestDuoSessionInternals:
    async def test_auditor_stage_exception_propagates(self):
        """会话不吞审计异常（与 SwarmAggregator 的 fail-open 边界不同）。"""
        session = _session(_ExplodingAuditor())
        with pytest.raises(RuntimeError, match="auditor crashed"):
            await session.run(_REQUEST)

    async def test_max_repair_rounds_clamped_non_negative(self):
        session = _session(_FailingAuditor(), max_repair_rounds=-5)
        assert session._max_rounds == 0

    async def test_ledger_defaults_to_cartographer_ledger(self):
        ledger = ArtifactLedger()
        cartographer = CartographerAgent(ledger=ledger)
        session = CartoAuditDuoSession(
            cartographer=cartographer, auditor=_PassingAuditor()
        )
        assert session._ledger is ledger  # compose 与 audit 共享取货位

    async def test_run_label_from_request_title(self):
        session = _session(_PassingAuditor())
        result = await session.run({**_REQUEST, "title": "自定义标题"})
        assert "自定义标题" in result.receipts[0].summary


# ─────────────────────────── InProcessSpecialistRuntime ───────────────────────────


def _assignment(task_id: str, capability: str) -> Any:
    return type(
        "A",
        (),
        {
            "assignment_id": f"asg-{task_id}",
            "task": SwarmTaskDescriptor(
                task_id=task_id,
                role=SwarmSpecialistRole.CARTOGRAPHY_SPECIALIST,
                goal="compose task",
                capability=capability,
            ),
        },
    )()


class TestInProcessRuntimeRouting:
    """capability → compose / revise / audit 路由。"""

    async def test_compose_routing_via_capability_request(self):
        ledger = ArtifactLedger()
        runtime = InProcessSpecialistRuntime(
            cartographer=CartographerAgent(ledger=ledger),
            auditor=CriticAuditorAgent(),
            ledger=ledger,
        )
        runtime.register_request("swarm.cartography.compose", _REQUEST)
        receipt = await runtime.execute(
            _assignment("swarm.cartography.compose", "swarm.cartography.compose")
        )
        assert receipt.status == SwarmReceiptStatus.SUCCEEDED
        assert receipt.produced_refs and receipt.produced_refs[0].startswith("ref:mapspec-")
        assert "thematic map" in receipt.summary

    async def test_compose_routing_via_task_id_request(self):
        ledger = ArtifactLedger()
        runtime = InProcessSpecialistRuntime(
            cartographer=CartographerAgent(ledger=ledger),
            auditor=CriticAuditorAgent(),
            ledger=ledger,
        )
        runtime.register_request("task-42", _REQUEST)  # 按 task_id 注册
        receipt = await runtime.execute(
            _assignment("task-42", "swarm.cartography.compose")
        )
        assert receipt.status == SwarmReceiptStatus.SUCCEEDED

    async def test_unregistered_request_fails_honestly(self):
        ledger = ArtifactLedger()
        runtime = InProcessSpecialistRuntime(
            cartographer=CartographerAgent(ledger=ledger),
            auditor=CriticAuditorAgent(),
            ledger=ledger,
        )
        receipt = await runtime.execute(
            _assignment("swarm.cartography.compose", "swarm.cartography.compose")
        )
        assert receipt.status == SwarmReceiptStatus.FAILED
        assert receipt.error_code == "non_retryable"
        assert "未注册 compose 请求" in receipt.error

    async def test_revise_before_compose_fails(self):
        ledger = ArtifactLedger()
        runtime = InProcessSpecialistRuntime(
            cartographer=CartographerAgent(ledger=ledger),
            auditor=CriticAuditorAgent(),
            ledger=ledger,
        )
        receipt = await runtime.execute(
            _assignment("swarm.cartography.revise", "swarm.cartography.revise")
        )
        assert receipt.status == SwarmReceiptStatus.FAILED
        assert "revise 前必须先 compose" in receipt.error

    async def test_audit_before_compose_fails(self):
        ledger = ArtifactLedger()
        runtime = InProcessSpecialistRuntime(
            cartographer=CartographerAgent(ledger=ledger),
            auditor=CriticAuditorAgent(),
            ledger=ledger,
        )
        receipt = await runtime.execute(
            _assignment("swarm.audit.judge", "swarm.audit.judge")
        )
        assert receipt.status == SwarmReceiptStatus.FAILED
        assert "audit 前必须先 compose" in receipt.error

    async def test_audit_after_compose_produces_audit_ref(self):
        ledger = ArtifactLedger()
        runtime = InProcessSpecialistRuntime(
            cartographer=CartographerAgent(ledger=ledger),
            auditor=CriticAuditorAgent(),
            ledger=ledger,
        )
        runtime.register_request("swarm.cartography.compose", _REQUEST)
        await runtime.execute(
            _assignment("swarm.cartography.compose", "swarm.cartography.compose")
        )
        receipt = await runtime.execute(
            _assignment("swarm.audit.judge", "swarm.audit.judge")
        )
        assert receipt.status == SwarmReceiptStatus.SUCCEEDED
        assert receipt.produced_refs[0].startswith("ref:audit-")
        assert ledger.get(receipt.produced_refs[0]) is not None
        assert "verdict=" in receipt.summary

    async def test_revise_after_compose_succeeds(self):
        ledger = ArtifactLedger()
        runtime = InProcessSpecialistRuntime(
            cartographer=CartographerAgent(ledger=ledger),
            auditor=CriticAuditorAgent(),
            ledger=ledger,
        )
        runtime.register_request("swarm.cartography.compose", _REQUEST)
        first = await runtime.execute(
            _assignment("swarm.cartography.compose", "swarm.cartography.compose")
        )
        second = await runtime.execute(
            _assignment("swarm.cartography.revise", "swarm.cartography.revise")
        )
        assert second.status == SwarmReceiptStatus.SUCCEEDED
        assert second.produced_refs != first.produced_refs  # 新券（revision+1）

    async def test_unknown_capability_fails(self):
        ledger = ArtifactLedger()
        runtime = InProcessSpecialistRuntime(
            cartographer=CartographerAgent(ledger=ledger),
            auditor=CriticAuditorAgent(),
            ledger=ledger,
        )
        receipt = await runtime.execute(
            _assignment("swarm.data.base_geo", "swarm.data.base_geo")
        )
        assert receipt.status == SwarmReceiptStatus.FAILED
        assert "不认识 capability" in receipt.error

    async def test_audit_kwargs_forwarded(self):
        ledger = ArtifactLedger()
        seen: Dict[str, Any] = {}

        class _RecordingAuditor(CriticAuditorAgent):
            def audit(self, delivery, **kwargs):
                seen.update(kwargs)
                return DeliveryAuditReport(verdict="pass", review_status="passed")

        runtime = InProcessSpecialistRuntime(
            cartographer=CartographerAgent(ledger=ledger),
            auditor=_RecordingAuditor(),
            ledger=ledger,
            audit_kwargs={"user_goal": "测试目标", "round_index": 1},
        )
        runtime.register_request("swarm.cartography.compose", _REQUEST)
        await runtime.execute(
            _assignment("swarm.cartography.compose", "swarm.cartography.compose")
        )
        await runtime.execute(_assignment("swarm.audit.judge", "swarm.audit.judge"))
        assert seen["user_goal"] == "测试目标"
        assert seen["round_index"] == 1

    async def test_compose_failure_folds_to_failed_receipt(self):
        ledger = ArtifactLedger()
        runtime = InProcessSpecialistRuntime(
            cartographer=CartographerAgent(ledger=ledger),
            auditor=CriticAuditorAgent(),
            ledger=ledger,
        )
        runtime.register_request("swarm.cartography.compose", {"field": "x"})  # 缺 geojson
        receipt = await runtime.execute(
            _assignment("swarm.cartography.compose", "swarm.cartography.compose")
        )
        assert receipt.status == SwarmReceiptStatus.FAILED
        assert "geojson" in receipt.error

    async def test_heartbeat_callback_accepted_and_ignored(self):
        ledger = ArtifactLedger()
        runtime = InProcessSpecialistRuntime(
            cartographer=CartographerAgent(ledger=ledger),
            auditor=CriticAuditorAgent(),
            ledger=ledger,
        )
        runtime.register_request("swarm.cartography.compose", _REQUEST)
        receipt = await runtime.execute(
            _assignment("swarm.cartography.compose", "swarm.cartography.compose"),
            on_heartbeat=lambda note: None,
        )
        assert receipt.status == SwarmReceiptStatus.SUCCEEDED
