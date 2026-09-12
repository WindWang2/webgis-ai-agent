"""Data Lifecycle — RetentionPolicy 环境解析（P4 补强 D6 相邻面）。"""
from __future__ import annotations

import pytest

from app.services.data_lifecycle.quota import RetentionPolicy

_ENV_AGE = "WEBGIS_RETENTION_MAX_AGE_DAYS"
_ENV_GRACE = "WEBGIS_RETENTION_GRACE_HOURS"


@pytest.fixture(autouse=True)
def _stub_grace_default(monkeypatch):
    # grace 默认值经 artifact_lifecycle 导入（PDF 渲染依赖链）—— 与本文件
    # 的年龄策略解析无关，置常量以保持环境无关。
    monkeypatch.setattr(
        "app.services.data_lifecycle.quota._default_retention_grace_hours",
        lambda: 48.0)


def test_default_is_keep_forever(monkeypatch) -> None:
    monkeypatch.delenv(_ENV_AGE, raising=False)
    policy = RetentionPolicy.from_env()
    assert policy.max_age_days == 0.0  # 0 = 永久保留
    assert policy.grace_hours == 48.0


def test_age_days_env_enables_retention(monkeypatch) -> None:
    monkeypatch.setenv(_ENV_AGE, "30")
    policy = RetentionPolicy.from_env()
    assert policy.max_age_days == 30.0


def test_grace_hours_env(monkeypatch) -> None:
    monkeypatch.setenv(_ENV_GRACE, "48")
    policy = RetentionPolicy.from_env()
    assert policy.grace_hours == 48.0


def test_fractional_age_is_honored(monkeypatch) -> None:
    monkeypatch.setenv(_ENV_AGE, "0.5")
    assert RetentionPolicy.from_env().max_age_days == 0.5


def test_garbage_age_falls_back_to_keep_forever(monkeypatch) -> None:
    monkeypatch.setenv(_ENV_AGE, "soon")
    assert RetentionPolicy.from_env().max_age_days == 0.0
