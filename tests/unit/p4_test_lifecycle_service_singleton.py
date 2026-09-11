"""Data Lifecycle — 生命周期服务单例与重置（P4 补强：组合根面）。"""
from __future__ import annotations

from app.services.data_lifecycle.service import (
    ArtifactLifecycleService,
    get_lifecycle_service,
    reset_lifecycle_service,
)


def test_get_returns_same_singleton() -> None:
    reset_lifecycle_service()
    a = get_lifecycle_service()
    b = get_lifecycle_service()
    assert a is b
    assert isinstance(a, ArtifactLifecycleService)


def test_reset_yields_fresh_instance() -> None:
    reset_lifecycle_service()
    a = get_lifecycle_service()
    reset_lifecycle_service()
    b = get_lifecycle_service()
    assert a is not b
