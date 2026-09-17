"""Data Fabric offline policy（ADR-0202）：air-gapped 下远程源 typed unavailable。

probe 面走 validate_url → SecurityBlockedError（数据面原生词表，
reliability 层按 permanent 处理不重试）；send 面的 redirect 逃逸由
SSRFSafeHTTPAdapter 的 AirGappedEgressError 兜底（见 wiring 测试）。
"""
import pytest

from app.core.config import settings
from app.core.egress import reset_policy_cache
from app.services.data_fabric.errors import SecurityBlockedError
from app.services.data_fabric.security import DataFabricSecurity


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    reset_policy_cache()
    yield
    reset_policy_cache()


def _air_gapped(monkeypatch, allow=""):
    monkeypatch.setattr(settings, "DEPLOYMENT_PROFILE", "air_gapped")
    monkeypatch.setattr(settings, "NETWORK_EGRESS_MODE", "allowlist")
    monkeypatch.setattr(settings, "NETWORK_EGRESS_ALLOW", allow)
    monkeypatch.setattr(settings, "NETWORK_EGRESS_ALLOW_PRIVATE", "true")


def test_air_gapped_public_remote_source_security_blocked(monkeypatch):
    _air_gapped(monkeypatch)
    with pytest.raises(SecurityBlockedError) as excinfo:
        DataFabricSecurity.validate_url("https://public.example/wfs")
    err = excinfo.value
    assert err.code == "SECURITY_BLOCKED"
    assert err.details.get("reason") == "not_allowlisted"
    assert err.details.get("host") == "public.example"
    assert err.details.get("policy") == "egress_allowlist"


def test_air_gapped_private_remote_source_passes_policy(monkeypatch):
    _air_gapped(monkeypatch)
    # 内网 WFS/瓦片正是 air-gapped 部署形态：策略层放行（SSRF 门照常工作）
    url = DataFabricSecurity.validate_url("http://10.0.0.20/geoserver/wfs",
                                          allow_private=True)
    assert url.endswith("/wfs")


def test_air_gapped_allowlisted_remote_source_passes_policy(monkeypatch):
    _air_gapped(monkeypatch, allow="tiles.intranet.example")
    DataFabricSecurity.validate_url("https://tiles.intranet.example/v1/t.json")


def test_unrestricted_validate_url_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "NETWORK_EGRESS_MODE", "unrestricted")
    # cloud 默认：无策略注入（本机 DNS 环境下 example.com 可能为保留地址，
    # 这里只断言不抛 SecurityBlockedError 的 egress 变体）
    try:
        DataFabricSecurity.validate_url("https://public.example/wfs")
    except SecurityBlockedError as exc:
        if exc.details.get("policy") == "egress_allowlist":
            pytest.fail("unrestricted mode must not inject egress policy")


def test_metadata_ip_still_security_blocked(monkeypatch):
    """元数据端点在 allowlist 模式下被 egress 拒绝（METADATA_BLOCKED）。"""
    _air_gapped(monkeypatch)
    with pytest.raises(SecurityBlockedError) as excinfo:
        DataFabricSecurity.validate_url("http://169.254.169.254/latest/meta-data/")
    assert excinfo.value.details.get("reason") == "metadata_blocked"
