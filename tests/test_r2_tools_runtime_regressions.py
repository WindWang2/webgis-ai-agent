"""R2/R1 收尾回归（#2147/#2148/#2149/#2154/#2219/#2220/#2221/#2204 工具与运行时）。"""
from __future__ import annotations

import asyncio
import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ── #2148 read_coverage_subset：SSRF/参数异常必须走类型化回执 ─────────────

async def test_2148_read_coverage_subset_ssrf_blocked_is_typed():
    from app.tools.ogc_service_tools import register_ogc_service_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_ogc_service_tools(reg)
    fn = reg._tools["read_coverage_subset"]
    # 内网地址触发 SSRF validate_url（DataFabricError 子类）
    out = await fn(
        url="http://169.254.169.254/latest/meta-data",
        dataset_id="cov1",
        session_id="s-2148",
    )
    assert out["status"] == "error"
    assert out.get("error_type"), f"裸异常逃出类型化回执: {out}"
    assert out.get("ref") is None


async def test_2148_read_coverage_subset_scalar_subset_no_typeerror():
    from app.tools.ogc_service_tools import register_ogc_service_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_ogc_service_tools(reg)
    fn = reg._tools["read_coverage_subset"]
    # subsets 标量值旧实现 {k: tuple(v)} 直接 TypeError 裸穿
    out = await fn(
        url="https://example.com/cov",
        dataset_id="cov1",
        session_id="s-2148",
        subsets={"lat": 30.0},
    )
    assert isinstance(out, dict)
    assert "TypeError" not in str(out.get("error_type", ""))


# ── #2147 inspect_data_service：同步远程 HTTP 不得跑在事件循环上 ───────────

async def test_2147_inspect_data_service_offloaded_from_event_loop():
    from app.tools.ogc_service_tools import register_ogc_service_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_ogc_service_tools(reg)
    meta = reg._metadata["inspect_data_service"]
    from app.tools.descriptor import ToolExecutionPolicy
    assert meta.get("execution_policy") != ToolExecutionPolicy.ASYNC, (
        "inspect_data_service 仍按 ASYNC 策略在事件循环上串行同步 HTTP（#2147）"
    )


# ── #2149 Celery 路径：外层取消必须 revoke 已投递任务 ─────────────────────

async def test_2149_celery_outer_cancel_revokes_task(monkeypatch):
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register("t2149_probe", "probe", lambda: {"ok": True})

    revoked: list = []
    blocker = threading.Event()

    class FakeTask:
        id = "fake-task-2149"

        def get(self, timeout=None):
            blocker.wait(60)

        def revoke(self, terminate=False):
            revoked.append(terminate)

    class FakeIsolated:
        @staticmethod
        def apply_async(**kwargs):
            return FakeTask()

    fake_mod = types.ModuleType("app.services.spatial_tasks")
    fake_mod.run_sync_tool_isolated = FakeIsolated()
    monkeypatch.setitem(sys.modules, "app.services.spatial_tasks", fake_mod)
    monkeypatch.setattr("app.core.config.settings.USE_REDIS", True, raising=False)

    dispatch_task = asyncio.create_task(reg.dispatch("t2149_probe", {}))
    await asyncio.sleep(0.3)
    dispatch_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dispatch_task
    blocker.set()
    assert revoked == [True], "外层取消未 revoke 已投递 Celery 任务（#2149）"


# ── #2154 get_execution_run：跨租户 run_id 不得读取他人证据 ────────────────

async def test_2154_get_execution_run_enforces_owner_scope():
    import app.tools.geocompute_tools as gt
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    gt.register_geocompute_tools(reg)
    fn = reg._tools["get_execution_run"]

    calls: list = []

    class FakeEngine:
        def get_run(self, run_id, owner_scope=None):
            calls.append(owner_scope)
            return None

    fake_engine = FakeEngine()
    with patch.object(gt, "db_session", MagicMock()), \
         patch("app.services.geocompute.executor.engine", fake_engine, create=True):
        out = fn(run_id="gexec-deadbeef")
    assert out.get("status") == "not_found"
    assert calls and calls[0] is not None, (
        "get_execution_run 未传 owner_scope，跨租户 run_id 可读取证据（#2154）"
    )


# ── #2219 webgis_symbol_layer_inspect：args_model 与函数签名一致 ───────────

async def test_2219_symbol_layer_inspect_callable_without_plan():
    from app.tools.cartographic_asset_tools import register_cartographic_asset_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_cartographic_asset_tools(reg)
    # 不带 plan（原注册要求必填 plan → 两头都死）
    result = await reg.dispatch(
        "webgis_symbol_layer_inspect",
        {"layer_id": "lyr-1", "session_id": "s-2219"},
    )
    text = str(result)
    assert "VALIDATION_ERROR" not in text or "SOURCE_UNAVAILABLE" in text or result.get("success") is False
    # 关键：不再出现参数绑定死（unexpected keyword argument 'plan' / Field required）
    assert "unexpected keyword argument" not in text
    assert "Field required" not in text


async def test_2219_symbol_layer_inspect_with_plan_still_works():
    from app.tools.cartographic_asset_tools import register_cartographic_asset_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_cartographic_asset_tools(reg)
    result = await reg.dispatch(
        "webgis_symbol_layer_inspect",
        {"layer_id": "lyr-1", "session_id": "s-2219", "plan": {}},
    )
    text = str(result)
    assert "unexpected keyword argument" not in text
    assert "Field required" not in text


# ── #2221 raster_sampling store：并发 CAS 必须一胜四冲突 ───────────────────

def _make_plan(plan_id: str, session_id: str, revision: int):
    from app.services.raster_sampling.contracts import (
        SamplingPlan, SourceBinding, StationSpec,
    )

    return SamplingPlan(
        plan_id=plan_id, session_id=session_id, revision=revision,
        mode="points",
        station_spec=StationSpec(mode="points"),
        sources=[SourceBinding(ref_id="ref:test/1", data_revision="r1")],
    )


@pytest.mark.asyncio
async def test_2221_concurrent_cas_single_winner(tmp_path, monkeypatch):
    import app.services.raster_sampling.store as store

    monkeypatch.setattr("app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path)

    async def _seed():
        plan = _make_plan("p1", "sess-2221", 1)
        ok = await store.save_plan(plan)
        assert ok

    await _seed()

    async def _cas(_i: int):
        plan = _make_plan("p1", "sess-2221", 2)
        try:
            await store.save_plan_cas(plan, expected_revision=1)
            return "win"
        except Exception as e:  # noqa: BLE001
            return type(e).__name__

    results = await asyncio.gather(*[_cas(i) for i in range(5)])
    wins = sum(1 for r in results if r == "win")
    conflicts = sum(1 for r in results if r == "RasterSamplingError")
    assert wins == 1, f"并发 CAS 应恰好一胜: {results}"
    assert conflicts == 4, f"并发 CAS 应四冲突: {results}"

    final = await store.get_plan("sess-2221", "p1")
    assert final.revision == 2


@pytest.mark.asyncio
async def test_2221_concurrent_append_receipts_no_loss(tmp_path, monkeypatch):
    import app.services.raster_sampling.store as store
    from app.services.raster_sampling.contracts import ExecutionReceipt

    monkeypatch.setattr("app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path)

    async def _append(i: int):
        receipt = ExecutionReceipt(
            execution_id=f"exec-{i}", plan_id="p1", plan_revision=1,
            session_id="sess-2221b", params_digest=f"d{i}",
            algorithm_version="v1", method="source_pixel", mode="points",
            station_count=1,
        )
        return await store.append_receipt(receipt, published=False)

    results = await asyncio.gather(*[_append(i) for i in range(8)])
    assert all(results)
    receipts = await store.list_receipts("sess-2221b")
    ids = {r.execution_id for r in receipts}
    assert len(ids) == 8, f"并发 append 丢收据: {sorted(ids)}"


# ── #2220 reader_schedule：越界批/bilinear 行列/窗口偏移 ───────────────────

def test_2220_read_window_degenerate_batch_returns_empty_not_valueerror():
    """批内站位全部越界 → 空窗口读（下游逐站位标 outside_extent），
    而不是把负行列的 Window 传给 rasterio 抛 ValueError。"""
    from app.services.raster_sampling import reader_schedule as rs

    grid = MagicMock()
    grid.height = 100
    grid.width = 100
    grid.itemsize_est = 8
    grid.block_shape = (16, 16)

    ds = MagicMock()
    # 紧贴底边外的 2×2 批（2 行 × 2 列 × 1 波段 ≈ 32B，远低于预算闸，
    # 直接命中 clamp 后负窗口的 ValueError 路径）
    data, valid, window, masks = rs._read_window(
        ds, [1], 100, 101, 0, 1, grid, rs.ReadMetrics(),
    )
    assert data == {} and valid == {}
    ds.read.assert_not_called()


def test_2220_station_rows_bilinear_row_col_and_support_offset():
    """bilinear：SampleRow.row/col 不得行列互换；support_pixels 必须加回
    窗口 (r0,c0) 偏移（审计实证：expected row=5,col=7 → 旧实现 row=7,col=5，
    supports 全是窗口局部 (0,0)..）。"""
    from app.services.raster_sampling import reader_schedule as rs
    from app.services.raster_sampling.contracts import METHOD_BILINEAR
    from app.services.raster_sampling.kernel import BandFacts
    from app.services.raster_sampling.stations import Station

    facts = BandFacts(index=1, label="b1", unit="m")
    window_data = np.arange(16, dtype=float).reshape(4, 4)
    window_valid = np.ones((4, 4), dtype=bool)

    grid = MagicMock()
    grid.transform_failed = None
    grid.binding.bands = [1]
    grid.band_facts = {1: facts}
    # 站位 (col_f=7.5, row_f=5.5)，窗口 (r0..r1)=(4..5), (c0..c1)=(6..7)
    grid.grid_cols = [7.5]
    grid.grid_rows = [5.5]

    station = Station(sample_id="s1", station_index=0, x=104.0, y=30.0)
    rows = rs._station_rows(
        station, grid, 0, METHOD_BILINEAR, "EPSG:4326",
        {1: window_data}, {1: window_valid}, (4, 5, 6, 7),
    )
    assert len(rows) == 1
    row = rows[0]
    assert (row.row, row.col) == (5, 7), f"行列互换: row={row.row} col={row.col}"
    supports = {(int(s["row"]), int(s["col"])) for s in row.support_pixels}
    assert supports == {(4, 6), (4, 7), (5, 6), (5, 7)}, supports
