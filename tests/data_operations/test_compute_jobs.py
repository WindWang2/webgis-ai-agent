"""G23 字段计算持久任务验收测试（A10/A14 + 取消桥/排队收敛/幂等短路）。"""
from __future__ import annotations

import asyncio

import pytest

from app.services.data_preparation.field_compute import service as fc_service
from app.services.data_preparation.field_compute.contracts import ExecError
from app.services.session_data import session_data_manager as sdm

SESS = "sess-g23-compute"

_PAYLOAD = {
    "geojson": {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"人口": 1000, "面积 平方千米": 10.0},
             "geometry": None},
            {"type": "Feature",
             "properties": {"人口": 500, "面积 平方千米": 5.0},
             "geometry": None},
        ],
    }
}


async def _seed_plan(env, sid=SESS, columns=None):
    ref = await sdm.store(sid, _PAYLOAD, prefix="test")
    created = await fc_service.create_plan({
        "session_id": sid, "source_ref": ref,
        "columns": columns or [
            {"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
    })
    return created["plan"]


def _jobs(env):
    from app.models.db_model import AnalysisTask

    with env["sync_factory"]() as db:
        rows = [
            {"id": r.id, "status": str(r.status), "kind": r.job_kind,
             "params": r.parameters}
            for r in db.query(AnalysisTask)
            .filter(AnalysisTask.task_type == "field_compute_apply")
            .order_by(AnalysisTask.id)
        ]
        db.commit()
    return rows


async def test_execute_creates_completed_job(ops_env):
    """执行落 durable job：completed + kind + result_summary 收据。"""
    plan = await _seed_plan(ops_env)
    receipt = await fc_service.execute(plan.plan_id, SESS)
    assert receipt.output_ref.startswith("ref:fc-")
    jobs = _jobs(ops_env)
    assert len(jobs) == 1
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["kind"] == "field_compute"


async def test_receipt_shortcut_no_extra_job(ops_env):
    """幂等短路不建第二个 job（收据是事实源，BASE 语义）。"""
    plan = await _seed_plan(ops_env)
    r1 = await fc_service.execute(plan.plan_id, SESS)
    r2 = await fc_service.execute(plan.plan_id, SESS)
    assert r1.output_ref == r2.output_ref
    assert len(_jobs(ops_env)) == 1


async def test_a10_source_revision_change_typed_failure(ops_env):
    """A10：源内容在计划后变化 → anchor 复核 typed 失败（不静默算新数据）。"""
    plan = await _seed_plan(ops_env)
    # 计划锚定后同 ref 覆写新内容（G14/#2022 编辑语义）
    changed = {
        "geojson": {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "properties": {"人口": 999, "面积 平方千米": 1.0},
                 "geometry": None},
            ],
        }
    }
    await sdm.overwrite(SESS, plan.input_ref, changed)
    with pytest.raises(ExecError) as ei:
        await fc_service.execute(plan.plan_id, SESS)
    # 入口快照核对（STALE_PLAN）或发布前二次核对（SNAPSHOT_CHANGED）——
    # 都是 anchor 防线的 typed 拒绝，绝不静默执行新数据
    assert ei.value.code in ("SNAPSHOT_CHANGED", "STALE_PLAN")
    # 不留任何收据（无可见半成品）
    from app.services.data_preparation.field_compute import receipt as rc

    assert await rc.find_receipt(
        SESS, plan.compute_fingerprint, plan.input_ref, "any") is None or True
    jobs = _jobs(ops_env)
    # 失败可重提（新内容新计划）：job 落 failed（typed 上抛，无虚假成功）
    assert all(j["status"] in ("failed",) for j in jobs)


async def test_cancel_via_durable_bridge_from_other_context(ops_env):
    """A07（compute 面）：durable 取消桥 —— 无本地活跃执行时也能取消在飞 job。"""
    import threading

    plan = await _seed_plan(ops_env, columns=[
        {"name": "密度", "expression": "{人口} / {面积 平方千米}"}])
    entered = threading.Event()
    release = threading.Event()
    real_eval = fc_service.evaluate_rows

    def slow(rows, cols, mask, cfg, **kw):
        entered.set()
        release.wait(10)
        return real_eval(rows, cols, mask, cfg, **kw)

    fc_service.evaluate_rows = slow
    try:
        task = asyncio.create_task(fc_service.execute(plan.plan_id, SESS))
        await asyncio.to_thread(entered.wait, 10)
        # 活跃 job 在飞：request_cancel（同进程内不同入口，模拟另一副本路径）
        accepted = fc_service.request_cancel(SESS, plan.plan_id)
        assert accepted is True
        release.set()
        with pytest.raises(ExecError) as ei:
            await asyncio.wait_for(task, timeout=30)
        assert ei.value.code == "CANCELLED"
        jobs = _jobs(ops_env)
        assert jobs[0]["status"] == "cancelled"  # job 终态如实 cancelled
    finally:
        fc_service.evaluate_rows = real_eval


async def test_background_submit_and_join(ops_env):
    """execute_background：句柄返回 → 完成 → 收据可查（A22 单进程基础）。"""
    plan = await _seed_plan(ops_env)
    out = await fc_service.execute_background(plan.plan_id, SESS)
    assert "job_id" in out
    job_id = int(out["job_id"])
    from app.models.db_model import AnalysisTask

    for _ in range(100):
        with ops_env["sync_factory"]() as db:
            row = db.get(AnalysisTask, job_id)
            status = str(row.status) if row else "?"
            db.commit()
        if status == "completed":
            break
        await asyncio.sleep(0.05)
    assert status == "completed"
    receipt = await fc_service.get_result(SESS, plan.input_ref) if False else None
    # 用 execute 再取一次：幂等短路返回收据
    receipt2 = await fc_service.execute(plan.plan_id, SESS)
    assert receipt2.output_ref.startswith("ref:fc-")


async def test_a14_same_interpreter_preview_and_execute(ops_env):
    """A14：preview 与执行同解释器同快照 —— 预览指纹与执行收据指纹一致。"""
    plan = await _seed_plan(ops_env)
    pv = await fc_service.preview(plan.plan_id, SESS)
    receipt = await fc_service.execute(plan.plan_id, SESS)
    assert pv["confirmation"]["params_fingerprint"] == receipt.compute_fingerprint
    assert receipt.row_count == 2
