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
