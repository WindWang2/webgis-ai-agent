"""network_policy 组件（/status/detailed）—— ADR-0202 配置面探测。

纯配置投影（无 IO、无 DNS）：守卫状态 + 离线依赖可用面概要。
词表封闭沿用 ok | degraded | down | not_configured。
"""
import pytest

from app.api.routes import health as health_mod
from app.core import network_dependency as nd
from app.core.config import settings
from app.core.egress import reset_policy_cache


@pytest.fixture(autouse=True)
def _reset_caches():
    reset_policy_cache()
    nd.reset_catalog_settings_cache()
    yield
    reset_policy_cache()
    nd.reset_catalog_settings_cache()


def _set(profile, mode, allow="", allow_private="true"):
    return {
        "DEPLOYMENT_PROFILE": profile,
        "NETWORK_EGRESS_MODE": mode,
        "NETWORK_EGRESS_ALLOW": allow,
        "NETWORK_EGRESS_ALLOW_PRIVATE": allow_private,
    }


def test_network_policy_not_configured_in_cloud_default(monkeypatch):
    for k, v in _set("cloud", "unrestricted").items():
        monkeypatch.setattr(settings, k, v)
    status, latency, detail = health_mod._probe_component("network_policy")
    assert status == "not_configured"
    assert latency is None  # 配置面探测无 IO，不得伪造延迟


def test_network_policy_ok_when_allowlist_active(monkeypatch):
    for k, v in _set("air_gapped", "allowlist", allow="tiles.intranet").items():
        monkeypatch.setattr(settings, k, v)
    status, latency, detail = health_mod._probe_component("network_policy")
    assert status == "ok"
    assert detail is not None
    assert "air_gapped" in detail
    assert "allowlist" in detail
    # 概要携带离线依赖可用面（数字可 diff，不算密）
    assert "offline_deps=" in detail


def test_network_policy_component_registered():
    assert "network_policy" in health_mod._SRE_COMPONENTS


def test_snapshot_includes_network_policy(monkeypatch):
    for k, v in _set("cloud", "unrestricted").items():
        monkeypatch.setattr(settings, k, v)
    snapshot = health_mod._collect_sre_snapshot()
    assert "network_policy" in snapshot["components"]
    comp = snapshot["components"]["network_policy"]
    assert comp.status in ("ok", "degraded", "down", "not_configured")
