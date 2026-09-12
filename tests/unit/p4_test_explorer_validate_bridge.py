"""Explorer — validate 阶段收尾契约（P4 补强：#774/#776 语义面）。"""
from __future__ import annotations

import pytest

from app.services.explorer import validate_stage as VS


@pytest.mark.asyncio
async def test_validate_without_bridge_returns_completion(monkeypatch) -> None:
    async def _no_bridge(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("无 session 时不应触发桥接")

    monkeypatch.setattr(VS, "bridge_geocoded_to_session", _no_bridge)
    result = await VS.run_validate_stage("t1", geocoded_ref_id="ref:g",
                                         total_rows=42)
    assert result.success is True
    assert result.data["status"] == "completed"
    assert result.data["total_rows"] == 42
    assert "session_ref_id" not in result.data


@pytest.mark.asyncio
async def test_validate_carries_fetch_errors_forward(monkeypatch) -> None:
    monkeypatch.setattr(VS, "bridge_geocoded_to_session", _noop_bridge)
    fetch_errors = [{"source_id": "s1", "error": "connection reset"}]
    result = await VS.run_validate_stage("t2", total_rows=3,
                                         fetch_errors=fetch_errors)
    assert result.data["fetch_errors"] == fetch_errors


@pytest.mark.asyncio
async def test_validate_bridges_to_session(monkeypatch) -> None:
    async def _bridge(task_id, session_id, ref):  # noqa: ANN001
        return {"session_ref_id": "ref:session-copy",
                "session_ref_alias": "探索结果"}

    monkeypatch.setattr(VS, "bridge_geocoded_to_session", _bridge)
    result = await VS.run_validate_stage("t3", geocoded_ref_id="ref:g",
                                         session_id="s-1")
    assert result.data["session_ref_id"] == "ref:session-copy"


@pytest.mark.asyncio
async def test_validate_bridge_failure_does_not_fail_exploration(
    monkeypatch,
) -> None:
    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("session store down")

    monkeypatch.setattr(VS, "bridge_geocoded_to_session", _boom)
    result = await VS.run_validate_stage("t4", geocoded_ref_id="ref:g",
                                         session_id="s-1")
    # 桥接失败：探索仍完成（诚实：桥接产物缺席）。
    assert result.success is True
    assert "session_ref_id" not in result.data


async def _noop_bridge(*a, **k):  # noqa: ANN002, ANN003
    return {}
