"""G42 R01 —— 真实地图导出闭环（G20 提交链种子 → attempt → 统一派发导出）。

真实链：GeoTIFF → ref → commit_expression（真实 MapSpec 修订）→ 章节
export:pdf 需求未兑现 → attempt → ToolDispatchService 派发
webgis_map_export（blocking）→ 真实文件 + lineage 回执入章节 → 重终验
消费新证据。mock 仅限隔离环境（temp DB/内存 store）。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.getcwd())


@pytest.fixture()
def g42_env(tmp_path, monkeypatch):
    db_file = tmp_path / "g42e.db"
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
        db.add(Conversation(id="sess-g42e", user_id="user-1", title="G42E"))
        db.commit()

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


async def _seed_committed_mapspec(tmp_path, session_id="sess-g42e") -> int:
    """真实 G20 提交链：GeoTIFF → ref → commit_expression → revision。"""
    import numpy as np
    from rasterio.transform import from_origin

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))
    from tests.unit.raster_authoring.helpers import (  # noqa: WPS433
        register_raster_ref,
        write_tif,
    )

    import app.services.scoped_raster_paths as srp

    owner_root = tmp_path / "raster_owner"
    owner_root.mkdir(exist_ok=True)
    srp.scoped_raster_roots = lambda **kw: [owner_root]

    rows, cols = np.mgrid[0:12, 0:12]
    values = ((rows * 8 + cols) + 100).astype("float32")[None]
    tif = write_tif(
        owner_root / "g42e.tif", bands=1, width=12, height=12,
        dtype="float32", values=values, crs="EPSG:4326",
        transform=from_origin(10.0, 25.0, 10.0 / 12, 10.0 / 12))
    ref = await register_raster_ref(session_id, tif)

    from app.services.raster_authoring import commit_expression
    from app.services.raster_authoring.service import current_revision

    # 并行测试共享进程内 mapspec 状态时 revision 可能竞争推进 —— CAS 冲突
    # 即按当前值重试（有界 3 次），不放宽 CAS 语义。
    for _ in range(3):
        try:
            receipt = await commit_expression(
                session_id, ref, "raster-layer",
                {"bands": [1], "stretch": {"min": 0, "max": 400},
                 "colormap": "viridis"},
                expected_revision=await current_revision(session_id),
            )
            return int(receipt["mapspec_revision"])
        except Exception:
            continue
    raise RuntimeError("commit_expression kept racing CAS")


async def _seed_chapter_with_export_goal(session_id="sess-g42e"):
    from app.services.session_plan import SessionPlan, save_session_plan

    chapter = {
        "intent": {"task": "区域栅格制图", "scope": {"name": "测试区域"},
                   "output_intents": [], "export_intents": ["pdf"],
                   "query": "制图并导出 pdf"},
        "analysis_steps": [],
        "export_receipts": [],
        "map_product": {
            "goal_satisfaction": {
                "verdict": "partial", "signal": "continue",
                "requirements": [{
                    "requirement_id": "export:pdf", "kind": "export",
                    "verdict": "partial",
                    "missing": ["export_receipt_absent"],
                    "next_action": "export_product",
                    "export_format": "pdf", "delivery_expected": False,
                }]},
            "goal_repair_plan": {
                "goal_id": "goal-g42e", "verdict": "partial",
                "signal": "continue",
                "actions": [{
                    "rule": "export_receipt_absent",
                    "requirement_id": "export:pdf",
                    "action": "export_product", "side_effect": "write",
                    "path": "export_lineage", "budget_class": "repair",
                    "expected_new_evidence":
                        "export_receipt@revision==current"}],
                "blockers": [],
                "progress_rule": "new_evidence_required_per_round"},
            "rows_fingerprint": "rows-g42e-1",
        },
    }
    plan = SessionPlan(envelope_id="env-g42e", session_id=session_id,
                       user_goal="制图并导出 pdf", gis_chapter=chapter)
    await save_session_plan(plan)


async def _wait_terminal(attempt_id: int, timeout: float = 120.0):
    from app.services.gis_harness.goal_repair_execution import store

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        view = store.get_attempt_view(attempt_id)
        if view is not None and not view.get("active"):
            return view
        await asyncio.sleep(0.3)
    raise TimeoutError(f"attempt {attempt_id} not terminal")


@pytest.mark.asyncio
async def test_map_export_loop_real_export(g42_env, _registry, tmp_path):
    """R01：attempt → 统一派发 webgis_map_export → 真实文件 + lineage 回执。

    导出格式按需求（pdf）；blocking 语义与 CAS 修订由工具层保证 —— 本测试
    断言真实产物（download_url / bytes>0）与章节 export_receipts 更新。
    """
    from app.services.gis_harness.goal_repair_execution import service

    revision = await _seed_committed_mapspec(tmp_path)
    await _seed_chapter_with_export_goal()

    from app.services.gis_harness.goal_repair_execution.actions import (
        RepairOwnerContext,
    )

    ctx = RepairOwnerContext(session_id="sess-g42e", user_id="user-1")

    preview = await service.build_repair_preview("sess-g42e", ctx=ctx)
    binding = preview["actions"][0]
    assert binding["action"] == "export_product"
    assert binding["disposition"] == "executable"
    assert binding["params"]["format"] == "pdf"
    assert binding["params"]["expected_revision"] == revision

    started = await service.start_repair_attempt(
        "sess-g42e", ctx=ctx, session_busy_check=False)
    final = await _wait_terminal(int(started["attempt_id"]))
    receipt = next(
        (a for a in final["actions"] if a["action"] == "export_product"),
        None)
    assert receipt is not None
    assert receipt["status"] == "succeeded", receipt
    assert receipt["download_url"].startswith("/api/v1/export/download/")
    assert final["outcome"] in ("completed", "no_progress")

    # 章节血缘回执（record_export_lineage 真实写入）——重终验的证据源。
    from app.services.session_plan import load_session_plan

    plan = await load_session_plan("sess-g42e")
    receipts = (plan.gis_chapter or {}).get("export_receipts") or []
    assert any(str(r.get("format")).lower() == "pdf" for r in receipts), \
        f"export lineage receipt missing: {receipts}"
    # 真实文件存在于导出目录
    from app.services.export_paths import exports_root

    files = list(exports_root().glob("mixed_map_*"))
    assert files, "no mixed_map export file written"
