"""Data Lifecycle — Revision 保护判定矩阵（P4 补强 D6）。

plan/execute 双侧共用的保护函数：pinned/head/持久层/血缘根 四类保护，
保护语义漂移即红（保护矩阵是 retention 的旗舰契约）。
"""
from __future__ import annotations

import types

import pytest

from app.services.data_lifecycle import quota as Q


class _Rev(types.SimpleNamespace):
    pass


class _Art(types.SimpleNamespace):
    pass


@pytest.fixture()
def run_protection(monkeypatch):
    head_ids = {"rev-head"}
    lineage = {"art-parent"}

    def _scan(db):
        return head_ids, lineage

    monkeypatch.setattr(Q, "_retention_scan_state", _scan)

    def _run(rev, art=None):
        return Q._retention_revision_protection(
            None, rev, art, head_ids=head_ids, lineage_parent_ids=lineage)

    return _run


def test_pinned_revision_is_never_deleted(run_protection) -> None:
    rev = _Rev(id="rev-1", pinned_at="now", artifact_id="art-a")
    assert run_protection(rev) == "pinned"


def test_head_revision_is_protected(run_protection) -> None:
    rev = _Rev(id="rev-head", pinned_at=None, artifact_id="art-a")
    assert "head" in run_protection(rev)


def test_workspace_tier_artifact_revisions_are_protected(run_protection) -> None:
    rev = _Rev(id="rev-2", pinned_at=None, artifact_id="art-b")
    art = _Art(metadata_json={"persistence_tier": "workspace"})
    reason = run_protection(rev, art)
    assert reason and "workspace" in reason


def test_lineage_root_with_downstream_is_protected(run_protection) -> None:
    rev = _Rev(id="rev-3", pinned_at=None, artifact_id="art-parent")
    assert "lineage" in run_protection(rev)


def test_plain_old_revision_is_deletable(run_protection) -> None:
    rev = _Rev(id="rev-9", pinned_at=None, artifact_id="art-plain")
    art = _Art(metadata_json={})
    assert run_protection(rev, art) is None


def test_orphan_revision_has_no_tier_or_lineage_protection(run_protection) -> None:
    # 孤儿修订（artifact 行消失 → art=None）：层/血缘保护天然缺席。
    rev = _Rev(id="rev-10", pinned_at=None, artifact_id="art-deleted")
    assert run_protection(rev, None) is None


def test_non_dict_metadata_is_tolerated(run_protection) -> None:
    rev = _Rev(id="rev-11", pinned_at=None, artifact_id="art-c")
    art = _Art(metadata_json="not-a-dict")
    assert run_protection(rev, art) is None
