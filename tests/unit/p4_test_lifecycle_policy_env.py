"""Data Lifecycle — ProjectQuotaPolicy 环境解析（P4 补强 D3 相邻面）。"""
from __future__ import annotations

import pytest

from app.services.data_lifecycle.quota import ProjectQuotaPolicy


def test_default_env_is_unlimited(monkeypatch) -> None:
    for key in ("WEBGIS_PROJECT_ARTIFACT_MAX_BYTES",
                "WEBGIS_PROJECT_ARTIFACT_MAX_COUNT",
                "WEBGIS_PROJECT_ARTIFACT_MAX_REVISION_BYTES"):
        monkeypatch.delenv(key, raising=False)
    policy = ProjectQuotaPolicy.from_env()
    assert policy.unlimited is True


def test_env_values_are_honored(monkeypatch) -> None:
    monkeypatch.setenv("WEBGIS_PROJECT_ARTIFACT_MAX_BYTES", "1000")
    monkeypatch.setenv("WEBGIS_PROJECT_ARTIFACT_MAX_COUNT", "7")
    monkeypatch.setenv("WEBGIS_PROJECT_ARTIFACT_MAX_REVISION_BYTES", "64")
    policy = ProjectQuotaPolicy.from_env()
    assert policy.unlimited is False
    assert (policy.max_bytes, policy.max_artifact_count,
            policy.max_revision_bytes_per_artifact) == (1000, 7, 64)


def test_garbage_env_falls_back_to_unlimited_axis(monkeypatch) -> None:
    monkeypatch.setenv("WEBGIS_PROJECT_ARTIFACT_MAX_BYTES", "abc")
    monkeypatch.setenv("WEBGIS_PROJECT_ARTIFACT_MAX_COUNT", "")
    policy = ProjectQuotaPolicy.from_env()
    assert policy.max_bytes == 0
    assert policy.max_artifact_count == 0


def test_partial_limit_is_not_unlimited(monkeypatch) -> None:
    monkeypatch.setenv("WEBGIS_PROJECT_ARTIFACT_MAX_REVISION_BYTES", "16")
    policy = ProjectQuotaPolicy.from_env()
    assert policy.unlimited is False
