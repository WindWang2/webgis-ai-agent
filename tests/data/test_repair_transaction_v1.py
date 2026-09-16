"""RepairSession 修复事务回归锁（DQH V1）。

不变式：
- 状态机确定性：proposed → dry_run → applied → verified | failed |
  rolled_back；非法迁移 raise，绝不静默换态；
- session_id 确定性：sha256(plan_id + source_digest)，墙钟不参与；
- dry-run 预览绝不改源、绝不登记 ref（纯 deepcopy 演练 + 预测摘要）；
- apply 失败（故障注入）→ state=failed，源 payload 逐字节不变，
  零部分状态（新 ref 语义的结构性保证）；
- 幂等：同 plan + 同源已 verified → 重复调用返回既有结果，不二次执行；
- rollback 只从 applied/verified 出发；产物 = 回指 source 的新 ref 证据 +
  状态迁移，绝不物理改写任何 ref（append-only）。
"""
from __future__ import annotations

import copy

import pytest

from app.lib.data.quality import QualityIssue, QualityIssueCode, QualityReport
from app.services.data_quality.repair_plan import build_repair_plan
from app.services.data_quality.repair_transaction import (
    STATE_APPLIED,
    STATE_DRY_RUN,
    STATE_FAILED,
    STATE_PROPOSED,
    STATE_ROLLED_BACK,
    STATE_VERIFIED,
    mark_applied,
    mark_dry_run,
    mark_failed,
    mark_rolled_back,
    mark_verified,
    open_repair_session,
    reset_session_cache,
    resolve_pipeline_ops,
    run_repair_transaction,
)


@pytest.fixture(autouse=True)
def _isolated_session_cache():
    reset_session_cache()
    yield
    reset_session_cache()

_DIRTY_FC = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
         "properties": {"v": 1}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [3.0, 4.0]},
         "properties": {"v": 2}},
    ],
}


def _dirty_geojson() -> dict:
    """含空几何 + 重复行的脏数据（remove_empty/deduplicate 可确定性修复）。"""
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": None, "properties": {"id": 1}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
             "properties": {"id": 2, "v": 10}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
             "properties": {"id": 2, "v": 10}},
        ],
    }


def _plan_for(payload: dict, ops=("remove_empty", "deduplicate")):
    report = QualityReport(
        target_ref="ref:dirty",
        issues=[QualityIssue(code=QualityIssueCode.EMPTY_GEOMETRY),
                QualityIssue(code=QualityIssueCode.DUPLICATE_ROWS)],
    )
    plan = build_repair_plan(report, dataset_identity="fp-dirty-1")
    assert plan.plan_id
    return plan, list(ops)


class TestStateMachine:
    def test_open_is_proposed_with_deterministic_id(self):
        plan, _ = _plan_for(_DIRTY_FC)
        s1 = open_repair_session(plan=plan, source_payload=_DIRTY_FC)
        s2 = open_repair_session(plan=plan, source_payload=_DIRTY_FC)
        assert s1.state == STATE_PROPOSED
        assert s1.session_id == s2.session_id
        assert s1.session_id.startswith("rsession_")
        assert s1.source_digest

    def test_happy_path_transitions(self):
        plan, _ = _plan_for(_DIRTY_FC)
        s = open_repair_session(plan=plan, source_payload=_DIRTY_FC)
        s = mark_dry_run(s, preview={"predicted_ops": ["remove_empty"]})
        assert s.state == STATE_DRY_RUN
        s = mark_applied(s, apply_result={"status": "success", "repaired_ref": "ref:r"})
        assert s.state == STATE_APPLIED
        s = mark_verified(s, verify_result={"residual_status": "pass"})
        assert s.state == STATE_VERIFIED
        s = mark_rolled_back(s, rollback_evidence={"rollback_ref": "ref:src"})
        assert s.state == STATE_ROLLED_BACK

    def test_illegal_transitions_raise(self):
        plan, _ = _plan_for(_DIRTY_FC)
        s = open_repair_session(plan=plan, source_payload=_DIRTY_FC)
        with pytest.raises(ValueError):
            mark_applied(s, apply_result={"status": "success"})
        with pytest.raises(ValueError):
            mark_rolled_back(s, rollback_evidence={})
        s2 = mark_failed(open_repair_session(plan=plan, source_payload=_DIRTY_FC),
                         reason="x")
        with pytest.raises(ValueError):
            mark_dry_run(s2, preview={})

    def test_apply_failure_marked_failed(self):
        plan, _ = _plan_for(_DIRTY_FC)
        s = mark_dry_run(open_repair_session(plan=plan, source_payload=_DIRTY_FC),
                         preview={})
        s = mark_failed(s, reason="boom")
        assert s.state == STATE_FAILED
        assert s.history[-1]["reason"] == "boom"

    def test_history_bounded(self):
        plan, _ = _plan_for(_DIRTY_FC)
        s = open_repair_session(plan=plan, source_payload=_DIRTY_FC)
        for i in range(40):
            s.history.append({"n": i})
        assert len(s.to_bounded_dict()["history"]) <= 16


class TestResolvePipelineOps:
    def test_repair_geometry_backing_resolved_in_canonical_order(self):
        ops = resolve_pipeline_ops(["repair_geometry"])
        assert ops, "repair_geometry 的 fn 背书应解析出 pipeline ops"
        assert all(o in ("make_valid", "snap_within_tolerance", "deduplicate")
                   for o in ops)

    def test_non_pipeline_ops_skipped_honestly(self):
        # filter_null 的背书是 geocompute 算子，不在 pipeline 事务口径内 ——
        # 解析结果为空且不硬凑。
        assert resolve_pipeline_ops(["filter_null"]) == []

    def test_empty(self):
        assert resolve_pipeline_ops([]) == []


class TestTransaction:
    @pytest.mark.asyncio
    async def test_dry_run_preview_no_mutation(self):
        payload = _dirty_geojson()
        snapshot = copy.deepcopy(payload)
        plan, ops = _plan_for(payload)
        result = await run_repair_transaction(
            geojson=payload, plan=plan, operations=ops, mode="dry_run")
        assert result["session"]["state"] == STATE_DRY_RUN
        assert payload == snapshot, "dry-run 绝不改源"
        preview = result["preview"]
        assert preview["predicted_feature_count_after"] == 1
        assert preview["source_feature_count"] == 3

    @pytest.mark.asyncio
    async def test_apply_produces_repaired_and_source_untouched(self):
        payload = _dirty_geojson()
        snapshot = copy.deepcopy(payload)
        plan, ops = _plan_for(payload)
        result = await run_repair_transaction(
            geojson=payload, plan=plan, operations=ops, mode="apply")
        assert result["session"]["state"] in (STATE_APPLIED, STATE_VERIFIED)
        assert payload == snapshot, "apply 绝不改源（新 ref 语义）"
        assert result["apply"]["feature_count_after"] == 1
        assert result["apply"]["content_digest_before"]
        assert result["apply"]["content_digest_after"]

    @pytest.mark.asyncio
    async def test_apply_failure_source_intact(self, monkeypatch):
        payload = _dirty_geojson()
        snapshot = copy.deepcopy(payload)
        plan, ops = _plan_for(payload)

        async def _boom(*a, **k):
            raise RuntimeError("injected pipeline failure")

        # 打在 apply 阶段的执行件上（dry-run 已通过的路径继续有效）。
        monkeypatch.setattr(
            "app.services.data_quality.repair_execution.execute_repair", _boom)
        result = await run_repair_transaction(
            geojson=payload, plan=plan, operations=ops, mode="apply")
        assert result["session"]["state"] == STATE_FAILED
        assert "injected pipeline failure" in result["failure"]["reason"]
        assert payload == snapshot, "失败后源 artifact 逐字节不变"

    @pytest.mark.asyncio
    async def test_idempotent_rerun_returns_existing_session(self):
        payload = _dirty_geojson()
        plan, ops = _plan_for(payload)
        first = await run_repair_transaction(
            geojson=payload, plan=plan, operations=ops, mode="apply")
        second = await run_repair_transaction(
            geojson=payload, plan=plan, operations=ops, mode="apply")
        assert first["session"]["session_id"] == second["session"]["session_id"]
        assert second["idempotent_reuse"] is True
        assert second["session"]["state"] == first["session"]["state"]

    @pytest.mark.asyncio
    async def test_rollback_records_evidence_without_mutation(self):
        payload = _dirty_geojson()
        snapshot = copy.deepcopy(payload)
        plan, ops = _plan_for(payload)
        result = await run_repair_transaction(
            geojson=payload, plan=plan, operations=ops, mode="apply")
        rollback = await run_repair_transaction(
            geojson=payload, plan=plan, operations=ops, mode="rollback",
            prior=result)
        assert rollback["session"]["state"] == STATE_ROLLED_BACK
        assert rollback["rollback"]["plan_id"] == plan.plan_id
        assert payload == snapshot

    @pytest.mark.asyncio
    async def test_rollback_without_apply_raises(self):
        payload = _dirty_geojson()
        plan, ops = _plan_for(payload)
        with pytest.raises(ValueError):
            await run_repair_transaction(
                geojson=payload, plan=plan, operations=ops, mode="rollback")
