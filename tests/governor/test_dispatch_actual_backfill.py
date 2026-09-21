"""dispatch adapter actual 回填 + 校准 + 重试入账测试（ADR-0204 D4/R6）。"""
import asyncio

import pytest

from app.services.governor import dispatch_adapter as da
from app.services.governor.calibration import (
    get_calibration_store,
    reset_calibration_store_for_tests,
)
from app.services.governor.config import GovernorConfig, GovernorMode
from app.services.governor.contract import (
    AdmissionDecision,
    Dimension,
    ResourceDecision,
    ResourceReservation,
    ResourceUsage,
    RetryClass,
)
from app.services.governor.dispatch_adapter import (
    GovernorDispatchAdapter,
    _recent_attempt,
    _note_outcome,
    _TRACKED,
)


class _CaptureGovernor:
    """最小 governor stub：admit 恒 accept；complete/charge 全捕获。"""

    def __init__(self, mode: GovernorMode = GovernorMode.ENFORCE):
        self.config = GovernorConfig()
        self.config.mode = mode
        self.completed = []
        self.charged = []
        from app.services.governor.retry_budget import RetryBudget
        self.retries = RetryBudget()

    async def admit_and_reserve(self, demand, **kwargs):
        decision = ResourceDecision(decision=AdmissionDecision.ACCEPT,
                                    reasons=["test"])
        reservation = ResourceReservation(
            session_id=demand.session_id,
            subsystem=demand.subsystem,
        )
        return decision, reservation, None

    async def complete(self, reservation, ticket, *, usage=None, actual=None,
                       estimate=None):
        self.completed.append({
            "usage": usage, "actual": actual, "estimate": estimate})


def _adapter(governor=None) -> tuple:
    governor = governor or _CaptureGovernor()
    adapter = GovernorDispatchAdapter(
        governor, metadata_fn=lambda _n: {"cost": "medium"})
    return adapter, governor


class _Result:
    def __init__(self, raw):
        self.raw_result = raw


@pytest.fixture(autouse=True)
def _clean_state():
    _TRACKED.clear()
    reset_calibration_store_for_tests()
    yield
    _TRACKED.clear()
    reset_calibration_store_for_tests()


def test_actual_backfill_counts_and_calibration_recorded():
    adapter, governor = _adapter()

    async def inner():
        return _Result({"success": True, "feature_count": 120})

    result = asyncio.run(adapter.run(
        tool_name="query_local_poi", tool_args={"limit": 10},
        session_id="s1", dispatch_inner=inner))
    assert result.raw_result["feature_count"] == 120
    assert len(governor.completed) == 1
    rec = governor.completed[0]
    usage: ResourceUsage = rec["usage"]
    assert usage.wall_time_s and usage.wall_time_s > 0
    assert usage.dims[Dimension.FEATURE_COUNT] == 120
    assert rec["actual"][Dimension.WALL_TIME_S] > 0
    # 校准 store 已记 wall + feature_count 两个样本
    # （query_local_poi 经名字模式路由 → data_fabric 子系统键）
    stats = get_calibration_store().stats("data_fabric:query_local_poi")
    stats = stats["data_fabric:query_local_poi"]
    assert "wall_time_s" in stats
    assert stats["feature_count"]["n"] == 1


def test_payload_size_actual_only_for_str_bytes():
    adapter, governor = _adapter()

    async def inner():
        return _Result({"success": True, "data": "x" * 2048})

    asyncio.run(adapter.run(tool_name="query_dataset", tool_args={},
                            session_id="s2", dispatch_inner=inner))
    usage = governor.completed[0]["usage"]
    assert usage.dims[Dimension.NETWORK_BYTES] == 2048

    async def inner_dict():
        return _Result({"success": True, "data": {"big": [1] * 100}})

    asyncio.run(adapter.run(tool_name="query_dataset", tool_args={},
                            session_id="s3", dispatch_inner=inner_dict))
    usage2 = governor.completed[1]["usage"]
    assert Dimension.NETWORK_BYTES not in usage2.dims   # dict 不测（O(1) 纪律）


def test_recent_failure_marks_attempt_and_charges_retry_budget():
    governor = _CaptureGovernor()
    adapter, governor = _adapter(governor)
    _note_outcome("s4", "query_osm_boundary", "failed")
    assert _recent_attempt("s4", "query_osm_boundary") == 2

    async def inner():
        return _Result({"success": True})

    asyncio.run(adapter.run(tool_name="query_osm_boundary", tool_args={},
                            session_id="s4", dispatch_inner=inner))
    assert governor.charged == []   # charge 走 retries（Capture 有真 RetryBudget）
    snap = governor.retries.snapshot()
    assert snap["sessions"]["s4"]["charges"].get("tool") == 1
    # demand attempt 透传验证：直接调 _build_demand
    demand = adapter._build_demand("t", {}, "s5", "", attempt=3,
                                   retry_class=RetryClass.TOOL)
    assert demand.attempt == 3
    assert demand.retry_class is RetryClass.TOOL


def test_success_clears_failure_window():
    _note_outcome("s6", "t", "failed")
    _note_outcome("s6", "t", "completed")
    assert _recent_attempt("s6", "t") == 1


def test_tracked_bounded():
    for i in range(3000):
        _note_outcome(f"s-{i}", "t", "failed")
    assert len(_TRACKED) <= da._MAX_TRACKED


def test_dispatch_exception_marks_failure_and_keeps_window():
    """dispatch_inner 抛错 → _note_outcome 记失败（窗口保留，下次 attempt+1）。"""
    adapter, _ = _adapter()

    async def boom():
        raise RuntimeError("tool exploded")

    with pytest.raises(RuntimeError):
        asyncio.run(adapter.run(tool_name="query_osm_boundary", tool_args={},
                                session_id="s7", dispatch_inner=boom))
    assert _recent_attempt("s7", "query_osm_boundary") == 2


def test_cancelled_error_still_completes_and_clears_window():
    adapter, governor = _adapter()

    async def cancelled():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(adapter.run(tool_name="query_osm_boundary", tool_args={},
                                session_id="s8", dispatch_inner=cancelled))
    assert governor.completed and governor.completed[0]["usage"].status == \
        "cancelled"
    assert _recent_attempt("s8", "query_osm_boundary") == 1   # 取消清窗


def test_calibrate_usage_script_suggestion_flow(tmp_path):
    """离线校准闭环：快照 → suggest → 建议文件（不触碰运行时先验）。"""
    import json
    import subprocess
    import sys
    from pathlib import Path

    store = get_calibration_store()
    for _ in range(12):
        store.record("tool_dispatch:drifty", Dimension.WALL_TIME_S, 5.0, 25.0)
    snap_path = tmp_path / "snap.json"
    snap_path.write_text(json.dumps(store.snapshot()))
    out_path = tmp_path / "suggest.json"
    proc = subprocess.run(
        [sys.executable, "scripts/perf/calibrate_from_usage.py",
         "--snapshot", str(snap_path), "--report", str(out_path)],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[2]))
    assert proc.returncode == 0, proc.stderr
    report = json.loads(out_path.read_text())
    assert report["suggestions"][0]["tool_key"] == "tool_dispatch:drifty"
    assert report["suggestions"][0]["action"] == "raise_prior"
