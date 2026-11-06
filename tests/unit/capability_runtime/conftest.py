"""Capability Runtime vNext(H05)测试包。

单例卫生:每个测试前后复位进程级单例(健康台账/lease manager/presence
provider/catalog 缓存),防测试间状态泄漏 —— 这些单例是生产接线面,
测试必须从干净状态出发。
投影刷新还会替换 runtime manifest；保留入场快照（包括 None），退出时
原样恢复，避免测试 registry 的空能力视图泄漏到后续 planner 用例。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_runtime_singletons(monkeypatch):
    from app.lib.secret_lease import set_secret_lease_manager
    from app.lib.tool_security import set_credential_presence_provider
    from app.services.capability_runtime.health import (
        set_provider_health_registry,
    )

    set_provider_health_registry(None)
    set_secret_lease_manager(None)
    set_credential_presence_provider(None)
    monkeypatch.delenv("GIS_PROVIDER_HEALTH", raising=False)
    monkeypatch.delenv("GIS_TOOL_CREDENTIALS", raising=False)
    monkeypatch.delenv("GIS_TOOL_PERMISSIONS", raising=False)
    # catalog 单例复位(模块级缓存;F07 测试同款直改手法)
    import app.lib.gis.execution_catalog as _ec
    import app.lib.gis.runtime_manifest as _rm

    _saved_catalog = _ec._cached_catalog
    _saved_manifest = _rm._cached_manifest
    _ec._cached_catalog = None
    try:
        yield
    finally:
        set_provider_health_registry(None)
        set_secret_lease_manager(None)
        set_credential_presence_provider(None)
        _ec._cached_catalog = _saved_catalog
        _rm._cached_manifest = _saved_manifest
