"""Data Lifecycle — QuotaDecision 判定矩阵（P4 补强 D3）。

typed 决策面：byte/count/per-artifact 三闸各自触发、去重零字节不收费、
unlimited 直通；决策 → typed 异常 → 有界 metadata 投影全链一致。
"""
from __future__ import annotations

import pytest

from app.services.data_lifecycle.quota import (
    ProjectQuotaPolicy,
    ProjectQuotaExceededError,
    QuotaDecision,
    QuotaUsage,
    check_quota,
)


def _usage(bytes_: int = 0, count: int = 0, revision_bytes: int = 0) -> dict:
    return {"bytes": bytes_, "artifact_count": count, "revision_bytes": revision_bytes,
            "revision_bytes_by_artifact": {"a1": revision_bytes},
            "max_per_artifact_revision_bytes": revision_bytes}


@pytest.fixture()
def _patch_usage(monkeypatch):
    def _install(usage: dict):
        monkeypatch.setattr(
            "app.services.artifact_revisions.project_quota_usage",
            lambda db, project_id: usage)
    return _install


def test_unlimited_policy_allows_anything(_patch_usage) -> None:
    _patch_usage(_usage(bytes_=10**12, count=10**6))
    decision = check_quota(None, "p1", incoming_bytes=10**9,
                           policy=ProjectQuotaPolicy())
    assert decision.allowed is True
    assert decision.reason == ""


def test_byte_quota_exceeded_carries_bounded_details(_patch_usage) -> None:
    _patch_usage(_usage(bytes_=900))
    policy = ProjectQuotaPolicy(max_bytes=1000)
    decision = check_quota(None, "p-quota", incoming_bytes=200, policy=policy)
    assert decision.allowed is False
    assert decision.reason == "project artifact byte quota exceeded"
    assert decision.details["limit_bytes"] == 1000
    assert decision.details["usage_bytes"] == 900
    assert decision.details["incoming_bytes"] == 200


def test_byte_quota_exact_fit_is_allowed(_patch_usage) -> None:
    _patch_usage(_usage(bytes_=800))
    decision = check_quota(None, "p", incoming_bytes=200,
                           policy=ProjectQuotaPolicy(max_bytes=1000))
    assert decision.allowed is True


def test_dedup_hit_charges_nothing(_patch_usage) -> None:
    # CAS 去重命中 → incoming_bytes=0：已满项目里克隆仍可用。
    _patch_usage(_usage(bytes_=1000))
    decision = check_quota(None, "p", incoming_bytes=0,
                           policy=ProjectQuotaPolicy(max_bytes=1000))
    assert decision.allowed is True


def test_artifact_count_quota_exceeded(_patch_usage) -> None:
    _patch_usage(_usage(count=5))
    decision = check_quota(None, "p", incoming_artifacts=1,
                           policy=ProjectQuotaPolicy(max_artifact_count=5))
    assert decision.allowed is False
    assert decision.reason == "project artifact count quota exceeded"
    assert decision.details["limit_artifacts"] == 5


def test_to_exception_roundtrip_preserves_numbers(_patch_usage) -> None:
    _patch_usage(_usage(bytes_=900))
    decision = check_quota(None, "p-x", incoming_bytes=300,
                           policy=ProjectQuotaPolicy(max_bytes=1000))
    err = decision.to_exception()
    assert isinstance(err, ProjectQuotaExceededError)
    assert err.limit_bytes == 1000
    assert err.usage_bytes == 900
    assert err.incoming_bytes == 300
    assert err.project_id == "p-x"


def test_error_metadata_projection_is_bounded() -> None:
    err = ProjectQuotaExceededError(reason="r" * 300, project_id="p" * 300,
                                    limit_bytes=1, usage_bytes=2, incoming_bytes=3)
    meta = err.to_metadata()
    assert len(meta) <= 8
    assert len(meta["reason"]) <= 96
    assert len(meta["project_id"]) <= 64


def test_decision_defaults_are_allowed() -> None:
    d = QuotaDecision()
    assert d.allowed is True and d.usage == QuotaUsage()
