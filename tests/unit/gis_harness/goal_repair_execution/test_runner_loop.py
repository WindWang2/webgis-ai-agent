"""G42 runner 集成测试 —— 真实交付闭环 + 取消 + waiting_delivery 对账。

真实链：SessionPlan/章节种子 → service.start（幂等 attempt）→ runner →
统一派发 submit_data_delivery → G18 真实服务（预览复检/损失确认/durable
job/写盘）→ 回执 → 重终验触发。mock 仅限隔离环境（temp DB/内存 store）。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from typing import Any, Dict

import pytest

sys.path.insert(0, os.getcwd())


@pytest.fixture()
def g42_env(tmp_path, monkeypatch):
    """隔离环境：temp DB + 内存 session store + temp DATA_DIR。"""
    db_file = tmp_path / "g42.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("USE_REDIS", "false")
    monkeypatch.setenv("CELERY_BROKER_URL", "memory://")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "cache+memory://")

    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import sessionmaker

    import app.core.database as db_mod
    from app.core.config import settings
    from app.models.db_model import Base

    sync_engine = create_engine(
        f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine)
    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file.as_posix()}")
    async_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    sync_factory = sessionmaker(bind=sync_engine)

    @contextlib.asynccontextmanager
    async def fake_async_db():
        async with async_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(db_mod, "async_db_session", fake_async_db)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", async_factory)
    monkeypatch.setattr(db_mod, "SessionLocal", sync_factory)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", str(data_dir))

    from app.models.db_model import Conversation

    with sync_factory() as db:
        db.add(Conversation(id="sess-g42", user_id="user-1", title="G42"))
        db.commit()

    from app.services.data_delivery import service as dd_service

    dd_service._ACTIVE_RUNNERS.clear()
    yield {"sync_factory": sync_factory, "data_dir": data_dir}
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(async_engine.dispose())
        else:
            loop.run_until_complete(async_engine.dispose())
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture()
def _registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    from app.agent_pi_bridge import set_tool_registry

    reg = ToolRegistry()
    init_tools(reg)
    set_tool_registry(reg)
    return reg


FEATURES = [
    {"type": "Feature", "id": "f1",
     "geometry": {"type": "Point", "coordinates": [116.397, 39.916]},
     "properties": {"name": "北京", "pop": 2154,
                    "very_long_field_name_exceeding_ten_chars": 1}},
    {"type": "Feature", "id": "f2",
     "geometry": {"type": "Point", "coordinates": [116.407, 39.926]},
     "properties": {"name": "东城", "pop": 709,
                    "very_long_field_name_exceeding_ten_chars": 2}},
]


_SESSION_SEQ = {"n": 0}


def _fresh_session() -> str:
    _SESSION_SEQ["n"] += 1
    return f"sess-g42-{_SESSION_SEQ['n']}"


async def _seed_session(session_id: str = "sess-g42") -> str:
    """会话（含 Conversation 行）+ 章节（export:gpkg 需求未兑现）+ ref。"""
    import contextlib

    from app.core.database import SessionLocal
    from app.models.db_model import Conversation
    from app.services.session_data import session_data_manager
    from app.services.session_plan import save_session_plan, SessionPlan

    with contextlib.closing(SessionLocal()) as db:
        if db.get(Conversation, session_id) is None:
            db.add(Conversation(id=session_id, user_id="user-1",
                                title="G42"))
            db.commit()

    fc = {"type": "FeatureCollection", "features": FEATURES,
          "properties": {"total_count": 2, "truncated": False,
                         "schema_info": {"crs": "EPSG:4326"}}}
    ref = await session_data_manager.store(session_id, fc, prefix="geojson")
    chapter: Dict[str, Any] = {
        "intent": {"task": "数据交付", "scope": {"name": "北京"},
                   "output_intents": [], "export_intents": ["shapefile_zip"],
                   "query": "交付 shapefile_zip"},
        "analysis_steps": [{"capability": "buffer", "status": "done",
                            "bound_ref": ref,
                            "purpose": "缓冲区分析"}],
        "export_receipts": [],
        "map_product": {
            "goal_satisfaction": {
                "verdict": "partial", "signal": "continue",
                "requirements": [{
                    "requirement_id": "export:shapefile_zip", "kind": "export",
                    "verdict": "partial",
                    "missing": ["delivery_receipt_absent"],
                    "next_action": "run_delivery",
                    "export_format": "shapefile_zip", "delivery_expected": True,
                }]},
            "goal_repair_plan": {
                "goal_id": "goal-g42", "verdict": "partial",
                "signal": "continue",
                "actions": [{
                    "rule": "delivery_receipt_absent",
                    "requirement_id": "export:shapefile_zip",
                    "action": "run_delivery", "side_effect": "write",
                    "path": "data_delivery.service", "budget_class": "repair",
                    "expected_new_evidence":
                        "delivery_receipt@status==completed"}],
                "blockers": [],
                "progress_rule": "new_evidence_required_per_round"},
            "rows_fingerprint": "rows-g42-1",
        },
    }
    plan = SessionPlan(envelope_id="env-g42", session_id=session_id,
                       user_goal="交付 shapefile_zip", gis_chapter=chapter)
    await save_session_plan(plan)
    return ref


async def _wait_terminal(attempt_id: int, timeout: float = 90.0) -> Dict[str, Any]:
    from app.services.gis_harness.goal_repair_execution import store

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        view = store.get_attempt_view(attempt_id)
        if view is not None and not view.get("active"):
            return view
        await asyncio.sleep(0.3)
    raise TimeoutError(f"attempt {attempt_id} not terminal")


def _ctx(sid: str = "sess-g42"):
    from app.services.gis_harness.goal_repair_execution.actions import (
        RepairOwnerContext,
    )

    return RepairOwnerContext(session_id=sid, user_id="user-1")


@pytest.mark.asyncio
async def test_delivery_loop_real_services(g42_env, _registry):
    """R02 链：预览（真实 ack 词表）→ start → 派发 → G18 真实交付 → 回执。

    损失确认缺席时零提交（先断言），带确认后真实完成并回读产物事实。
    """
    from app.services.gis_harness.goal_repair_execution import service

    sid = _fresh_session()
    await _seed_session(sid)

    preview = await service.build_repair_preview(sid, ctx=_ctx(sid))
    binding = preview["actions"][0]
    assert binding["action"] == "run_delivery"
    ack_codes = list(binding.get("ack_codes") or [])
    assert ack_codes, "shapefile_zip + 长字段名必须披露损失确认义务"

    # 无确认启动 → 动作时预览复检阻断（零提交），attempt 如实收口。
    started = await service.start_repair_attempt(
        sid, acknowledged_losses=[], ctx=_ctx(sid),
        session_busy_check=False)
    view = await _wait_terminal(int(started["attempt_id"]))
    ack_action = next(
        (a for a in view["actions"] if a["action"] == "run_delivery"), None)
    assert ack_action is not None
    # gpkg 对该源可能有损失确认（嵌套/几何）—— 缺席必须阻断或需确认，
    # 绝不出现「已提交 job」的假成功。
    assert ack_action["status"] in ("needs_acknowledgment", "blocked",
                                    "failed"), ack_action
    if ack_action["status"] == "needs_acknowledgment":
        assert not ack_action["job_id"], "损失确认缺席时零提交"

    # 带确认启动（预览披露的全部义务）→ 真实交付。
    started2 = await service.start_repair_attempt(
        sid, acknowledged_losses=ack_codes, ctx=_ctx(sid),
        session_busy_check=False)
    view2 = await _wait_terminal(int(started2["attempt_id"]))
    # 真实 finalizer 再生成的计划动作名可能是 export_product（同规则）——
    # 执行入口由绑定裁决（fmt=shapefile_zip → submit_data_delivery）。
    done = next(
        (a for a in view2["actions"]
         if a["status"] == "succeeded" and a["job_id"]), None)
    assert done is not None, view2["actions"]
    assert done["job_id"]
    assert view2["outcome"] == "completed"
    assert view2["final_verdict_before"] == "partial"

    # 交付产物可经既有服务受权回读（真实文件）。
    from app.services.data_delivery import get_delivery_job
    from app.services.data_delivery.source_resolver import DeliveryActor

    job = await get_delivery_job(
        done["job_id"], DeliveryActor(user_id="user-1"))
    receipt = getattr(job, "receipt", None)
    assert receipt is not None, "交付 job 完成后必须有回执"
    assert int(getattr(receipt, "row_count", 0)) == 2

    # R02/R05 收口：真实交付回执进入证据面 → 原终验翻转为 satisfied
    # （verdict 只由既有 evaluator 判定；此后 preview 诚实拒绝 —— 无待补全）。
    from app.services.gis_harness.goal_repair_execution.contracts import (
        GoalRepairErrorCode,
    )
    with pytest.raises(GoalRepairErrorCode) as ei3:
        await service.build_repair_preview(sid, ctx=_ctx(sid))
    assert ei3.value.code == "GOAL_SATISFIED"
    from app.services.session_plan import load_session_plan as _lsp
    plan_after = await _lsp(sid)
    gs_after = ((plan_after.gis_chapter or {}).get("map_product")
                or {}).get("goal_satisfaction") or {}
    assert str(gs_after.get("verdict")) == "satisfied"


@pytest.mark.asyncio
async def test_cancel_skips_unstarted_actions(g42_env, _registry, monkeypatch):
    """R24：取消后未提交动作不再启动；已积累回执可回看。"""
    from app.services.gis_harness.goal_repair_execution import service, store

    sid = _fresh_session()
    await _seed_session(sid)

    started = False

    async def slow_delivery(ctx, binding, *, attempt_id, acked_losses,
                            filename_hint="", check_cancel=None):
        nonlocal started
        started = True
        # 与生产执行体同纪律：等待期经 checkpoint 协作取消。
        for _ in range(150):
            if check_cancel is not None:
                check_cancel()
            await asyncio.sleep(0.2)

    monkeypatch.setattr(
        "app.services.gis_harness.goal_repair_execution.runner."
        "execute_delivery", slow_delivery)

    view = await service.start_repair_attempt(
        sid, acknowledged_losses=[], ctx=_ctx(sid),
        session_busy_check=False)
    attempt_id = int(view["attempt_id"])
    # 等执行体真正进入动作（bounded）
    deadline = asyncio.get_event_loop().time() + 10
    while not started and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
    assert started

    await service.cancel_repair_attempt(attempt_id, ctx=_ctx(sid))
    final = await _wait_terminal(attempt_id)
    assert final["status"] == "cancelled"
    assert final["outcome"] == "cancelled"
    summary = store.read_summary(attempt_id)
    assert summary.get("cancelled_partial") is True
    assert summary.get("actions"), "取消后已积累回执必须保留可回看"


@pytest.mark.asyncio
async def test_superseded_goal_marks_attempt_stale(g42_env, _registry,
                                                   monkeypatch):
    """R12：执行中目标被替换 → 剩余动作 stale，attempt superseded。"""
    from app.services.gis_harness.goal_repair_execution import service
    from app.services.session_plan import load_session_plan, save_session_plan

    sid = _fresh_session()
    await _seed_session(sid)
    entered = asyncio.Event()

    async def slow_then_ok_delivery(ctx, binding, *, attempt_id,
                                    acked_losses, filename_hint="",
                                    check_cancel=None):
        entered.set()
        # 给测试留出窗口在动作 1 与动作 2 之间替换目标。
        await asyncio.sleep(0.5)
        return {"action_id": binding.get("action_id"),
                "action": "run_delivery",
                "requirement_id": binding.get("requirement_id"),
                "status": "succeeded", "detail": "", "evidence_ref": "",
                "download_url": "", "job_id": "", "error_code": ""}

    monkeypatch.setattr(
        "app.services.gis_harness.goal_repair_execution.runner."
        "execute_delivery", slow_then_ok_delivery)

    # 计划加第二个动作（reobserve）以构造「替换发生在下一步之前」。
    plan = await load_session_plan(sid)
    plan.gis_chapter["map_product"]["goal_repair_plan"]["actions"].append({
        "rule": "map_render_stale", "requirement_id": "map",
        "action": "reobserve", "side_effect": "read_only",
        "path": "render_observation", "budget_class": "repair",
        "expected_new_evidence": "render_observation@revision>current"})
    await save_session_plan(plan)

    view = await service.start_repair_attempt(
        sid, acknowledged_losses=[], ctx=_ctx(sid),
        session_busy_check=False)
    await entered.wait()
    # 替换目标（intent.task 变化 → goal_key 变化）。
    plan2 = await load_session_plan(sid)
    plan2.gis_chapter["intent"]["task"] = "另一个任务"
    await save_session_plan(plan2)
    final = await _wait_terminal(int(view["attempt_id"]))
    assert final["outcome"] == "superseded"
    statuses = {a["action"]: a["status"] for a in final["actions"]}
    assert statuses.get("reobserve") == "stale"
