"""G23 导入持久任务验收测试（A01/A02/A03/A07/A08/A09/A11/A13/A16）。

两个 DataImportService 实例模拟两个 API 副本（进程内隔离子集：内存登记
互不可见、DB 与 staging 文件共享 —— 与双进程拓扑的共享面一致）。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services.data_import.service import DataImportError, DataImportService
from tests.data_operations.conftest import GEOJSON_A, make_csv_bytes

SESS = "sess-g23-import"


async def _seed_batch(env, files=None):
    """创建 + 计划 + 勾选（同步路径提交前的一切准备）。"""
    svc = DataImportService()
    files = files or [("a.geojson", json.dumps(GEOJSON_A).encode())]
    state = await svc.create_batch(SESS, "user:g23", files)
    keys = [m.member_key for m in state.members]
    await svc.save_plan(state.batch_id, SESS, {k: {} for k in keys},
                        selected=keys)
    return svc, state, keys


def _job_ids(env):
    from app.models.db_model import AnalysisTask

    with env["sync_factory"]() as db:
        rows = [
            {"id": r.id, "status": str(r.status), "task_type": r.task_type,
             "kind": r.job_kind, "idem": r.idempotency_key}
            for r in db.query(AnalysisTask)
            .filter(AnalysisTask.task_type == "data_import_commit")
            .order_by(AnalysisTask.id)
        ]
        db.commit()
    return rows


async def test_a01_concurrent_submit_single_job(ops_env):
    """A01：相同提交并发只产生一个逻辑 job（幂等键收敛）。"""
    svc, state, keys = await _seed_batch(ops_env)
    out1, out2 = await asyncio.gather(
        svc.start_commit(state.batch_id, SESS, background=False),
        svc.start_commit(state.batch_id, SESS, background=False),
        return_exceptions=True,
    )
    # 第二个提交在锁内看到 COMMITTING → 409 冲突（不是第二个 job）
    codes = sorted(
        out1.get("status", "conflict") if isinstance(out1, dict) else "conflict"
        for out in (out1, out2)
    )
    jobs = _job_ids(ops_env)
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "data_import"
    # 完成后重复提交 → 幂等短路（无新 job、duplicate 标注）
    if isinstance(out1, dict) and out1.get("status") == "completed":
        again = await svc.start_commit(state.batch_id, SESS, background=False)
        assert again["status"] == "completed"
        assert again["receipts"][keys[0]]["duplicate"] is True
        assert len(_job_ids(ops_env)) == 1


async def test_a02_plan_version_change_new_job(ops_env):
    """A02：不同计划版本不误复用 —— 改计划后提交是新 job（新键/新计划 hash）。"""
    svc, state, keys = await _seed_batch(ops_env)
    out1 = await svc.start_commit(state.batch_id, SESS, background=False)
    assert out1["status"] == "completed"
    # 修改计划（keep_fields 收窄 → 内容不同 → plan_hash 变化 → plan_version+1）
    await svc.save_plan(state.batch_id, SESS, {keys[0]: {"keep_fields": []}})
    out2 = await svc.start_commit(state.batch_id, SESS, background=False)
    assert out2["status"] == "completed"
    jobs = _job_ids(ops_env)
    assert len(jobs) == 2
    assert jobs[0]["idem"] != jobs[1]["idem"]
    # 不同计划：收据 plan_hash 不同；产物内容不同（keep_fields=[] 无属性列）
    assert (
        out2["receipts"][keys[0]]["plan_hash"]
        != out1["receipts"][keys[0]]["plan_hash"]
    )
    assert (
        out2["receipts"][keys[0]]["output_ref"]
        != out1["receipts"][keys[0]]["output_ref"]
    )


async def test_a03_other_instance_read_keeps_committing(ops_env):
    """A03：另一副本读批次不重置活任务（多副本误判消除）。"""
    svc_a, state, keys = await _seed_batch(ops_env)
    csv = make_csv_bytes(
        [[116.0 + i * 0.001, 39.0 + i * 0.001, i] for i in range(3000)],
        ["lng", "lat", "v"])
    # 大 CSV 保证提交窗口
    svc_b = DataImportService()  # 另一副本：内存登记互不可见
    state_big = await svc_b.create_batch(SESS, "user:g23", [("big.csv", csv)])
    k = state_big.members[0].member_key
    await svc_b.save_plan(state_big.batch_id, SESS,
                          {k: {"mode": "table", "source_crs_declared": "EPSG:4326"}},
                          selected=[k])

    accepted = await svc_a.start_commit(state_big.batch_id, SESS, background=True)
    assert accepted["status"] == "committing"
    # 副本 B 读：不得把 COMMITTING 复位（G23 核心修复）
    view = await svc_b.get_batch(state_big.batch_id, SESS)
    assert view.status.value == "committing"
    assert all(m.status.value == "committing" for m in view.members)
    # 副本 B 再提交 → 冲突（活跃 job 持有批次写权）
    with pytest.raises(DataImportError) as ei:
        await svc_b.start_commit(state_big.batch_id, SESS, background=False)
    assert ei.value.code == "COMMIT_IN_PROGRESS"
    # 副本 B 删除 → 同样被拒
    with pytest.raises(DataImportError) as ei2:
        await svc_b.delete_batch(state_big.batch_id, SESS)
    assert ei2.value.code == "COMMIT_IN_PROGRESS"
    # 等 A 完成后归一
    task = svc_a._commit_tasks.get(state_big.batch_id)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout=120)
    final = await svc_b.get_batch(state_big.batch_id, SESS)
    assert final.status.value == "completed"


async def test_a07_a08_cancel_from_other_instance(ops_env):
    """A07/A08：另一副本取消生效；取消后迟到完成不能变 success。"""
    csv = make_csv_bytes(
        [[116.0 + i * 0.001, 39.0 + i * 0.001, i] for i in range(200_000)],
        ["lng", "lat", "v"])
    svc_a = DataImportService()
    state = await svc_a.create_batch(SESS, "user:g23", [("big.csv", csv)])
    k = state.members[0].member_key
    await svc_a.save_plan(SESS and state.batch_id, SESS,
                          {k: {"mode": "table"}},
                          selected=[k])
    accepted = await svc_a.start_commit(state.batch_id, SESS, background=True)
    job_id = accepted["job_id"]

    await asyncio.sleep(0.3)  # 让提交进入解释循环
    svc_b = DataImportService()
    out = await svc_b.cancel_batch(state.batch_id, SESS)
    assert out["status"] in ("cancelled", "cancelling")

    task = svc_a._commit_tasks.get(state.batch_id)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout=60)
    # job 终态必须是 cancelled（迟到完成不写 completed）
    from app.services.jobs.store import DurableJobStore

    async with ops_env["async_factory"]() as db:
        job = await DurableJobStore.get(db, job_id)
        await db.commit()
    assert job is not None and job.status == "cancelled"
    final = await svc_b.get_batch(state.batch_id, SESS)
    assert final.status.value == "cancelled"
    for m in final.members:
        assert m.status.value != "committing"
    # P4 取消语义：取消前已完成的成员产物**保留**（不可回滚的已发布产物），
    # 收据如实披露 completed；未完成成员不得有 completed 收据（无虚假成功）。
    # job 终态保持 cancelled（迟到完成不改写，A08）。
    for m in final.members:
        receipt = final.receipts.get(m.member_key)
        if m.status.value == "completed":
            assert receipt is not None and receipt.status == "completed"
            assert receipt.output_ref  # retained 可追踪
        else:
            assert receipt is None or receipt.status != "completed"


async def test_a09_cancel_after_completed_semantics(ops_env):
    """A09：完成后的取消有明确语义 —— completed 保持，不误报已取消。"""
    from app.services.jobs.store import DurableJobStore

    svc, state, keys = await _seed_batch(ops_env)
    out = await svc.start_commit(state.batch_id, SESS, background=False)
    assert out["status"] == "completed"
    job_id = out["job_id"]
    # 批次面：BASE 语义保持 —— 非提交中取消 → typed NOT_COMMITTING（409）
    with pytest.raises(DataImportError) as ei:
        await svc.cancel_batch(state.batch_id, SESS)
    assert ei.value.code == "NOT_COMMITTING"
    # job 面：终态取消是 no-op —— completed 不被改回 cancelling
    async with ops_env["async_factory"]() as db:
        changed, current = await DurableJobStore.request_cancel(db, int(job_id))
        await db.commit()
    assert changed is False and current.value == "completed"
    final = await svc.get_batch(state.batch_id, SESS)
    assert final.status.value == "completed"


async def test_a11_receipt_reconcile_after_job_crash(ops_env):
    """A11：job 终态后 batch.json 缺失更新的对账 —— 幂等键查询核实，不盲目重做。

    模拟「commit 完成、job 落终态，但批次状态文件停留在 COMMITTING」的
    崩溃窗口：下一次读批次按 durable 事实归一化（收据优先）。
    """
    from app.services.data_import import staging

    svc, state, keys = await _seed_batch(ops_env)
    out = await svc.start_commit(state.batch_id, SESS, background=False)
    assert out["status"] == "completed"
    # 人为制造崩溃窗口：状态文件回写 COMMITTING（收据保留）
    fresh = staging.load_state(state.batch_id)
    fresh.status = staging.BatchStatus.COMMITTING if hasattr(staging, "BatchStatus") else fresh.status
    from app.services.data_import.models import BatchStatus, MemberStatus

    fresh.status = BatchStatus.COMMITTING
    for m in fresh.members:
        m.status = MemberStatus.COMMITTING
    staging.save_state(fresh)
    # 新副本读：无活跃 job → 归一化；有收据 → COMPLETED（不盲目重做）
    svc_b = DataImportService()
    view = await svc_b.get_batch(state.batch_id, SESS)
    assert view.status.value == "completed"
    assert view.members[0].status.value == "completed"
    # 不产生第二个 job
    assert len(_job_ids(ops_env)) == 1


async def test_a13_partial_member_manifest(ops_env):
    """A13：成员 partial 有完整清单（job result_summary 成员投影）。"""
    from app.services.data_operations.import_jobs import TASK_TYPE
    from app.models.db_model import AnalysisTask

    # good.geojson 成功 + bad csv（编码错误）失败 → partial
    gbk_csv = make_csv_bytes(
        [["116.3", "39.8", "北京"]], ["lng", "lat", "城市"], encoding="gb18030")
    svc = DataImportService()
    state = await svc.create_batch(
        SESS, "user:g23",
        [("a.geojson", json.dumps(GEOJSON_A).encode()), ("bad.csv", gbk_csv)])
    keys = {m.display_name: m.member_key for m in state.members}
    await svc.save_plan(
        state.batch_id, SESS,
        {keys["a.geojson"]: {}, keys["bad.csv"]: {"encoding": "utf-8"}},
        selected=list(keys.values()))
    out = await svc.start_commit(state.batch_id, SESS, background=False)
    assert out["status"] == "partial"

    with ops_env["sync_factory"]() as db:
        row = (
            db.query(AnalysisTask)
            .filter(AnalysisTask.task_type == TASK_TYPE)
            .order_by(AnalysisTask.id.desc())
            .first()
        )
        summary = row.result_summary
        db.commit()
    assert summary["batch_status"] == "partial"
    members = {m["member_key"]: m for m in summary["members"]}
    assert set(members) == set(keys.values())
    good = members[keys["a.geojson"]]
    bad = members[keys["bad.csv"]]
    assert good["status"] == "completed" and good["output_ref"]
    assert bad["status"] == "failed" and bad["error"]
    # retained 披露：已发布产物明示保留
    assert good["output_ref"] in summary["retained_refs"]


async def test_source_files_never_modified(ops_env):
    """A16：源数据始终未改（staging 原件在提交前后字节一致）。"""
    from app.services.data_import import staging

    svc, state, keys = await _seed_batch(ops_env)
    src = state.sources[0]
    before = staging.source_path(state, src.source_id).read_bytes()
    await svc.start_commit(state.batch_id, SESS, background=False)
    after = staging.source_path(state, src.source_id).read_bytes()
    assert before == after
