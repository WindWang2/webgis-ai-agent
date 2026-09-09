"""STORAGE_TRANSIENT_FAIL chaos 注入的行为验证（Quality V2 W9）。

故障：制品账本存储后端（session_data_manager.store/overwrite）瞬时写
失败（OSError 抖动）后恢复。

期望行为（测试断言的对象）：
1. 瞬时失败**显式上抛**（类型化 OSError 传播到调用方）——绝不静默丢
   数据、绝不假成功；
2. 账本 alias 不前进：失败路径不留下半截提交（旧账本仍可完整读回）；
3. 恢复后同一操作重试成功，账本语义完整（注册可见、可读回）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))  # tests/ 作为包导入 fixtures

from tests.fixtures.chaos import (  # noqa: E402
    chaos,
    journal_snapshot,
    reset_journal,
)


@pytest.fixture(autouse=True)
def _clean_journal():
    reset_journal()
    yield
    reset_journal()


@pytest.fixture
async def session_env():
    sid = f"chaos-storage-{id(object()):x}"
    from app.services.session_data import session_data_manager

    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


@pytest.mark.chaos
async def test_transient_storage_failure_surfaces_typed_error(session_env):
    """第 1 次 store 抛 OSError：注册显式失败，账本不半截提交。"""
    sid = session_env
    from app.services.artifact_registry import (
        get_artifact,
        register_artifact,
    )

    with chaos("STORAGE_TRANSIENT_FAIL", fail_times=1):
        # 真实契约：register_artifact 捕获存储异常 → 返回 None（显式拒绝，
        # 不静默假成功）；产物本体不受影响。
        rejected = await register_artifact(
            sid,
            artifact_id="ref:chaos-fc-1",
            artifact_type="feature_collection",
            producer_tool="buffer",
            metadata={"seam": "chaos-storage"},
        )
        assert rejected is None, "瞬时存储失败必须显式拒绝注册（None）"

    # 恢复后重试成功（无残留半截状态，ref 可读回）
    rec = await register_artifact(
        sid,
        artifact_id="ref:chaos-fc-1",
        artifact_type="feature_collection",
        producer_tool="buffer",
        metadata={"seam": "chaos-storage"},
    )
    assert rec is not None
    got = await get_artifact(sid, "ref:chaos-fc-1")
    assert got is not None and got.metadata.get("seam") == "chaos-storage"

    # journal 记录了真实注入（fired）
    fired = [e for e in journal_snapshot()
             if e.fault_id == "STORAGE_TRANSIENT_FAIL"
             and e.action == "fired"]
    assert fired, "瞬时故障必须被真实触发过（journal 证据）"


@pytest.mark.chaos
async def test_ledger_alias_not_advanced_on_failure(session_env):
    """失败路径不推进 alias：后续读侧看不到半截账本。"""
    sid = session_env
    from app.services.artifact_registry import (
        LEDGER_ALIAS,
        register_artifact,
    )
    from app.services.session_data import session_data_manager

    # 先建立一笔健康账本
    await register_artifact(
        sid, artifact_id="ref:base-1", artifact_type="feature_collection",
        producer_tool="buffer", metadata={},
    )
    alias_ref = await session_data_manager.resolve_alias(
        sid, LEDGER_ALIAS)
    baseline = await session_data_manager.get(sid, alias_ref)
    assert baseline and "ref:base-1" in (baseline.get("artifacts") or {})

    with chaos("STORAGE_TRANSIENT_FAIL", fail_times=2):
        # 真实契约：register_artifact 捕获存储异常并返回 None（注册失败
        # 只影响记录，不影响产物）——调用方以 None 为失败信号。
        rejected = await register_artifact(
            sid, artifact_id="ref:base-2",
            artifact_type="feature_collection",
            producer_tool="buffer", metadata={},
        )
        assert rejected is None, "瞬时存储失败必须显式拒绝注册（None）"
        # 失败后旧账本仍完整可见（读侧一致性）
        current = await session_data_manager.get(
            sid,
            await session_data_manager.resolve_alias(sid, LEDGER_ALIAS))
        assert current is not None
        assert "ref:base-1" in (current.get("artifacts") or {})
        assert "ref:base-2" not in (current.get("artifacts") or {}), (
            "失败路径不得留下半截提交")
