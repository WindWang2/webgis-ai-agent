"""Harness V5 project-level resume anchor 契约（ADR-0118 决策 D8）。

验收锚点（Epic V5）：interrupted workflow 可恢复 / session 过期后能在
项目权限下安全 resume ——
- 锚点构建：有界（关键 chapter 块 + progress + trace 游标 + ref 清单）；
- 恢复：新 session（旧 session 不复活）、plan/gis_chapter 关键块还原、
  resumed_from 披露；ref 重水合尽力而为 + missing_refs 诚实披露；
- 授权：user 一致才可恢复，匿名 fail-closed；
- 重启模拟：旧 session 清空（= 内存 store 丢失）后 resume 仍成功。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.resume_anchor import (
    RESTORABLE_CHAPTER_KEYS,
    build_anchor,
    resume_from_anchor,
    save_anchor,
)
from app.services.session_data import session_data_manager
from app.services.session_plan import (
    SessionPlan,
    load_session_plan,
    save_session_plan,
)


def _plan(sid: str) -> SessionPlan:
    return SessionPlan(
        envelope_id="sp-test-v5",
        session_id=sid,
        user_goal="对北京市各区 PM2.5 做插值出图",
        gis_chapter={
            "query": "PM2.5 插值地图",
            "intent": {"task_type": "interpolate"},
            "workflow_instance": {"state_revision": 3, "stages": []},
            "map_product": {"verdict": "READY_WITH_WARNINGS"},
            "ephemeral_block": {"big": "payload-not-restorable"},
        },
        progress=[],
    )


class _FakeDb:
    """极简 async DB 桩：内存表语义（owner 查询路径够用）。"""

    def __init__(self):
        self.rows = {}

    def add(self, row):
        import uuid

        if not getattr(row, "id", None):
            row.id = str(uuid.uuid4())
        self.rows[row.id] = row

    async def commit(self):
        return None

    async def refresh(self, row):
        return None

    async def get(self, model, pk):
        return self.rows.get(pk)


@pytest.fixture()
async def seeded_session():
    sid = "v5-resume-source"
    await session_data_manager.clear_session(sid)
    await save_session_plan(_plan(sid))
    ref = await session_data_manager.store(
        sid, {"type": "FeatureCollection", "features": []})
    yield sid, ref
    await session_data_manager.clear_session(sid)
    await session_data_manager.clear_session("v5-resume-new")


@pytest.mark.asyncio
async def test_build_anchor_bounded_and_cursored(seeded_session):
    sid, ref = seeded_session
    anchor = await build_anchor(sid)
    assert anchor is not None
    assert anchor["source_session_id"] == sid
    assert anchor["user_goal"].startswith("对北京")
    for key in ("workflow_instance", "map_product", "intent", "query"):
        assert key in anchor["gis_chapter"]
    assert "ephemeral_block" not in anchor["gis_chapter"]
    assert ref in anchor["ref_ids"]
    assert anchor["trace_last_seq"] >= 0


@pytest.mark.asyncio
async def test_save_and_resume_roundtrip_with_restart(seeded_session):
    """save → 旧 session 清空（重启/TTL 模拟）→ resume 新 session 还原。"""
    sid, ref = seeded_session
    db = _FakeDb()
    saved = await save_anchor(db, session_id=sid, user_id="u-v5")
    anchor_id = saved["anchor_id"]

    # 「重启/TTL 过期」：清掉旧 session（plan + ref 载荷全部消失）
    await session_data_manager.clear_session(sid)
    assert await load_session_plan(sid) is None

    result = await resume_from_anchor(
        db, anchor_id=anchor_id, user_id="u-v5")
    new_sid = result["session_id"]
    assert new_sid != sid
    assert new_sid.startswith("resume-")

    restored = await load_session_plan(new_sid)
    assert restored is not None
    assert restored.user_goal == "对北京市各区 PM2.5 做插值出图"
    for key in ("workflow_instance", "map_product", "query"):
        assert key in (restored.gis_chapter or {})
    assert restored.gis_chapter["resumed_from"]["anchor_id"] == anchor_id
    assert restored.gis_chapter["resumed_from"]["source_session_id"] == sid

    # ephemeral 块不跨 session 恢复
    assert "ephemeral_block" not in restored.gis_chapter

    # map_state 标记
    ms = await session_data_manager.get_map_state(new_sid)
    assert ms.get("_resumed_from", {}).get("anchor_id") == anchor_id

    # ref 载荷在旧 session 清空后无法重水合 → missing 诚实披露
    assert result["missing_refs"] or result["restored_refs"]


@pytest.mark.asyncio
async def test_resume_rehydrates_live_refs(seeded_session):
    """旧 session 仍在（未过期）→ ref 载荷重水合进新 session。"""
    sid, ref = seeded_session
    db = _FakeDb()
    saved = await save_anchor(db, session_id=sid, user_id="u-v5")
    result = await resume_from_anchor(
        db, anchor_id=saved["anchor_id"], user_id="u-v5")
    assert ref in result["restored_refs"]
    assert result["missing_refs"] == []
    # 新 session 中可读出同载荷
    new_refs = await session_data_manager.list_refs(result["session_id"])
    assert len(new_refs) >= 1


@pytest.mark.asyncio
async def test_resume_authz_fail_closed(seeded_session):
    sid, _ref = seeded_session
    db = _FakeDb()
    saved = await save_anchor(db, session_id=sid, user_id="owner-u")
    with pytest.raises(PermissionError):
        await resume_from_anchor(
            db, anchor_id=saved["anchor_id"], user_id="attacker-u")
    with pytest.raises(PermissionError):
        await resume_from_anchor(
            db, anchor_id=saved["anchor_id"], user_id=None)


@pytest.mark.asyncio
async def test_resume_unknown_anchor_404(seeded_session):
    db = _FakeDb()
    with pytest.raises(LookupError):
        await resume_from_anchor(
            db, anchor_id="no-such-anchor", user_id="u-v5")


@pytest.mark.asyncio
async def test_build_anchor_requires_plan():
    from app.services.session_data import session_data_manager as mgr

    sid = "v5-resume-empty"
    await mgr.clear_session(sid)
    assert await build_anchor(sid) is None


def test_restorable_keys_vocabulary():
    assert "workflow_instance" in RESTORABLE_CHAPTER_KEYS
    assert "map_product" in RESTORABLE_CHAPTER_KEYS
