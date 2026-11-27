"""#2226 回归：brief_store 编辑操作不得绕过模型校验器写库。

旧实现：add/update_block、add/update_chapter 用 ``model_copy(update=...)``
返回 —— pydantic model_copy 不运行校验器，_cross_references_resolve /
_id_shape 被绕过；带未知 chapter_id/fact_id 或非法 map_view 的补丁直接
写库，此后每次 load_brief 抛 BriefCorrupt（会话全部 brief 端点 422 锁死）。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.lib.storymap.brief_spec import MapBriefSpec
from app.services.storymap import brief_store


def _spec() -> MapBriefSpec:
    return MapBriefSpec.model_validate({
        "metadata": {"title": "测试简报"},
        "chapters": [{"id": "ch1", "title": "一", "narrative": "n",
                      "arc_role": "introduction"}],
        "blocks": [{"id": "blk-1", "chapter_id": "ch1", "kind": "text",
                    "text": "hello"}],
        "facts": [{"fact_id": "fact-1", "label": "总数", "kind": "count",
                   "value": 42.0, "unit": "个", "status": "verified",
                   "basis": {"artifact_ref": "ref:stats-abc1",
                             "source_label": "测试来源"}}],
    })


def test_add_block_unknown_chapter_rejected():
    with pytest.raises(ValidationError):
        brief_store.add_block(_spec(), {
            "id": "blk-2", "chapter_id": "ghost-chapter", "kind": "text",
            "text": "hi",
        })


def test_update_block_unknown_fact_rejected():
    with pytest.raises(ValidationError):
        brief_store.update_block(_spec(), "blk-1", {"fact_id": "ghost-fact"})


def test_update_block_invalid_map_view_rejected():
    """map_view 形状非法（缺 center / zoom 越界）必须在写库前被拒。"""
    with pytest.raises(ValidationError):
        brief_store.update_block(_spec(), "blk-1", {
            "map_view": {"zoom": 99.0},  # 缺 center + zoom 越界
        })


def test_update_chapter_fact_references_stay_resolvable():
    """合法编辑仍通过（正控）：复验不得误伤正常路径。"""
    spec = _spec()
    out = brief_store.update_block(spec, "blk-1",
                                   {"fact_id": "fact-1", "locked": True})
    assert out.blocks[0].fact_id == "fact-1"
    assert out.blocks[0].locked is True
    out2 = brief_store.add_block(out, {
        "id": "blk-2", "chapter_id": "ch1", "kind": "text", "text": "more",
    })
    assert len(out2.blocks) == 2
    ch = brief_store.add_chapter(out2, {
        "id": "ch2", "title": "二", "narrative": "n2",
        "arc_role": "focus_dissection",
    })
    assert len(ch.chapters) == 2
    moved = brief_store.update_chapter(ch, "ch2", {"order": {"ch2": 0, "ch1": 1}})
    assert [c.id for c in moved.chapters] == ["ch2", "ch1"]


def test_compute_stale_flags_fact_when_bound_artifact_changes():
    """同 ref 产物原地更新 → 自动 fact 也必须披露 stale。

    旧实现 fact 分支只查 artifact_missing，basis.artifact_sha256 从未
    比对（block 分支有比对）—— 过期数值不会被告警。basis 指纹由编译器
    ``_canonical_sha256``（compact separators）产出，测试按同口径构造。
    """
    import hashlib
    import json

    record = {"artifact_id": "ref:stats-abc1", "producer_tool": "t", "n": 1}
    sha = hashlib.sha256(json.dumps(
        record, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    spec = MapBriefSpec.model_validate({
        "metadata": {"title": "测试简报"},
        "chapters": [{"id": "ch1", "title": "一", "narrative": "n",
                      "arc_role": "introduction"}],
        "facts": [{"fact_id": "fact-1", "label": "总数", "kind": "count",
                   "value": 42.0, "status": "verified",
                   "basis": {"artifact_ref": "ref:stats-abc1",
                             "artifact_sha256": sha}}],
    })
    unchanged = {"ref:stats-abc1": record}
    assert brief_store.compute_stale(
        spec, current_records=unchanged) == []
    changed = {"ref:stats-abc1": dict(record, n=2)}
    assert brief_store.compute_stale(
        spec, current_records=changed) == [
        {"fact_id": "fact-1", "reason": "artifact_updated"}]


async def test_create_brief_conflict_keeps_outer_transaction(
        tmp_path, monkeypatch):
    """并发双创建撞 uq_mb_conversation：只回滚插入帧。

    旧实现 IntegrityError 后 ``db.rollback()`` 回滚整个请求事务（注释却
    写"回滚本帧"）—— 同事务其他挂起变更被静默丢弃。
    """
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401
    from app.core.database import Base
    from app.models.db_model import Conversation
    from app.models.map_brief import MapBrief

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/store.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as setup:
            setup.add(Conversation(id="conv-1", user_id="u1", title="t"))
            # 竞争胜者已提交（预检查窗口之后才可见）
            setup.add(MapBrief(conversation_id="conv-1", title="existing",
                               spec="{}", revision=1))
            await setup.commit()

        async with maker() as db:
            conv = await db.get(Conversation, "conv-1")
            # 同事务其他挂起变更（外层请求尚未提交）
            db.add(Conversation(id="conv-pending", user_id="u1", title="p"))
            real_select = brief_store.select
            calls = {"n": 0}

            def _select(*args, **kwargs):
                stmt = real_select(*args, **kwargs)
                calls["n"] += 1
                if calls["n"] == 1:
                    # 模拟竞争窗口：存在性预检查漏看并发胜者
                    return real_select(MapBrief).where(
                        MapBrief.conversation_id == "__concurrent_winner__")
                return stmt

            monkeypatch.setattr(brief_store, "select", _select)
            with pytest.raises(brief_store.RevisionConflict):
                await brief_store.create_brief(db, conv, _spec())
            await db.commit()

        async with maker() as verify:
            assert await verify.get(Conversation, "conv-pending") is not None
            count = (await verify.execute(
                select(func.count()).select_from(MapBrief).where(
                    MapBrief.conversation_id == "conv-1"))).scalar()
            assert count == 1
    finally:
        await engine.dispose()
