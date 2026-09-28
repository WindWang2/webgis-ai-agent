"""委派契约层（ADR-0187 delegation_contracts）有界纪律测试。

Zero Big Data in Context 的第一道强制点：所有上下文面（投影 / 提货券 /
任务描述符 / 派发单 / 清单 / 运行态）的 fail-closed validator 与有界
截断。

口径说明：field_validator 内抛出的 ``SwarmContractError``（ValueError
子类）经 pydantic v2 包装为 ``ValidationError`` —— 违约依然 fail-closed
（构造即失败），仅外层异常类型不同；本套件按 ``ValidationError`` +
原始消息匹配断言。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.agent_swarm.delegation_contracts import (
    MAX_CONSTRAINTS,
    MAX_DEPENDS_PER_TASK,
    MAX_ERROR_CHARS,
    MAX_FACTS,
    MAX_MANIFEST_ENTRIES,
    MAX_REFS_PER_RECEIPT,
    MAX_SUMMARY_CHARS,
    MAX_SWARM_TASKS,
    SpecialistAssignment,
    SubagentReceipt,
    SwarmAssetEntry,
    SwarmAssetManifest,
    SwarmErrorCode,
    SwarmExecutionStatus,
    SwarmReceiptStatus,
    SwarmRunState,
    SwarmSpecialistRole,
    SwarmTaskDescriptor,
    WorldStateProjection,
)
from app.services.workflow_runtime.contracts import NodeState


def _task(task_id: str = "t1", **overrides) -> SwarmTaskDescriptor:
    base = {
        "task_id": task_id,
        "goal": "探测任务",
        "role": SwarmSpecialistRole.DATA_HUNTER,
        "capability": "cap.x",
    }
    base.update(overrides)
    return SwarmTaskDescriptor(**base)


def _receipt(**overrides) -> SubagentReceipt:
    base = {
        "assignment_id": "asg-1",
        "task_id": "t1",
        "role": SwarmSpecialistRole.DATA_HUNTER,
        "status": SwarmReceiptStatus.SUCCEEDED,
    }
    base.update(overrides)
    return SubagentReceipt(**base)


class TestWorldStateProjection:
    """输入切片②：refs-only + 事实行截断。"""

    def test_non_ref_relevant_refs_rejected_fail_closed(self):
        with pytest.raises(ValidationError, match="Zero Big Data in Context"):
            WorldStateProjection(
                session_id="s1", relevant_refs=["ref:ok", "payload-geojson-inline"]
            )

    def test_oversized_facts_and_constraints_clipped(self):
        projection = WorldStateProjection(
            session_id="s1",
            facts=["x" * 500, "short"],
            constraints=["y" * 500],
        )
        assert len(projection.facts) == 2
        assert len(projection.facts[0]) == 240  # MAX_FACT_CHARS 截断
        assert projection.facts[1] == "short"
        assert len(projection.constraints[0]) == 240

    def test_collection_bounds_enforced(self):
        with pytest.raises(ValidationError):
            WorldStateProjection(session_id="s1", facts=["a"] * (MAX_FACTS + 1))
        with pytest.raises(ValidationError):
            WorldStateProjection(session_id="s1", constraints=["a"] * (MAX_CONSTRAINTS + 1))

    def test_goal_summary_bounded_by_max_goal_chars(self):
        with pytest.raises(ValidationError):
            WorldStateProjection(session_id="s1", goal_summary="g" * 3000)


class TestSubagentReceipt:
    """输出提货券：refs-only + 摘要/错误截断 + 数量上限。"""

    def test_non_ref_produced_refs_rejected(self):
        with pytest.raises(ValidationError, match="只收 ref: 提货券"):
            _receipt(produced_refs=["not-a-ref"])

    def test_summary_and_error_clipped_to_bounds(self):
        receipt = _receipt(summary="s" * 900, error="e" * 900)
        assert len(receipt.summary) == MAX_SUMMARY_CHARS
        assert len(receipt.error) == MAX_ERROR_CHARS

    def test_produced_refs_capped(self):
        with pytest.raises(ValidationError):
            _receipt(produced_refs=[f"ref:r{i}" for i in range(MAX_REFS_PER_RECEIPT + 1)])

    def test_defaults_are_bounded_zero_state(self):
        receipt = _receipt()
        assert receipt.attempts == 1
        assert receipt.heartbeats == 0
        assert receipt.degraded is False
        assert receipt.produced_refs == []


class TestSwarmTaskDescriptor:
    """子任务描述符：词表 / 上限 / 运行期字段缺省。"""

    def test_task_id_pattern_enforced(self):
        with pytest.raises(ValidationError):
            _task("Bad ID With Spaces")

    def test_side_effect_vocabulary_fail_closed(self):
        with pytest.raises(ValidationError, match="未知副作用词表值"):
            _task(side_effect="drop_everything")

    def test_depends_on_over_cap_rejected(self):
        deps = tuple(f"d{i}" for i in range(MAX_DEPENDS_PER_TASK + 1))
        with pytest.raises(ValidationError, match="depends_on 超过上限"):
            _task(depends_on=deps)

    def test_priority_and_retry_bounds(self):
        assert _task(priority=-10).priority == -10
        assert _task(priority=10).priority == 10
        with pytest.raises(ValidationError):
            _task(priority=11)
        with pytest.raises(ValidationError):
            _task(max_retries=3)  # > MAX_RETRIES_PER_TASK(2)

    def test_non_positive_timeout_rejected(self):
        with pytest.raises(ValidationError):
            _task(timeout_s=0.0)

    def test_runtime_fields_default_to_pending(self):
        task = _task()
        assert task.state == NodeState.PENDING  # 复用 Execution Graph 词表（大写）
        assert task.attempts == 0
        assert task.degraded is False


class TestSpecialistAssignment:
    """派发单：三段切片装配 + 上游提货券有界。"""

    def test_upstream_receipts_capped_at_six(self):
        task = _task()
        projection = WorldStateProjection(session_id="s1")
        receipts = [_receipt(assignment_id=f"asg-{i}") for i in range(7)]
        with pytest.raises(ValidationError):
            SpecialistAssignment(
                assignment_id="asg-main", task=task, projection=projection,
                upstream_receipts=receipts,
            )

    def test_assignment_carries_only_bounded_slices(self):
        assignment = SpecialistAssignment(
            assignment_id="asg-main",
            task=_task(goal="本任务目标"),
            projection=WorldStateProjection(session_id="s1", facts=["f1"]),
            upstream_receipts=[_receipt(summary="上游摘要")],
        )
        assert assignment.task.goal == "本任务目标"
        assert assignment.projection.facts == ["f1"]
        assert assignment.upstream_receipts[0].summary == "上游摘要"
        # 无 payload 面：派发单上不存在原始几何字段
        assert "geojson" not in assignment.model_dump()


class TestSwarmAssetManifest:
    """凭证总线提货单：有界 dict 投影。"""

    def test_to_bounded_dict_truncates_lists(self):
        # entries 由 pydantic max_length 把守（32）；failed_capabilities /
        # dropped_refs 是裸 list，靠 to_bounded_dict 有界投影截断。
        manifest = SwarmAssetManifest(
            run_id="r1",
            session_id="s1",
            entries=[SwarmAssetEntry(ref_id=f"ref:e{i}") for i in range(MAX_MANIFEST_ENTRIES)],
            failed_capabilities=[f"cap.{i}" for i in range(MAX_SWARM_TASKS + 5)],
            dropped_refs=[f"ref:d{i}" for i in range(MAX_MANIFEST_ENTRIES + 5)],
        )
        bounded = manifest.to_bounded_dict()
        assert len(bounded["entries"]) == MAX_MANIFEST_ENTRIES
        assert len(bounded["failed_capabilities"]) == MAX_SWARM_TASKS
        assert len(bounded["dropped_refs"]) == MAX_MANIFEST_ENTRIES
        assert bounded["failed_capabilities"][-1] == f"cap.{MAX_SWARM_TASKS - 1}"

    def test_entries_capped_at_construction(self):
        with pytest.raises(ValidationError):
            SwarmAssetManifest(
                run_id="r1",
                session_id="s1",
                entries=[
                    SwarmAssetEntry(ref_id=f"ref:e{i}")
                    for i in range(MAX_MANIFEST_ENTRIES + 1)
                ],
            )

    def test_asset_entry_summary_clipped(self):
        entry = SwarmAssetEntry(ref_id="ref:e1", summary="s" * 900)
        assert len(entry.summary) == MAX_SUMMARY_CHARS

    def test_asset_entry_defaults(self):
        entry = SwarmAssetEntry(ref_id="ref:e1")
        assert entry.role == SwarmSpecialistRole.DATA_HUNTER
        assert entry.capability == ""


class TestSwarmExecutionStatus:
    """集群运行态：manifest_ref 必须 ref:。"""

    def test_manifest_ref_must_be_ref_prefixed(self):
        with pytest.raises(ValidationError, match="manifest_ref 必须是 ref:"):
            SwarmExecutionStatus(run_id="r1", session_id="s1", manifest_ref="swarm-1")

    def test_ref_prefixed_manifest_ref_accepted(self):
        status = SwarmExecutionStatus(run_id="r1", session_id="s1", manifest_ref="ref:swarm-1")
        assert status.manifest_ref == "ref:swarm-1"
        assert status.state == SwarmRunState.RUNNING


class TestVocabularies:
    """词表完备性（下游依赖的稳定投影面）。"""

    def test_run_state_vocabulary(self):
        assert SwarmRunState.RUNNING == "running"
        assert SwarmRunState.SUCCEEDED == "succeeded"
        assert SwarmRunState.PARTIAL == "partial"
        assert SwarmRunState.FAILED == "failed"
        assert SwarmRunState.CANCELLED == "cancelled"

    def test_error_code_vocabulary(self):
        assert SwarmErrorCode.RETRYABLE == "retryable"
        assert SwarmErrorCode.NON_RETRYABLE == "non_retryable"
        assert SwarmErrorCode.TIMEOUT == "timeout"
        assert SwarmErrorCode.CANCELLED == "cancelled"

    def test_role_vocabulary_has_four_specialists(self):
        assert [r.value for r in SwarmSpecialistRole] == [
            "data_hunter",
            "compute_specialist",
            "cartography_specialist",
            "audit_judge",
        ]
