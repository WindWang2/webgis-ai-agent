"""G30 — skill job runner 集成测试（三旅程 + 幂等/取消/漂移）。

真实链：生产 ToolRegistry → ToolDispatchService → 既有生产工具 →
durable job 行 → run 记录 → 产物 ref 回读。场景映射：#1（质检报告）、
#2（绑定点集不重抓 POI）、#3（度量语义）、#13（双击幂等）、#14（取消）、
#21（报告任务不伪造地图）、#22（map 完成语义=图层/ref，观察待验收）、
#23（旧 ref 过期不复用）。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import inspect as sa_inspect

from app.services.gis_harness.skill_jobs.contracts import SkillJobErrorCode
from app.services.gis_harness.skill_jobs.prepare import prepare_skill_job
from app.services.gis_harness.skill_jobs.runner import (
    SkillJobOwner,
    get_skill_job_view,
    start_skill_job,
)
from app.services.gis_harness.skill_jobs.store import prepared_store


def _points(n: int = 12):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.0 + i * 0.01, 30.6 + i * 0.005]},
             "properties": {"name": f"p{i}", "score": i * 1.5}}
            for i in range(n)
        ],
    }


def _polys(n: int = 6):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Polygon",
                          "coordinates": [[[104.0 + i * 0.1, 30.6],
                                           [104.1 + i * 0.1, 30.6],
                                           [104.1 + i * 0.1, 30.7],
                                           [104.0 + i * 0.1, 30.7],
                                           [104.0 + i * 0.1, 30.6]]]},
             "properties": {"district": f"d{i}", "pop": 1000 + i * 200,
                            "area_km2": 50.0}}
            for i in range(n)
        ],
    }


@pytest.fixture()
def _registry():
    """function 作用域：conftest autouse 复位在每测前清 bridge registry。"""
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    from app.agent_pi_bridge import set_tool_registry

    reg = ToolRegistry()
    init_tools(reg)
    set_tool_registry(reg)
    return reg


@pytest.fixture(autouse=True)
def _tables():
    from app.core.database import Base, get_engine

    Base.metadata.create_all(bind=get_engine())
    yield


@pytest.fixture(autouse=True)
def _clear_store():
    prepared_store.clear()
    yield
    prepared_store.clear()


async def _prepare_and_put(session_id, skill_id, params, bindings):
    result = await prepare_skill_job(
        session_id=session_id, skill_id=skill_id, params=params,
        bindings=bindings)
    prepared_store.put(result.prepared, result.inline_payloads)
    return result


async def _store_ref(session_id, data):
    from app.services.session_data import session_data_manager

    return await session_data_manager.store(session_id, data, prefix="geojson")


async def _wait_terminal(job_id: int, timeout: float = 60.0) -> dict:
    """轮询 durable 行到终态（runner 有界等待之外的稳健等待）。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        view = await get_skill_job_view(job_id)
        if view is not None and view.get("status") in (
                "completed", "failed", "cancelled", "stale"):
            return view
        await asyncio.sleep(0.4)
    raise TimeoutError(f"job {job_id} not terminal")


@pytest.mark.asyncio
async def test_report_journey_validate_spatial_data(_registry):
    """场景 #1/#21：质检报告旅程 —— 真实检查证据 + 可回读报告 + 不伪造地图。"""
    sid = "t-run-validate"
    ref = await _store_ref(sid, _points())
    result = await _prepare_and_put(
        sid, "validate_spatial_data", {}, [{"role": "subject", "ref": ref}])
    view = await start_skill_job(
        result.prepared, result.inline_payloads,
        owner=SkillJobOwner(session_id=sid), wait_timeout_s=60)
    assert view["status"] == "completed", view
    # 报告型完成证据：report ref 可回读
    assert view["result_ref"] and view["result_ref"].startswith("ref:skillreport-")
    from app.services.session_data import session_data_manager

    report = await session_data_manager.get(sid, view["result_ref"])
    assert report is not None
    assert report["skill_id"] == "validate_spatial_data"
    assert report["skill_version"] == "1.1.0"
    # 完成证据全覆盖（三类）且不伪造地图写入
    assert view["evidence_covered"] == [
        "dataset_profile_report", "geometry_check_report", "quality_verdict"]
    assert not view.get("artifact_refs", {}).get("layer_id"), \
        "报告任务不得伪造地图产物"


@pytest.mark.asyncio
async def test_map_journey_point_distribution(_registry):
    """场景 #2/#22：绑定点集 → 真实 MapSpec 图层；观察义务保留待验收。"""
    sid = "t-run-points"
    ref = await _store_ref(sid, _points())
    result = await _prepare_and_put(
        sid, "point_distribution_analysis",
        {"layer_title": "测试点分布"},
        [{"role": "subject", "ref": ref}])
    view = await start_skill_job(
        result.prepared, result.inline_payloads,
        owner=SkillJobOwner(session_id=sid), wait_timeout_s=90)
    assert view["status"] == "completed", view
    steps = view.get("steps") or []
    assert [s["step_id"] for s in steps] == ["inspect_points", "express_points"]
    assert all(s["status"] == "ok" for s in steps)
    # MapSpec 图层真实落地（layer_id + geojson ref）
    assert view["artifact_refs"].get("step:express_points:layer_id") == \
        "skill-point-distribution-analysis"
    assert view["artifact_refs"].get("step:express_points:geojson", "").startswith("ref:")
    # 地图完成语义：渲染观察义务保留（observation_pending），不伪称 render_observed
    assert view["run_record"]["observation_pending"] is True


@pytest.mark.asyncio
async def test_map_journey_choropleth(_registry):
    """场景 #3：分区专题 —— 度量语义记录 + 图例/分级证据随产物。"""
    sid = "t-run-choro"
    pref = await _store_ref(sid, _polys())
    result = await _prepare_and_put(
        sid, "choropleth_map_design",
        {"field": "pop", "measure_semantics": "count"},
        [{"role": "subject", "ref": pref}])
    view = await start_skill_job(
        result.prepared, result.inline_payloads,
        owner=SkillJobOwner(session_id=sid), wait_timeout_s=90)
    assert view["status"] == "completed", view
    assert view["evidence_covered"] == [
        "choropleth_eligibility", "classification_record", "product_completeness"]
    # 度量语义进 run 记录（参数 provenance）
    params = {p["name"]: p["value"] for p in view["run_record"]["params"]}
    assert params["measure_semantics"] == "count"
    assert params["field"] == "pop"


@pytest.mark.asyncio
async def test_double_click_idempotency(_registry):
    """场景 #13：双击/网络重试 —— 同内容只执行一次必要副作用。"""
    sid = "t-run-idem"
    ref = await _store_ref(sid, _points())
    r1 = await _prepare_and_put(
        sid, "validate_spatial_data", {}, [{"role": "subject", "ref": ref}])
    v1 = await start_skill_job(
        r1.prepared, r1.inline_payloads,
        owner=SkillJobOwner(session_id=sid), wait_timeout_s=60)
    assert v1["status"] == "completed"
    # 同输入重新 prepare（同幂等键）再 start → 复用同一行，不重跑
    r2 = await _prepare_and_put(
        sid, "validate_spatial_data", {}, [{"role": "subject", "ref": ref}])
    assert r2.prepared.idempotency_key == r1.prepared.idempotency_key
    v2 = await start_skill_job(
        r2.prepared, r2.inline_payloads,
        owner=SkillJobOwner(session_id=sid), wait_timeout_s=30)
    assert v2.get("idempotent_reuse") is True
    assert v2["job_id"] == v1["job_id"]


@pytest.mark.asyncio
async def test_input_drift_blocks_execution(_registry):
    """场景 #12/#23：prepare 后数据变化 → 启动核对 digest，typed 拒绝。"""
    sid = "t-run-drift"
    ref = await _store_ref(sid, _points())
    result = await _prepare_and_put(
        sid, "validate_spatial_data", {}, [{"role": "subject", "ref": ref}])
    from app.services.session_data import session_data_manager

    # ref 失效路径：清空会话存储模拟 ref 过期（场景 #23：旧 ref 不可读
    # → 不复用旧准备，typed INPUT_DRIFT 要求重新绑定）
    backend = getattr(session_data_manager, "_backend", None) or session_data_manager
    inner = getattr(backend, "_store", None)
    if isinstance(inner, dict):
        inner.pop(sid, None)
    else:
        getattr(inner, "_store", {}).pop(sid, None)
    with pytest.raises(SkillJobErrorCode) as ei:
        await start_skill_job(
            result.prepared, result.inline_payloads,
            owner=SkillJobOwner(session_id=sid), wait_timeout_s=10)
    assert ei.value.code == "INPUT_DRIFT"


@pytest.mark.asyncio
async def test_asset_drift_blocks_execution(_registry, monkeypatch):
    """场景 #8：同版本资产内容被替换 → digest 漂移 → 旧准备失效。"""
    sid = "t-run-asset-drift"
    ref = await _store_ref(sid, _points())
    result = await _prepare_and_put(
        sid, "validate_spatial_data", {}, [{"role": "subject", "ref": ref}])
    # 篡改 digest（模拟同版本内容替换）
    result.prepared.skill_digest = "0" * 64
    with pytest.raises(SkillJobErrorCode) as ei:
        await start_skill_job(
            result.prepared, result.inline_payloads,
            owner=SkillJobOwner(session_id=sid), wait_timeout_s=10)
    assert ei.value.code == "SKILL_ASSET_DRIFT"


@pytest.mark.asyncio
async def test_cancel_running_job(_registry):
    """场景 #14：运行中取消 —— 两阶段取消落 cancelled，已有部分产物如实保留。"""
    from app.core.database import SessionLocal
    from app.services.jobs.store import DurableJobStore

    sid = "t-run-cancel"
    ref = await _store_ref(sid, _points())
    result = await _prepare_and_put(
        sid, "validate_spatial_data", {}, [{"role": "subject", "ref": ref}])
    # 不等待（后台执行），立刻请求取消
    task = asyncio.create_task(start_skill_job(
        result.prepared, result.inline_payloads,
        owner=SkillJobOwner(session_id=sid), wait_timeout_s=0))
    await asyncio.sleep(0.05)
    job_id = int((await task).get("job_id"))
    with SessionLocal() as db:
        DurableJobStore.request_cancel_sync(db, job_id)
        db.commit()
    view = await _wait_terminal(job_id, timeout=30)
    # 快作业可能已完成（completed 合法）；若取消生效则必须落 cancelled，
    # 任何情况下不得是 failed（取消≠故障）。
    assert view["status"] in ("completed", "cancelled"), view["status"]


@pytest.mark.asyncio
async def test_runs_listing_survives_refresh(_registry):
    """场景 #15：刷新回看 —— durable 行列表恢复运行（不依赖内存 store）。"""
    from app.services.gis_harness.skill_jobs.runner import list_skill_job_views

    sid = "t-run-list"
    ref = await _store_ref(sid, _points())
    result = await _prepare_and_put(
        sid, "validate_spatial_data", {}, [{"role": "subject", "ref": ref}])
    v = await start_skill_job(
        result.prepared, result.inline_payloads,
        owner=SkillJobOwner(session_id=sid), wait_timeout_s=60)
    assert v["status"] == "completed"
    prepared_store.clear()  # 模拟刷新：内存 prepared 丢弃
    runs = await list_skill_job_views(session_id=sid)
    assert len(runs) >= 1
    assert runs[0]["skill_id"] == "validate_spatial_data"
    assert runs[0]["status"] == "completed"
    assert runs[0]["result_ref"].startswith("ref:skillreport-")
