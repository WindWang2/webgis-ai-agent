"""Data Lifecycle — per-artifact revision byte 闸（P4 补强 D3 补充）。

第三闸：单 artifact 修订字节上限（projected = 已用 + incoming 超限拒绝）；
无 artifact_id 时以全项目最大单 artifact 修订字节为口径。
"""
from __future__ import annotations

import pytest

from app.services.data_lifecycle.quota import ProjectQuotaPolicy, check_quota


@pytest.fixture()
def _patch_usage(monkeypatch):
    def _install(usage: dict):
        monkeypatch.setattr(
            "app.services.artifact_revisions.project_quota_usage",
            lambda db, project_id: usage)
    return _install


def _usage(revision_bytes: int) -> dict:
    return {"bytes": 0, "artifact_count": 0, "revision_bytes": revision_bytes,
            "revision_bytes_by_artifact": {"a1": revision_bytes},
            "max_per_artifact_revision_bytes": revision_bytes}


def test_per_artifact_revision_gate_blocks_overflow(_patch_usage) -> None:
    _patch_usage(_usage(revision_bytes=800))
    policy = ProjectQuotaPolicy(max_revision_bytes_per_artifact=1000)
    decision = check_quota(None, "p", incoming_bytes=300, artifact_id="a1",
                           policy=policy)
    assert decision.allowed is False
    assert decision.reason == "per-artifact revision byte quota exceeded"
    assert decision.details["artifact_id"] == "a1"
    assert decision.details["limit_bytes"] == 1000


def test_per_artifact_revision_gate_allows_within_limit(_patch_usage) -> None:
    _patch_usage(_usage(revision_bytes=700))
    policy = ProjectQuotaPolicy(max_revision_bytes_per_artifact=1000)
    decision = check_quota(None, "p", incoming_bytes=300, artifact_id="a1",
                           policy=policy)
    assert decision.allowed is True


def test_no_artifact_id_uses_project_max_gate(_patch_usage) -> None:
    # 无 artifact_id：projected = 全项目最大单 artifact 修订字节（无 incoming
    # 参与时 projected == max_per_artifact_revision_bytes 恰好不超限 → 放行）。
    _patch_usage(_usage(revision_bytes=1000))
    policy = ProjectQuotaPolicy(max_revision_bytes_per_artifact=1000)
    decision = check_quota(None, "p", incoming_bytes=0, policy=policy)
    assert decision.allowed is True


def test_zero_incoming_never_triggers_per_artifact_gate(_patch_usage) -> None:
    # 去重命中：零字节指针克隆不被 per-artifact 闸拒绝。
    _patch_usage(_usage(revision_bytes=1000))
    policy = ProjectQuotaPolicy(max_revision_bytes_per_artifact=1000)
    decision = check_quota(None, "p", incoming_bytes=0, artifact_id="a1",
                           policy=policy)
    assert decision.allowed is True
