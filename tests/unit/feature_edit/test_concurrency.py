"""M8 并发/锁透传/读面一致性测试。

- 并发双提交：一成一 409（CAS 语义，绝不双写）。
- turn 锁透传：工具路径在父 turn 持锁（ContextVar 绑定）下提交不死锁、
  盖章转后台 deferred（锁外执行）。
- 编辑后读面：feature_pages 的旧 revision cursor 被拒（409 面）。
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.feature_edit import executor as fe_executor
from app.services.feature_edit import service as fe_service
from app.services.session_data import MemorySessionStore

SCHOOLS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": f"sch-{i}",
            "geometry": {"type": "Point", "coordinates": [116.0 + 0.01 * i, 39.0]},
            "properties": {"name": f"学校{i}", "type": "小学" if i % 2 == 0 else "中学", "capacity": 500 + i},
        }
        for i in range(5)
    ],
}


@pytest.fixture
def store():
    return MemorySessionStore()


@pytest.fixture(autouse=True)
def _patch(store, monkeypatch):
    monkeypatch.setattr(fe_executor, "session_data_manager", store)
    monkeypatch.setattr(fe_service, "session_data_manager", store)


async def _mk_plan(store, sid, fields, ids):
    ref = next(iter(store._store.get(sid, {})))  # 该会话唯一测试 ref
    plan, findings = await fe_service.build_and_store_plan(
        sid, ref_id=ref, feature_ids=ids,
        ops=[{"op": "set_fields", "fields": fields}],
    )
    assert not findings, findings
    return plan


@pytest.mark.asyncio
async def test_concurrent_commits_exactly_one_wins(store):
    sid = "sess-conc1"
    ref = await store.store(sid, {"geojson": SCHOOLS}, prefix="data")
    plan_a = await _mk_plan(store, sid, {"type": "小学"}, ["sch-1"])
    plan_b = await _mk_plan(store, sid, {"capacity": 1}, ["sch-3"])

    results = await asyncio.gather(
        fe_executor.commit_plan(sid, plan_a, actor="user", origin="user", idempotency_key="k-a"),
        fe_executor.commit_plan(sid, plan_b, actor="user", origin="user", idempotency_key="k-b"),
        return_exceptions=True,
    )
    ok = [r for r in results if isinstance(r, dict)]
    conflicts = [r for r in results if isinstance(r, fe_executor.FeatureEditConflict)]
    assert len(ok) == 1 and len(conflicts) == 1
    # 输家是 CAS 冲突且携带 head 证据
    assert conflicts[0].code == "stale_revision"
    assert conflicts[0].detail["journal"]["head"]["seq"] == 1
    # journal 只有 1 条 apply；revision 只 bump 一次
    journals = await fe_executor._load_journals(sid)
    entries = journals[ref]["entries"]
    assert len([e for e in entries if e["kind"] == "apply"]) == 1
    desc = await store.get_ref_descriptor(sid, ref)
    assert desc["content_revision"] == 2


@pytest.mark.asyncio
async def test_turn_lock_passthrough_no_deadlock_and_deferred_stamp(store):
    """工具路径：父 turn 已持会话锁 → executor 透传（不自锁）；盖章转后台。"""
    from app.services.distributed_lock import session_lock
    from app.services.harness_kernel.legacy_adapter import bind_engine_lock, unbind_engine_lock

    sid = "sess-conc2"
    ref = await store.store(sid, {"geojson": SCHOOLS}, prefix="data")
    plan = await _mk_plan(store, sid, {"type": "小学"}, ["sch-1"])

    # 先在 mapspec 面挂一个引用该 ref 的源，盖章有实际对象（走全局 manager）
    from app.services.mapspec_store import mapspec_store

    await mapspec_store.layer_upsert(
        sid,
        layer={"id": "l1", "type": "circle", "source": "src-1"},
        source_data={"type": "geojson", "ref_id": ref, "profile": {}, "profile_fingerprint": "p", "data_fingerprint": "d"},
        origin="agent",
        actor="test",
    )

    lock = session_lock(sid)
    async with lock:
        token = bind_engine_lock(sid, lock)
        try:
            receipt = await asyncio.wait_for(
                fe_executor.commit_plan(sid, plan, actor="agent", origin="agent"),
                timeout=10,
            )
        finally:
            unbind_engine_lock(token)
    # 持锁透传下完成提交；盖章 deferred（后台锁外执行）
    assert receipt["kind"] == "apply"
    assert receipt["stamp_status"] in ("deferred", "stamped")

    # 后台盖章最终收敛（父锁已释放，轮询有界等待）
    from app.services.mapspec_store import mapspec_store as ms

    for _ in range(40):
        spec = await ms.get_mapspec(sid)
        sources = (spec or {}).get("sources", {})
        stamped = [s for s in sources.values() if s.get("ref_id") == ref and s.get("content_revision")]
        if stamped and stamped[0]["content_revision"] == receipt["content_revision_after"]:
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("deferred mapspec stamp did not converge after parent lock release")


@pytest.mark.asyncio
async def test_descriptor_revision_bump_drives_read_side_guard(store):
    """编辑 bump content_revision → /features 的 revision guard（v 不符即 409）
    自动对旧代客户端生效 —— 读面一致性由既有 guard 免费获得（layer.py:174-186）。"""
    sid = "sess-conc3"
    ref = await store.store(sid, {"geojson": SCHOOLS}, prefix="data")
    rev_before = (await store.get_ref_descriptor(sid, ref))["content_revision"]

    plan = await _mk_plan(store, sid, {"capacity": 999}, ["sch-0"])
    await fe_executor.commit_plan(sid, plan, actor="user", origin="user")

    payload = await store.get(sid, ref)
    assert payload["geojson"]["features"][0]["properties"]["capacity"] == 999
    desc = await store.get_ref_descriptor(sid, ref)
    assert desc["content_revision"] == rev_before + 1  # 旧 v 客户端 → 409 revision_conflict
