"""aiohttp 接缝的 egress 守卫接线测试（ADR-0197）。

覆盖：
- allowlist 模式下共享池/新建 session 的请求在连接前被 typed 拒绝；
- unrestricted（cloud 默认）不装 trace——零开销零行为变化；
- 调用方自带 trace_configs 时正确合并。
"""
import aiohttp
import pytest

from app.core import network as network_mod
from app.core.config import settings
from app.core.egress import AirGappedEgressError, reset_policy_cache


@pytest.fixture(autouse=True)
def _clean_policy_cache():
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


@pytest.mark.asyncio
async def test_shared_client_denies_public_host_before_connect(monkeypatch):
    _set_allowlist(monkeypatch)
    session = await network_mod.get_shared_client()
    try:
        with pytest.raises(AirGappedEgressError) as excinfo:
            await session.get("https://api.stepfun.com/v1/models")
        assert excinfo.value.host == "api.stepfun.com"
    finally:
        await network_mod.close_shared_client()


@pytest.mark.asyncio
async def test_shared_client_allows_loopback_target(monkeypatch):
    """私网放行路径：真实回环服务 200 往返（正向证明，不依赖死端口失败形态）。"""
    from tests.data.egress_fixtures import loopback_http_server

    _set_allowlist(monkeypatch)
    with loopback_http_server() as base:
        session = await network_mod.get_shared_client()
        try:
            resp = await session.get(f"{base}/ok")
            assert resp.status == 200
            resp.release()
        finally:
            await network_mod.close_shared_client()


@pytest.mark.asyncio
async def test_unrestricted_mode_installs_no_trace(monkeypatch):
    from tests.data.egress_fixtures import loopback_http_server

    _set_unrestricted(monkeypatch)
    with loopback_http_server() as base:
        session = await network_mod.get_shared_client()
        try:
            # cloud 默认：session 不携带守卫 trace（零行为变化 Oracle），
            # 请求路径与 master 一致（本机透明代理会让"公网必失败"不可断言，
            # 故只做结构断言 + 回环往返）。
            assert session.trace_configs == []
            resp = await session.get(f"{base}/ok")
            assert resp.status == 200
            resp.release()
        finally:
            await network_mod.close_shared_client()


@pytest.mark.asyncio
async def test_create_client_session_merges_caller_trace_configs(monkeypatch):
    _set_allowlist(monkeypatch)

    caller_trace = aiohttp.TraceConfig()
    seen = []

    async def _marker(session, ctx, params):
        seen.append(str(params.url))

    caller_trace.on_request_start.append(_marker)
    session = await network_mod.create_client_session(trace_configs=[caller_trace])
    try:
        # 守卫在前：公网目标在调用方 trace 之前即被拒绝（请求中止）。
        with pytest.raises(AirGappedEgressError):
            await session.get("https://example.com/denied")
        assert seen == []
        # 调用方 trace 未被覆盖：守卫 + 调用方共 2 个 trace 都已挂载。
        assert len(getattr(session, "_trace_configs", [])) == 2
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_create_client_session_guard_active_by_default(monkeypatch):
    _set_allowlist(monkeypatch)
    session = await network_mod.create_client_session()
    try:
        with pytest.raises(AirGappedEgressError):
            await session.get("https://example.com/denied")
    finally:
        await session.close()
