"""requests 接缝（data_fabric SSRFSafeHTTPAdapter）的 egress 守卫测试。

守卫在 SSRF 校验之前执行；requests 对每跳 redirect 重挂 adapter，
因此 redirect 目标同样过守卫。
"""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.core.config import settings
from app.core.egress import AirGappedEgressError, reset_policy_cache
from app.services.data_fabric.security import make_safe_session


@pytest.fixture(autouse=True)
def _clean_policy_cache_and_proxies(monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    reset_policy_cache()
    yield
    reset_policy_cache()


def _set_allowlist(monkeypatch, allow="", allow_private="true"):
    monkeypatch.setattr(settings, "DEPLOYMENT_PROFILE", "air_gapped")
    monkeypatch.setattr(settings, "NETWORK_EGRESS_MODE", "allowlist")
    monkeypatch.setattr(settings, "NETWORK_EGRESS_ALLOW", allow)
    monkeypatch.setattr(settings, "NETWORK_EGRESS_ALLOW_PRIVATE", allow_private)


def _set_unrestricted(monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_PROFILE", "cloud")
    monkeypatch.setattr(settings, "NETWORK_EGRESS_MODE", "unrestricted")


class _RedirectHandler(BaseHTTPRequestHandler):
    """127.0.0.1 → 302 Location 指向公网 host（redirect-egress 逃逸样本）。"""

    def do_GET(self):  # noqa: N802 — stdlib 接口
        self.send_response(302)
        self.send_header("Location", "https://api.stepfun.com/exfiltrated")
        self.end_headers()

    def log_message(self, *args):  # 静默
        pass


@pytest.fixture()
def redirect_server():
    server = HTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/redirect"
    server.shutdown()
    server.server_close()


def test_public_url_denied_before_any_io(monkeypatch):
    _set_allowlist(monkeypatch)
    session = make_safe_session()
    with pytest.raises(AirGappedEgressError) as excinfo:
        session.get("https://api.stepfun.com/v1/models")
    assert excinfo.value.host == "api.stepfun.com"


def test_redirect_to_public_host_denied(monkeypatch, redirect_server):
    """loopback 首跳放行（allow_private session，真实回环服务可达）→
    302 指向公网 → redirect 跳被守卫拦截（而非 SSRF 门）。"""
    _set_allowlist(monkeypatch)
    session = make_safe_session(allow_private=True)
    with pytest.raises(AirGappedEgressError) as excinfo:
        session.get(redirect_server)
    assert excinfo.value.host == "api.stepfun.com"


def test_unrestricted_keeps_legacy_behavior(monkeypatch, redirect_server):
    """cloud 默认：无 egress 拦截——redirect 请求失败于 DNS/连接层，
    而不是 AirGappedEgressError（守卫不改变既有失败形态）。"""
    _set_unrestricted(monkeypatch)
    session = make_safe_session()
    try:
        session.get(redirect_server)
    except AirGappedEgressError:
        pytest.fail("unrestricted mode must not raise AirGappedEgressError")
    except Exception:
        pass  # 真实网络失败（无外网/SSL）= 预期行为
