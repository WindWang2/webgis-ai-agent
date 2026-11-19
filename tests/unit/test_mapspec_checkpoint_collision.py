"""#2224 回归：同一毫秒内的两次自动 checkpoint 不得共用 ckpt_<ms> 目录。

旧实现：apply_mutation 先 snapshot(新世代) 再微秒级 snapshot(prior_spec)，
同毫秒时 mint 相同 id —— 第二份覆写同目录 mapspec.json+descriptor.json，
manifest 两条 content_hash 指向同一目录，rollback 恢复到错误世代。
"""
from __future__ import annotations

import json

import pytest

from app.services.mapspec import checkpoint


class _StubSDM:
    async def list_refs(self, session_id):
        return {}


@pytest.mark.asyncio
async def test_same_millisecond_snapshots_get_distinct_ids(tmp_path, monkeypatch):
    # 墙钟钉死：两次 snapshot 落在同一毫秒。
    monkeypatch.setattr(
        "app.services.mapspec.checkpoint.time.time", lambda: 1_700_000_000.0004)
    session_dir = tmp_path / "sess1"
    spec_a = {"version": "1.0", "layers": [{"id": "a"}], "sources": {}}
    spec_b = {"version": "1.0", "layers": [{"id": "b"}], "sources": {}}

    r1 = await checkpoint.snapshot(spec_a, session_dir, _StubSDM())
    r2 = await checkpoint.snapshot(spec_b, session_dir, _StubSDM())
    id1, id2 = r1["checkpoint_id"], r2["checkpoint_id"]
    assert id1 != id2
    assert id1.startswith("ckpt_") and id2.startswith("ckpt_")

    # 两个目录各自完整存活，内容互不覆写。
    d1 = session_dir / "checkpoints" / id1
    d2 = session_dir / "checkpoints" / id2
    assert json.loads((d1 / "mapspec.json").read_text()) == spec_a
    assert json.loads((d2 / "mapspec.json").read_text()) == spec_b

    # manifest 两条 content_hash 各指各的目录。
    manifest = json.loads(
        (session_dir / "checkpoints" / "manifest.json").read_text())
    assert set(manifest["entries"].values()) == {id1, id2}

    # rollback 各恢复到对应世代（同一 id 恢复出两种态 = 碰撞未修）。
    rb1 = await checkpoint.rollback(session_dir, id1, _StubSDM())
    rb2 = await checkpoint.rollback(session_dir, id2, _StubSDM())
    assert rb1["success"] and rb1["mapspec"] == spec_a
    assert rb2["success"] and rb2["mapspec"] == spec_b
