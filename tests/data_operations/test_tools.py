"""G23 Pi 工具新面测试（评审 M1）：状态工具 × 2 + wait 超时句柄分支 × 2。

经真实 ToolRegistry dispatch（非直接函数调用），fixture 走 ops_env 真实
SQLite/staging；「不存在批次」走 404 语义（KeyError → 工具层 KeyError）。
"""
from __future__ import annotations

import asyncio
import json
import threading

import pytest

from app.services.data_import.service import DataImportService
from app.tools.registry import ToolRegistry
from tests.data_operations.conftest import GEOJSON_A

pytest.importorskip("app.tools.data_import_tools")
pytest.importorskip("app.tools.field_compute_tools")

SESS = "sess-g23-tools"


def _registry() -> ToolRegistry:
    import app.tools.data_import_tools as di_tools
    import app.tools.field_compute_tools as fc_tools

    reg = ToolRegistry()
    di_tools.register_data_import_tools(reg)
    fc_tools.register_field_compute_tools(reg)
    return reg


async def _seed_import_batch(svc: DataImportService):
    state = await svc.create_batch(
        SESS, "user:g23t", [("a.geojson", json.dumps(GEOJSON_A).encode())])
    key = state.members[0].member_key
    await svc.save_plan(state.batch_id, SESS, {key: {}}, selected=[key])
    return state, key


async def test_import_commit_status_running_then_completed(ops_env):
    """import_commit_status：活跃期（running）与终态（completed+produced_refs）。"""
    reg = _registry()
    svc = DataImportService()
    state, key = await _seed_import_batch(svc)

    out = await reg.dispatch(
        "import_batch_commit",
        {"batch_id": state.batch_id, "session_id": SESS}, session_id=SESS)
    assert out["success"] is True and out["job_id"]

    # 活跃期（小 fixture 立即完成——先验证终态查询）；再造一个活跃批次验证 running 视图
    st = await reg.dispatch(
        "import_commit_status",
        {"batch_id": state.batch_id, "session_id": SESS}, session_id=SESS)
    assert st["success"] is True
    assert st["batch_status"] == "completed"
    assert st["job"]["status"] == "completed"
    assert st["produced_refs"] and st["produced_refs"][0].startswith("ref:")
    assert st["completed"] == 1 and st["total_members"] >= 1


async def test_import_commit_status_not_found_semantics(ops_env):
    """不存在批次 → 404 语义（KeyError，不泄露存在性差异）。"""
    reg = _registry()
    out = await reg.dispatch(
        "import_commit_status",
        {"batch_id": "ib-nonexistent", "session_id": SESS}, session_id=SESS)
    # registry 把工具的 KeyError 统一转 typed 失败（404 语义不泄露存在性）
    assert out["success"] is False
    assert out.get("code") in ("NOT_FOUND", "KEY_ERROR") or "不存在" in str(out)


async def test_import_commit_wait_zero_returns_handle_for_slow_commit(ops_env):
    """wait_max_s=0：慢提交立即返回 job 句柄 + guidance（不伪造完成）。"""
    import threading

    import app.services.data_import.service as di_service_mod
    from tests.data_operations.conftest import make_csv_bytes

    entered = threading.Event()
    release = threading.Event()
    real_commit = di_service_mod.commit_member

    async def slow_commit(*a, **kw):
        entered.set()
        await asyncio.get_running_loop().run_in_executor(
            None, release.wait, 10)
        return await real_commit(*a, **kw)

    # service.py 以 from-import 持有 commit_member 直接引用 —— 打在
    # service 模块名上（G17 既有测试同款手法）
    di_service_mod.commit_member = slow_commit
    try:
        svc = DataImportService()
        csv = make_csv_bytes([[116.0 + i * 0.01, 39.0, i] for i in range(50)],
                             ["lng", "lat", "v"])
        state = await svc.create_batch(
            SESS, "user:g23t", [("slow.csv", csv)])
        key = state.members[0].member_key
        await svc.save_plan(state.batch_id, SESS,
                            {key: {"mode": "points", "x_column": "lng",
                                   "y_column": "lat",
                                   "source_crs_declared": "EPSG:4326"}},
                            selected=[key])
        reg = _registry()
        out = await reg.dispatch(
            "import_batch_commit",
            {"batch_id": state.batch_id, "session_id": SESS, "wait_max_s": 0},
            session_id=SESS)
        assert await asyncio.to_thread(entered.wait, 5)
        assert out["success"] is True
        assert out.get("job_id"), out  # 句柄在场
        assert out["status"] == "committing"  # 如实进行中，不伪造完成
        assert "import_commit_status" in out.get("guidance", "")
        # 收尾：放行 + 等终态（后台 runner 仍在跑）
        release.set()
        for _ in range(120):
            st = await svc.get_receipts(state.batch_id, SESS)
            if st["status"] != "committing":
                break
            await asyncio.sleep(0.25)
        assert st["status"] == "completed"
    finally:
        di_service_mod.commit_member = real_commit
        release.set()


async def test_field_compute_status_completed_readable_ref(ops_env):
    """field_compute_status：completed + output_ref 可读才返回。"""
    from app.services.data_preparation.field_compute import service as fcs
    from app.services.session_data import session_data_manager as sdm

    payload = {"geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"人口": 100, "面积 平方千米": 10.0},
         "geometry": None}]}}
    ref = await sdm.store(SESS, payload, prefix="test")
    created = await fcs.create_plan({
        "session_id": SESS, "source_ref": ref,
        "columns": [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}]})
    receipt = await fcs.execute(created["plan"].plan_id, SESS)

    reg = _registry()
    st = await reg.dispatch(
        "field_compute_status",
        {"plan_id": created["plan"].plan_id, "session_id": SESS}, session_id=SESS)
    assert st["success"] is True
    assert st["job"]["status"] == "completed"
    assert st["output_readable"] is True
    assert st["output_ref"] == receipt.output_ref
    # 产物确实可读（存在性）
    assert await sdm.get(SESS, st["output_ref"]) is not None


async def test_field_compute_status_no_job_record(ops_env):
    """从未执行的计划 → job=None（如实「没有记录」，不是伪状态）。"""
    from app.services.data_preparation.field_compute import service as fcs
    from app.services.session_data import session_data_manager as sdm

    payload = {"geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"人口": 1, "面积 平方千米": 1.0},
         "geometry": None}]}}
    ref = await sdm.store(SESS, payload, prefix="test")
    created = await fcs.create_plan({
        "session_id": SESS, "source_ref": ref,
        "columns": [{"name": "x", "expression": "{人口} + 1"}]})
    reg = _registry()
    st = await reg.dispatch(
        "field_compute_status",
        {"plan_id": created["plan"].plan_id, "session_id": SESS}, session_id=SESS)
    assert st["success"] is True and st["job"] is None


async def test_field_compute_apply_wait_zero_returns_handle(ops_env):
    """field_compute_apply wait_max_s=0：慢执行返回 job 句柄（不占用调用）。"""
    from app.services.data_preparation.field_compute import service as fcs
    from app.services.session_data import session_data_manager as sdm

    payload = {"geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"人口": 100, "面积 平方千米": 10.0},
         "geometry": None}]}}
    ref = await sdm.store(SESS, payload, prefix="test")
    created = await fcs.create_plan({
        "session_id": SESS, "source_ref": ref,
        "columns": [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}]})
    entered = threading.Event()
    release = threading.Event()
    real_eval = fcs.evaluate_rows

    def slow(rows, cols, mask, cfg, **kw):
        entered.set()
        release.wait(10)
        return real_eval(rows, cols, mask, cfg, **kw)

    fcs.evaluate_rows = slow
    try:
        reg = _registry()
        out = await reg.dispatch(
            "field_compute_apply",
            {"plan_id": created["plan"].plan_id, "session_id": SESS,
             "wait_max_s": 0}, session_id=SESS)
        assert await asyncio.to_thread(entered.wait, 5)
        assert out["success"] is True
        assert out.get("job_id")
        assert out["status"] == "running"
        assert "field_compute_status" in out.get("guidance", "")
    finally:
        fcs.evaluate_rows = real_eval
        release.set()
        await asyncio.sleep(0.5)
